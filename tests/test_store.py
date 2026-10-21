import sqlite3
import tempfile
import time
import threading
import unittest
from pathlib import Path

from avenra.app import App
from avenra.errors import DomainError
from avenra.store import Store
from avenra.util import money, now


class StoreTests(unittest.TestCase):
    def enqueue_test(self, store, key='test-operation'):
        with store.transaction() as db:
            db.execute('INSERT OR IGNORE INTO tenants VALUES(?,?,?)', ('t', 'Test', now()))
            db.execute("INSERT OR IGNORE INTO environments(id,tenant_id,name,purpose,ledger_name,status,created_at) VALUES('e','t','Test','interactive','avn_test','provisioning',?)", (now(),))
            store.enqueue(db, 't', 'e', 'test', 'original-operation', {'original': True}, key)

    def test_progress_and_expired_leases_preserve_the_error_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            self.enqueue_test(store)
            for _ in range(20):
                job = store.claim('progress')
                self.assertEqual(job['error_attempts'], 0)
                self.assertTrue(store.defer(job, 0))
            stale = store.claim('expired', lease_seconds=-1)
            current = store.claim('replacement')
            self.assertEqual(current['attempts'], 22)
            self.assertFalse(store.finish(stale, 'StaleError'))
            self.assertFalse(store.defer(stale, 0))
            # Persisted current values, not a caller's cached counters, govern
            # the error budget and bounded backoff.
            current['attempts'] = 1_000_000
            current['error_attempts'] = 5
            before = time.time()
            self.assertTrue(store.finish(current, 'EngineError'))
            after = time.time()
            self.assertFalse(store.finish(current, 'DuplicateError'))
            with store.reader() as db:
                row = dict(db.execute('SELECT * FROM outbox').fetchone())
            self.assertEqual((row['state'], row['attempts'], row['error_attempts'], row['last_error']), ('ready', 22, 1, 'EngineError'))
            self.assertGreaterEqual(row['available_at'], before + .5)
            self.assertLessEqual(row['available_at'], after + .5)
            reopened = Store(directory)
            with reopened.reader() as db:
                self.assertEqual(db.execute('SELECT error_attempts FROM outbox').fetchone()[0], 1)

    def test_six_actual_errors_exhaust_after_productive_deferrals(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            self.enqueue_test(store)
            for number in range(1, 7):
                for _ in range(7):
                    job = store.claim('progress')
                    self.assertTrue(store.defer(job, 0))
                job = store.claim('error')
                self.assertTrue(store.finish(job, 'EngineError'))
                with store.reader() as db:
                    row = dict(db.execute('SELECT * FROM outbox').fetchone())
                self.assertEqual(row['error_attempts'], number)
                self.assertEqual(row['state'], 'failed' if number == 6 else 'ready')
                with store.transaction() as db:
                    db.execute('UPDATE outbox SET available_at=0')
            self.assertEqual(row['attempts'], 48)
            self.assertIsNone(store.claim('exhausted'))
            self.enqueue_test(store, 'explicit-original-operation-reconciliation')
            new = store.claim('explicit-reconciliation')
            self.assertNotEqual(new['id'], row['id'])
            self.assertEqual((new['aggregate_id'], new['payload_json']), (row['aggregate_id'], row['payload_json']))
            self.assertEqual((new['attempts'], new['error_attempts']), (1, 0))
            self.assertTrue(store.finish(new))

    def test_legacy_error_history_is_not_recounted_or_revived(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'avenra.db'
            source = Path(__file__).resolve().parents[1] / 'avenra'
            with sqlite3.connect(path) as db:
                db.executescript((source / 'schema.sql').read_text())
                for migration in sorted((source / 'migrations').glob('*.sql')):
                    db.executescript(migration.read_text())
                db.execute('PRAGMA user_version=9')
                db.execute("INSERT INTO tenants VALUES('t','Test',?)", (now(),))
                db.execute("INSERT INTO environments(id,tenant_id,name,purpose,ledger_name,status,created_at) VALUES('e','t','Test','interactive','avn_test','active',?)", (now(),))
                for identity, state, attempts, error in [('progress', 'ready', 40, None), ('retry', 'ready', 4, 'EngineError'), ('failed', 'failed', 7, 'EngineError')]:
                    db.execute('INSERT INTO outbox(id,tenant_id,environment_id,kind,aggregate_id,payload_json,dedupe_key,state,attempts,available_at,last_error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                               (identity, 't', 'e', 'test', 'original-operation', '{}', identity, state, attempts, 0, error, now()))
                columns = [row[1] for row in db.execute('PRAGMA table_info(outbox)')]
                before = db.execute('SELECT * FROM outbox ORDER BY id').fetchall()
            store = Store(directory)
            with store.reader() as db:
                self.assertEqual([tuple(row) for row in db.execute('SELECT ' + ','.join(columns) + ' FROM outbox ORDER BY id')], before)
                errors = {row['id']: row['error_attempts'] for row in db.execute('SELECT * FROM outbox')}
            self.assertEqual(errors, {'progress': 0, 'retry': None, 'failed': None})
            progress = store.claim('progress')
            self.assertEqual(progress['id'], 'progress')
            self.assertTrue(store.finish(progress, 'FirstActualError'))
            with store.reader() as db:
                self.assertEqual(db.execute("SELECT state,error_attempts FROM outbox WHERE id='progress'").fetchone()[1], 1)
            retry = store.claim('legacy')
            self.assertEqual((retry['id'], retry['attempts'], retry['error_attempts']), ('retry', 5, None))
            self.assertTrue(store.finish(retry, 'LegacyError'))
            with store.transaction() as db:
                db.execute("UPDATE outbox SET available_at=-1 WHERE id='retry'")
            retry = store.claim('legacy')
            self.assertTrue(store.finish(retry, 'LegacyError'))
            with store.reader() as db:
                self.assertEqual(db.execute("SELECT state FROM outbox WHERE id='retry'").fetchone()[0], 'failed')
                self.assertEqual(db.execute("SELECT attempts FROM outbox WHERE id='failed'").fetchone()[0], 7)
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 10)
            with self.assertRaises(sqlite3.IntegrityError), store.transaction() as db:
                db.execute('UPDATE outbox_retry_schema SET version=2')

    def test_environment_failure_uses_fenced_errors_not_lifetime_claims(self):
        for used, acknowledged, expected in [(0, True, 'provisioning'), (5, True, 'failed'), (5, False, 'provisioning')]:
            with self.subTest(errors=used, acknowledged=acknowledged), tempfile.TemporaryDirectory() as directory:
                store = Store(directory)
                self.enqueue_test(store)
                with store.transaction() as db:
                    db.execute("UPDATE outbox SET kind='environment',aggregate_id='e',attempts=50,error_attempts=?", (used,))
                app = App.__new__(App)
                app.store, app.worker_owner, app.stop_event = store, 'worker-test', threading.Event()
                def fail(*args):
                    raise RuntimeError('Synthetic provisioning failure')
                app.provision = fail
                original_finish = store.finish
                def finish(job, error=None):
                    app.stop_event.set()
                    return original_finish(job, error) if acknowledged else False
                store.finish = finish
                with self.assertLogs(level='ERROR'):
                    app._claim_and_work()
                with store.reader() as db:
                    self.assertEqual(db.execute("SELECT status FROM environments WHERE id='e'").fetchone()[0], expected)

    def test_expired_worker_cannot_acknowledge_new_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            with store.transaction() as db:
                db.execute('INSERT INTO tenants VALUES(?,?,?)', ('t', 'Test', now()))
                db.execute("INSERT INTO environments(id,tenant_id,name,purpose,ledger_name,status,created_at) VALUES('e','t','Test','interactive','avn_test','provisioning',?)", (now(),))
                store.enqueue(db, 't', 'e', 'test', 'operation', {}, 'test-operation')
            stale = store.claim('old', lease_seconds=-1)
            current = store.claim('new')
            self.assertNotEqual(stale['lease_token'], current['lease_token'])
            self.assertFalse(store.finish(stale))
            self.assertTrue(store.finish(current))
            self.assertIsNone(store.claim('third'))

    def test_audit_chain_detects_change_and_database_blocks_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            with store.transaction() as db:
                db.execute('INSERT INTO tenants VALUES(?,?,?)', ('t', 'Test', now()))
                store.audit(db, 't', None, 'first', 'one', {'amount': 100})
                store.audit(db, 't', None, 'second', 'two', {'amount': 200})
            self.assertEqual(store.verify_audit()['checked'], 2)
            self.assertTrue(store.verify_audit()['valid'])
            with self.assertRaises(sqlite3.IntegrityError):
                with store.transaction() as db:
                    db.execute("UPDATE audit_events SET payload_json='{}' WHERE sequence=1")
            with store.transaction() as db:
                db.execute('DROP TRIGGER immutable_audit_update')
                db.execute("UPDATE audit_events SET payload_json='{}' WHERE sequence=1")
            self.assertFalse(store.verify_audit()['valid'])

    def test_financial_input_rejects_boolean_float_and_nonpositive_amounts(self):
        for value in (True, 1.5, '100', 0, -1, 100_000_001):
            with self.subTest(value=value), self.assertRaises(DomainError):
                money(value)
        self.assertEqual(money(1), 1)


if __name__ == '__main__':
    unittest.main()
