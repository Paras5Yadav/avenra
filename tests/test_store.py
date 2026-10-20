import sqlite3
import tempfile
import time
import unittest

from avenra.app import App
from avenra.errors import DomainError
from avenra.store import Store
from avenra.util import money, now


class StoreTests(unittest.TestCase):
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
