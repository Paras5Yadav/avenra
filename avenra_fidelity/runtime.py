"""Additive runtime: reuse financial engines without editing the installed core."""
import hashlib
import hmac
import json
from pathlib import Path
import threading
import time

from avenra.errors import DomainError, require
from avenra.upi import Upi
from avenra.util import canonical, digest, now, uid
from .profile import PROFILE, PROFILE_HASH, REQUIREMENTS, EXTERNAL, fingerprint, case_pack_hash
from .credentials import Credentials
from .recurring import Recurring
from .backoffice import BackOffice
from avenra.private_config import deployment


TABLES = ['fidelity_' + name for name in ['schema', 'profiles', 'challenges', 'credentials', 'guards', 'mandates',
          'occurrences', 'notifications', 'complaints', 'events', 'proposals', 'cycles', 'cycle_items', 'reconciliations', 'acceptance']]


class GuardedUpi(Upi):
    def authorize(self, tenant, env, identity, data):
        if data.get('approved') is True:
            with self.store.reader() as db:
                payment = self.get(db, 'upi_payments', tenant, env, identity)
                if self.app.fidelity.applies(tenant, env, payment, db):
                    guard = db.execute('SELECT * FROM fidelity_guards WHERE payment_id=? AND tenant_id=? AND environment_id=?', (identity, tenant, env)).fetchone()
                    require(guard, 'CREDENTIAL_REQUIRED', 'Authorize this lifecycle intent with its synthetic credential or approved mandate.', 403)
        return super().authorize(tenant, env, identity, data)

    def _handle(self, tenant, env, payment, message, token):
        if message['step'] == 2:
            with self.store.transaction() as db:
                leg = db.execute("SELECT state FROM bank_legs WHERE operation_id=? AND kind='debit'", (payment['operation_id'],)).fetchone()
                if leg and leg['state'] == 'pending' and self.app.fidelity.applies(tenant, env, payment, db):
                    reason = self.app.fidelity.guard_error(db, tenant, env, payment)
                    if reason:
                        db.execute("UPDATE bank_legs SET state='declined',reason=?,updated_at=? WHERE operation_id=? AND kind='debit'", (reason, now(), payment['operation_id']))
                        self._event(db, payment, message['id'], 'lifecycle.authorization_invalid_before_debit', {'code': reason})
        return super()._handle(tenant, env, payment, message, token)


class Lifecycle(Credentials, Recurring, BackOffice):
    def __init__(self, app):
        self.app, self.store, self.upi, self.bank = app, app.store, app.upi, app.banking
        with self.store.transaction() as db:
            exists = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fidelity_schema'").fetchone() if not hasattr(self.store, 'pool') else db.execute("SELECT to_regclass('fidelity_schema')").fetchone()[0]
            if not exists:
                require(deployment(self.store.directory).get('database', {}).get('mode') != 'runtime', 'MIGRATION_REQUIRED', 'Install the lifecycle schema using the migration identity before serving.', 503)
                source = Path(__file__).with_name('schema.sql').read_text().replace(' REAL', ' DOUBLE PRECISION') if hasattr(self.store, 'pool') else Path(__file__).with_name('schema.sql').read_text()
                for statement in source.split(';'):
                    if statement.strip(): db.execute(statement.strip())
                for table in ['fidelity_profiles', 'fidelity_guards', 'fidelity_events', 'fidelity_notifications', 'fidelity_cycles', 'fidelity_cycle_items', 'fidelity_reconciliations', 'fidelity_acceptance']:
                    for action in ['UPDATE', 'DELETE']:
                        if hasattr(self.store, 'pool'):
                            db.execute(f'CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record()')
                        else:
                            db.execute(f"CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Lifecycle evidence is immutable'); END")
            version = db.execute('SELECT version FROM fidelity_schema').fetchone()[0]
            if version == 1:
                require(deployment(self.store.directory).get('database', {}).get('mode') != 'runtime', 'MIGRATION_REQUIRED', 'Lifecycle upgrade requires the migration identity.', 503)
                # The first deployed extension used millisecond wall time. Recover
                # its exact transaction boundary from the retained audit identity.
                profiles = [dict(x) for x in db.execute('SELECT * FROM fidelity_profiles')]
                db.execute('DROP TRIGGER fidelity_profiles_update' + (' ON fidelity_profiles' if hasattr(self.store, 'pool') else ''))
                db.execute('ALTER TABLE fidelity_profiles ADD COLUMN activation_sequence INTEGER NOT NULL DEFAULT 0')
                for profile in profiles:
                    event = db.execute("SELECT sequence FROM audit_events WHERE tenant_id=? AND environment_id=? AND kind='fidelity.profile.enabled' ORDER BY sequence LIMIT 1", (profile['tenant_id'], profile['environment_id'])).fetchone()
                    require(event, 'ACTIVATION_AUDIT_REQUIRED', 'Preserve the original profile activation audit event before migration.', 409)
                    db.execute('UPDATE fidelity_profiles SET activation_sequence=? WHERE environment_id=?', (event['sequence'], profile['environment_id']))
                if hasattr(self.store, 'pool'):
                    db.execute('CREATE TRIGGER fidelity_profiles_update BEFORE UPDATE ON fidelity_profiles FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record()')
                else:
                    db.execute("CREATE TRIGGER fidelity_profiles_update BEFORE UPDATE ON fidelity_profiles BEGIN SELECT RAISE(ABORT,'Lifecycle evidence is immutable'); END")
                db.execute('UPDATE fidelity_schema SET version=2')
                version = 2
            require(version == 2, 'SCHEMA', 'Unsupported lifecycle extension schema.', 503)

    def get(self, db, table, tenant, env, identity, column='id'):
        return self.bank._get(db, table, tenant, env, identity, column)

    def time(self, tenant, env, db=None):
        return self.upi.clock(tenant, env, db)

    def enabled(self, tenant, env, db):
        row = db.execute('SELECT * FROM fidelity_profiles WHERE tenant_id=? AND environment_id=?', (tenant, env)).fetchone()
        require(not row or row['profile_hash'] == PROFILE_HASH, 'PROFILE_UNSUPPORTED', 'Retain the original lifecycle profile implementation.', 409)
        return row

    def applies(self, tenant, env, payment, db):
        profile = self.enabled(tenant, env, db)
        if not profile: return False
        event = db.execute("SELECT details_json FROM upi_events WHERE payment_id=? AND event='intent.accepted'", (payment['id'],)).fetchone()
        require(event, 'ACTIVATION_AUDIT_REQUIRED', 'Original intent acceptance evidence is required.', 409)
        return json.loads(event['details_json'])['audit_sequence'] > profile['activation_sequence']

    def enable(self, tenant, env, data):
        from avenra.upi import fields
        fields(data, [])
        self.app.environment(tenant, env, True)
        with self.store.transaction() as db:
            if not self.enabled(tenant, env, db):
                self.event(db, tenant, env, env, 'profile.enabled', 'operator', {'profile_hash': PROFILE_HASH})
                sequence = db.execute('SELECT MAX(sequence) FROM audit_events').fetchone()[0]
                db.execute('INSERT INTO fidelity_profiles VALUES(?,?,?,?,?,?,?)', (env, tenant, PROFILE_HASH, canonical(PROFILE), self.time(tenant, env, db), now(), sequence))
        return self.overview(tenant, env)

    def require_enabled(self, tenant, env, db):
        require(db.execute('SELECT 1 FROM environments WHERE id=? AND tenant_id=?', (env, tenant)).fetchone(), 'NOT_FOUND', 'Environment not found.', 404)
        require(self.enabled(tenant, env, db), 'PROFILE_REQUIRED', 'Enable the synthetic lifecycle profile for this environment.', 409)

    def sign(self, document):
        hashed = digest(document)
        return hashed, hmac.new(self.store.signing_key, hashed.encode(), hashlib.sha256).hexdigest()

    def event(self, db, tenant, env, resource, kind, actor, details):
        self.store.audit(db, tenant, env, 'fidelity.' + kind, resource, {'principal': actor, **details})
        sequence = db.execute('SELECT MAX(sequence) FROM audit_events').fetchone()[0]
        db.execute('INSERT INTO fidelity_events VALUES(?,?,?,?,?,?,?,?,?)', ('fevent_' + str(sequence).zfill(20), tenant, env, resource, kind, actor, canonical(details), self.time(tenant, env, db), now()))

    def overview(self, tenant, env):
        self.app.environment(tenant, env)
        with self.store.reader() as db:
            enabled = bool(self.enabled(tenant, env, db))
            values = {name: [dict(x) for x in db.execute('SELECT * FROM fidelity_' + name + ' WHERE tenant_id=? AND environment_id=? ORDER BY created_at DESC,id DESC LIMIT 100', (tenant, env))] for name in ['mandates', 'occurrences', 'complaints', 'proposals', 'cycles', 'notifications']}
            credentials = [self.public_credential(dict(x)) for x in db.execute('SELECT * FROM fidelity_credentials WHERE tenant_id=? AND environment_id=?', (tenant, env))]
            pending = db.execute("SELECT count(*) FROM upi_payments WHERE tenant_id=? AND environment_id=? AND state='unknown'", (tenant, env)).fetchone()[0]
        for c in values['cycles']:
            c['manifest'] = json.loads(c.pop('manifest_json'))
        return {'profile': PROFILE, 'profile_hash': PROFILE_HASH, 'enabled': enabled, 'business_time': self.time(tenant, env), 'credentials': credentials,
                'coverage': self.coverage(tenant, env), 'unknown_payments': pending, 'official_conformance': False, 'production_ready': False, **values}

    def coverage(self, tenant, env):
        self.app.environment(tenant, env)
        with self.store.reader() as db:
            reports = db.execute('SELECT * FROM fidelity_acceptance WHERE tenant_id=? AND environment_id=? ORDER BY created_at DESC,id DESC LIMIT 20', (tenant, env)).fetchall()
        current = fingerprint(); matching = None
        for report in reports:
            manifest = json.loads(report['manifest_json'])
            if manifest.get('fingerprint') == current and manifest.get('case_pack_sha256') == case_pack_hash():
                hashed, signature = self.sign(manifest)
                if hashed == report['manifest_hash'] and hmac.compare_digest(signature, report['signature']):
                    matching = manifest; break
        rows = []
        for identity, rule in REQUIREMENTS:
            checks = [c for c in (matching or {}).get('checks', []) if identity in c.get('requirements', [])]
            state = 'passed' if checks and all(c['passed'] for c in checks) else 'failed' if checks else 'not_run'
            rows.append({'id': identity, 'rule': rule, 'authority': 'synthetic', 'state': state, 'cases': len(checks)})
        return {'fingerprint': current, 'requirements': rows, 'external': EXTERNAL, 'report_id': (matching or {}).get('id'), 'scope': PROFILE['scope']}

    def record_acceptance(self, tenant, env, checks):
        # Trusted local verifier only: no public write API can turn a flag green.
        identity = uid('qualification')
        manifest = {'id': identity, 'fingerprint': fingerprint(), 'case_pack_sha256': case_pack_hash(), 'profile_hash': PROFILE_HASH, 'tenant_id': tenant, 'environment_id': env, 'checks': checks, 'at': now(), 'official_conformance': False}
        hashed, signature = self.sign(manifest)
        with self.store.transaction() as db:
            self.require_enabled(tenant, env, db)
            db.execute('INSERT INTO fidelity_acceptance VALUES(?,?,?,?,?,?,?)', (identity, tenant, env, canonical(manifest), hashed, signature, now()))
        return manifest


    def start_scheduler(self):
        self.lifecycle_worker = None
        def work():
            import logging
            while not self.app.stop_event.wait(.4):
                try: self.tick()
                except Exception: logging.exception('Synthetic lifecycle scheduler failed; durable records retained')
        self.lifecycle_worker = threading.Thread(target=work, name='avenra-lifecycle-worker', daemon=True)
        self.lifecycle_worker.start()

    def stop_scheduler(self):
        worker = getattr(self, 'lifecycle_worker', None)
        if worker: worker.join(timeout=15)
