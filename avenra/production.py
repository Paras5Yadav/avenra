"""Shared deployment authorization, admission, observability and worker health.

The existing local operator mode remains available. Hardened deployments and
explicit environment policies enforce granular capability and resource rules.
"""
import hashlib
import hmac
import json
import logging
import math
from pathlib import Path
import re
import threading
import time

from .errors import DomainError, require
from .private_config import deployment
from .util import canonical, digest, now, reference, uid

CAPABILITIES = frozenset({
    'workspace.read', 'environment.manage', 'fixtures.manage', 'banking.simulate', 'payments.create',
    'payments.authorize', 'payments.recover', 'payments.adjust', 'participants.manage',
    'connections.manage', 'credentials.manage', 'mandates.manage', 'cases.manage',
    'cases.propose', 'approvals.approve', 'settlement.manage', 'risk.manage',
    'consent.manage', 'scenarios.manage', 'integrations.manage', 'runtime.observe', 'runtime.admin',
})
ROLE_CAPABILITIES = {
    'observer': ['workspace.read', 'runtime.observe'],
    'operator': sorted(CAPABILITIES - {'runtime.admin', 'approvals.approve'}),
    'reviewer': ['workspace.read', 'runtime.observe', 'approvals.approve'],
    'administrator': sorted(CAPABILITIES),
}
POLICY_DEFAULT = {'enforce_capabilities': True, 'rate_per_minute': 600,
                  'burst': 60, 'max_inflight': 16, 'max_pending_jobs': 2000,
                  'request_lease_seconds': 180}
TABLES = ['runtime_schema', 'runtime_policies', 'runtime_policy_bindings', 'runtime_grants',
          'runtime_sessions', 'runtime_buckets', 'runtime_requests', 'runtime_workers']
NEW_WORK = {'environment.manage', 'fixtures.manage', 'banking.simulate', 'payments.create', 'scenarios.manage', 'mandates.manage'}


def capabilities(value):
    require(isinstance(value, list) and len(value) <= len(CAPABILITIES) and all(isinstance(x, str) and x in CAPABILITIES for x in value), 'CAPABILITIES', 'Use declared Avenra capabilities.', 403)
    return sorted(set(value))


def capability(method, tail):
    if method == 'GET': return 'runtime.observe' if tail.startswith('banking/runtime') else 'workspace.read'
    if tail.startswith('banking/runtime'): return 'runtime.admin'
    if tail == 'environment': return 'environment.manage'
    if tail == 'payments' or tail.startswith('payments/') or tail.startswith('banking/operations') or tail.startswith('banking/mandates'):
        return 'banking.simulate'
    if re.search(r'/proposals/[A-Za-z0-9_]+/approve$', tail): return 'approvals.approve'
    if re.search(r'/(?:upi|fidelity)/payments/[A-Za-z0-9_]+/authorize$', tail): return 'payments.authorize'
    if re.search(r'/(?:reconcile|status)$', tail) and '/cycles/' not in tail: return 'payments.recover'
    if re.search(r'/(?:refund|reversal)$', tail): return 'payments.adjust'
    prefix = tail.removeprefix('banking/').split('/')
    if prefix[0] == 'aa': return 'consent.manage'
    if prefix[0] in {'upi', 'fidelity'}:
        resource = prefix[1] if len(prefix) > 1 else ''
        return {'participants': 'participants.manage', 'connections': 'connections.manage',
                'devices': 'credentials.manage', 'links': 'credentials.manage', 'discover': 'workspace.read',
                'challenges': 'credentials.manage', 'balance': 'workspace.read', 'qr': 'workspace.read',
                'payments': 'payments.create', 'mandates': 'mandates.manage', 'occurrences': 'mandates.manage',
                'complaints': 'cases.propose' if tail.endswith('/propose') else 'cases.manage',
                'cycles': 'settlement.manage', 'profile': 'environment.manage', 'messages': 'participants.manage'}.get(resource)
    return {'banks': 'fixtures.manage', 'customers': 'fixtures.manage', 'accounts': 'fixtures.manage',
            'operations': 'payments.create', 'payments': 'payments.create', 'resolve': 'workspace.read',
            'mandates': 'mandates.manage', 'disputes': 'cases.manage', 'cycles': 'settlement.manage',
            'consents': 'consent.manage', 'rules': 'risk.manage', 'analysis': 'risk.manage',
            'cases': 'scenarios.manage', 'test-runs': 'scenarios.manage', 'runs': 'scenarios.manage',
            'simulation': 'scenarios.manage', 'callbacks': 'integrations.manage',
            'core-commands': 'integrations.manage', 'contracts': 'integrations.manage'}.get(prefix[0])


class Runtime:
    def __init__(self, app):
        self.app, self.store = app, app.store
        self.node_id = app.worker_owner
        self.roles, self.thread = set(), None
        self.started_at = time.time()
        self._health_lock, self._health_cache = threading.Lock(), None
        with self.store.transaction() as db:
            exists = db.execute("SELECT to_regclass('runtime_schema')").fetchone()[0] if hasattr(self.store, 'pool') else db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_schema'").fetchone()
            if not exists:
                require(self.configuration().get('database', {}).get('mode') != 'runtime', 'MIGRATION_REQUIRED', 'Install the runtime schema using the migration identity before serving.', 503)
                source = Path(__file__).with_name('production_schema.sql').read_text()
                if hasattr(self.store, 'pool'): source = source.replace(' REAL', ' DOUBLE PRECISION')
                for statement in source.split(';'):
                    if statement.strip(): db.execute(statement.strip())
                for action in ['UPDATE', 'DELETE']:
                    if hasattr(self.store, 'pool'):
                        db.execute(f'CREATE TRIGGER runtime_policies_{action.lower()} BEFORE {action} ON runtime_policies FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record()')
                    else:
                        db.execute(f"CREATE TRIGGER runtime_policies_{action.lower()} BEFORE {action} ON runtime_policies BEGIN SELECT RAISE(ABORT,'Runtime policies are immutable'); END")
            require(db.execute('SELECT version FROM runtime_schema').fetchone()[0] == 1, 'MIGRATION_REQUIRED', 'Runtime schema requires its matching deployment version.', 503)
        from .operations import Operations
        self.operations = Operations(self)

    def configuration(self): return deployment(self.store.directory)

    def clock(self, db):
        return float(db.execute('SELECT EXTRACT(EPOCH FROM clock_timestamp()) AS epoch').fetchone()[0]) if hasattr(self.store, 'pool') else time.time()

    def signed(self, value):
        hashed = digest(value)
        return hashed, hmac.new(self.store.signing_key, hashed.encode(), hashlib.sha256).hexdigest()

    def context(self, principal, permissions, **extra):
        return {'principal': principal, 'approval_principal': principal, 'permissions': permissions, 'capabilities': [], **extra}

    def session_context(self, token_hash, tenant, permissions):
        with self.store.reader() as db:
            stored = db.execute('SELECT * FROM runtime_sessions WHERE token_hash=?', (token_hash,)).fetchone()
        if not stored:
            policy = self.configuration().get('identity', {})
            require(not (policy.get('granular') or policy.get('require_session_subject')), 'SSO_REAUTHENTICATION', 'Sign in again to bind the session to its institutional subject.', 401)
            return self.context('operator-session:' + token_hash, permissions, operator=tenant is None)
        require(not stored['revoked_at'] and time.time() < stored['expires_at'], 'SESSION_REVOKED', 'Institutional session is expired or revoked.', 401)
        configured = self.app.identity.configuration()
        expected_issuer = hashlib.sha256(configured.get('issuer', '').encode()).hexdigest()
        require(expected_issuer == stored['issuer_hash'], 'SESSION_ISSUER', 'Session belongs to a different institutional authority.', 401)
        return self.context(stored['principal'], permissions, approval_principal=stored['approval_principal'], capabilities=json.loads(stored['capabilities_json']), expires_at=stored['expires_at'], operator=False)

    def remember_session(self, db, token_hash, identity, expiry):
        db.execute('INSERT INTO runtime_sessions VALUES(?,?,?,?,?,?,?,?,NULL)',
                   (token_hash, identity['tenant_id'], identity['principal'], 'subject:' + identity['subject_hash'], canonical(identity.get('capabilities', [])), identity['issuer_hash'], identity['authenticated_at'], expiry))

    def effective_capabilities(self, db, tenant, env, context):
        if context.get('operator'): return set(CAPABILITIES)
        granted = set(context.get('capabilities', []))
        grant = db.execute('SELECT * FROM runtime_grants WHERE tenant_id=? AND principal=?', (tenant, context['principal'])).fetchone()
        if grant:
            # A revoked/expired explicit grant cannot fall back to broader token claims.
            require(not grant['revoked_at'] and self.clock(db) < grant['expires_at'], 'GRANT_REVOKED', 'The institutional subject grant is expired or revoked.', 403)
            environments = json.loads(grant['environments_json'])
            require(not environments or env in environments, 'ENVIRONMENT_PERMISSION', 'Principal is not granted this environment.', 403)
            granted = set(json.loads(grant['capabilities_json']))
        return granted

    def filter_catalog(self, value, tenant, context):
        if context.get('operator'): return value
        with self.store.reader() as db:
            grant = db.execute('SELECT * FROM runtime_grants WHERE tenant_id=? AND principal=?', (tenant, context['principal'])).fetchone()
            if grant:
                require(not grant['revoked_at'] and self.clock(db) < grant['expires_at'], 'GRANT_REVOKED', 'The institutional subject grant is expired or revoked.', 403)
                allowed = json.loads(grant['environments_json'])
                if allowed: value['environments'] = [e for e in value['environments'] if e['id'] in allowed]
                granted = set(json.loads(grant['capabilities_json']))
            else: granted = set(context.get('capabilities', []))
        if grant or self.configuration().get('identity', {}).get('granular'):
            require('workspace.read' in granted or 'runtime.admin' in granted, 'CAPABILITY_REQUIRED', 'Workspace discovery requires read permission.', 403)
        return value

    def policy(self, db, tenant, env):
        row = db.execute('SELECT p.* FROM runtime_policies p JOIN runtime_policy_bindings b ON b.policy_id=p.id WHERE b.tenant_id=? AND b.environment_id=?', (tenant, env)).fetchone()
        if row:
            document = json.loads(row['document_json']);hashed, signature = self.signed(document)
            require(row['document_hash'] == hashed and hmac.compare_digest(signature, row['signature']), 'POLICY_INTEGRITY', 'Active resource policy failed verification.', 503)
            return document
        return None

    def guard(self, handler, tenant, env, tail):
        try: return self._guard(handler,tenant,env,tail)
        except DomainError as error:
            if error.code in {'TENANT_CAPACITY','REQUEST_RATE','QUEUE_CAPACITY'}:
                try: self.operations.rejection(tenant,env,error.code)
                except Exception: logging.exception('Admission refusal observation could not be retained')
            raise

    def _guard(self, handler, tenant, env, tail):
        context = handler.identity_context
        needed = capability(handler.command, tail)
        with self.store.transaction() as db:
            policy = self.policy(db, tenant, env) if env else None
            strict = bool(self.configuration().get('identity', {}).get('granular') or (policy or {}).get('enforce_capabilities') or tail.startswith('banking/runtime'))
            if strict:
                granted = self.effective_capabilities(db, tenant, env, context)
                require(needed and (needed in granted or 'runtime.admin' in granted), 'CAPABILITY_REQUIRED', 'This principal is not granted the requested action.', 403)
            monitor = self.operations.bound(db,tenant,env) if env and not policy else None
            if not policy and not monitor: return None
            timestamp = self.clock(db)
            if not policy:
                identity = uid('request')
                db.execute('INSERT INTO runtime_requests(id,tenant_id,environment_id,principal,capability,started_at,expires_at) VALUES(?,?,?,?,?,?,?)', (identity,tenant,env,context['principal'],needed or 'legacy',timestamp,timestamp+180))
                return identity
            active = db.execute('SELECT count(*) FROM runtime_requests WHERE tenant_id=? AND environment_id=? AND completed_at IS NULL AND expires_at>?', (tenant, env, timestamp)).fetchone()[0]
            require(active < policy['max_inflight'], 'TENANT_CAPACITY', 'Environment has reached its active request budget; retry the original identity.', 429)
            bucket = db.execute('SELECT * FROM runtime_buckets WHERE tenant_id=? AND environment_id=?', (tenant, env)).fetchone()
            tokens = min(policy['burst'], bucket['tokens'] + max(0, timestamp - bucket['updated_at']) * policy['rate_per_minute'] / 60) if bucket else float(policy['burst'])
            require(tokens >= 1, 'REQUEST_RATE', 'Environment request budget is replenishing; retry later.', 429)
            if handler.command == 'POST' and needed in NEW_WORK:
                pending = db.execute("SELECT count(*) FROM outbox WHERE tenant_id=? AND environment_id=? AND state IN ('ready','leased')", (tenant, env)).fetchone()[0]
                require(pending < policy['max_pending_jobs'], 'QUEUE_CAPACITY', 'Accepted work is draining; new work must wait. Original recovery remains available.', 503)
            if bucket:
                db.execute('UPDATE runtime_buckets SET tokens=?,updated_at=? WHERE tenant_id=? AND environment_id=?', (tokens - 1, timestamp, tenant, env))
            else:
                db.execute('INSERT INTO runtime_buckets VALUES(?,?,?,?)', (tenant, env, tokens - 1, timestamp))
            identity = uid('request')
            db.execute('INSERT INTO runtime_requests(id,tenant_id,environment_id,principal,capability,started_at,expires_at) VALUES(?,?,?,?,?,?,?)', (identity, tenant, env, context['principal'], needed or 'legacy', timestamp, timestamp + policy['request_lease_seconds']))
            return identity

    def complete(self, identity, status):
        if not identity: return
        with self.store.transaction() as db:
            timestamp = self.clock(db)
            db.execute('UPDATE runtime_requests SET completed_at=?,domain_status=?,status=CASE WHEN deadline_exceeded=1 THEN 504 ELSE ? END WHERE id=? AND completed_at IS NULL', (timestamp, status, status, identity))

    def deadline(self, identity):
        if not identity: return
        with self.store.transaction() as db:
            db.execute('UPDATE runtime_requests SET status=504,deadline_exceeded=1 WHERE id=?', (identity,))

    def publish_policy(self, tenant, env, data, actor):
        require(isinstance(data, dict) and set(data) == set(POLICY_DEFAULT) | {'request_key'}, 'INVALID_POLICY', 'Publish the complete bounded runtime policy.')
        key = reference(data['request_key']);document = {k: data[k] for k in POLICY_DEFAULT}
        require(type(document['enforce_capabilities']) is bool, 'INVALID_POLICY', 'Capability enforcement must be boolean.')
        for field, minimum, maximum in [('rate_per_minute', 1, 60000), ('burst', 1, 1000), ('max_inflight', 1, 128), ('max_pending_jobs', 1, 100000), ('request_lease_seconds', 120, 600)]:
            require(type(document[field]) is int and minimum <= document[field] <= maximum, 'INVALID_POLICY', 'Policy value exceeds the declared bounds.')
        hashed, signature = self.signed(document)
        with self.store.transaction() as db:
            require(db.execute('SELECT 1 FROM environments WHERE id=? AND tenant_id=?', (env, tenant)).fetchone(), 'NOT_FOUND', 'Environment not found.', 404)
            old = db.execute('SELECT * FROM runtime_policies WHERE tenant_id=? AND environment_id=? AND request_key=?', (tenant, env, key)).fetchone()
            if old:
                require(old['request_hash'] == hashed, 'IDEMPOTENCY_CONFLICT', 'Policy request identity has different content.', 409)
                return dict(old)
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM runtime_policies WHERE tenant_id=? AND environment_id=?', (tenant, env)).fetchone()[0]
            identity = uid('policy')
            db.execute('INSERT INTO runtime_policies VALUES(?,?,?,?,?,?,?,?,?,?)', (identity, tenant, env, version, canonical(document), hashed, signature, key, hashed, now()))
            current = db.execute('SELECT 1 FROM runtime_policy_bindings WHERE tenant_id=? AND environment_id=?', (tenant, env)).fetchone()
            if current: db.execute('UPDATE runtime_policy_bindings SET policy_id=?,updated_at=? WHERE tenant_id=? AND environment_id=?', (identity, now(), tenant, env))
            else: db.execute('INSERT INTO runtime_policy_bindings VALUES(?,?,?,?)', (tenant, env, identity, now()))
            self.store.audit(db, tenant, env, 'runtime.policy_published', identity, {'principal': actor, 'document_hash': hashed, 'version': version})
            return dict(db.execute('SELECT * FROM runtime_policies WHERE id=?', (identity,)).fetchone())

    def grant(self, tenant, env, data, actor):
        require(isinstance(data, dict) and set(data) == {'principal', 'capabilities', 'environment_ids', 'expires_at'}, 'INVALID_GRANT', 'Use a principal, capabilities, environment list and expiry.')
        require(isinstance(data['principal'], str) and re.fullmatch(r'(?:service|oidc|operator-session):[0-9a-f]{64}', data['principal']), 'INVALID_GRANT', 'Use an authenticated public principal identifier.')
        allowed = capabilities(data['capabilities']);environments = data['environment_ids']
        require(isinstance(environments, list) and len(environments) <= 1000 and all(isinstance(x, str) for x in environments), 'INVALID_GRANT', 'Use a bounded environment list.')
        expiry = data['expires_at']
        require(type(expiry) in {int, float} and math.isfinite(expiry) and time.time() < expiry <= time.time() + 86400 * 366, 'INVALID_EXPIRY', 'Grant expiry must be within the next year.')
        with self.store.transaction() as db:
            for environment in environments:
                require(db.execute('SELECT 1 FROM environments WHERE id=? AND tenant_id=?', (environment, tenant)).fetchone(), 'NOT_FOUND', 'Grant environment not found in this workspace.', 404)
            old = db.execute('SELECT * FROM runtime_grants WHERE tenant_id=? AND principal=?', (tenant, data['principal'])).fetchone()
            identity = old['id'] if old else uid('grant')
            if old: db.execute('UPDATE runtime_grants SET capabilities_json=?,environments_json=?,expires_at=?,revoked_at=NULL WHERE id=?', (canonical(allowed), canonical(environments), expiry, identity))
            else: db.execute('INSERT INTO runtime_grants VALUES(?,?,?,?,?,?,NULL,?)', (identity, tenant, data['principal'], canonical(allowed), canonical(environments), expiry, now()))
            self.store.audit(db, tenant, env, 'runtime.grant_set', identity, {'principal': data['principal'], 'actor': actor, 'capabilities': allowed, 'environment_ids': environments, 'expires_at': expiry})
            return dict(db.execute('SELECT * FROM runtime_grants WHERE id=?', (identity,)).fetchone())

    def revoke(self, tenant, env, identity, actor):
        with self.store.transaction() as db:
            grant = db.execute('SELECT * FROM runtime_grants WHERE id=? AND tenant_id=?', (identity, tenant)).fetchone()
            require(grant, 'NOT_FOUND', 'Principal grant not found.', 404)
            db.execute('UPDATE runtime_grants SET revoked_at=? WHERE id=?', (now(), identity))
            self.store.audit(db, tenant, env, 'runtime.grant_revoked', identity, {'principal': actor})
        return {'id': identity, 'revoked': True}

    def heartbeat(self, role):
        self.roles.add(role)
        def beat():
            with self.store.transaction() as db:
                timestamp = self.clock(db)
                roles=set(self.roles)
                if 'worker' in roles and (not self.app.worker or not self.app.worker.is_alive() or not self.app.worker_available):roles.discard('worker')
                if db.execute('SELECT 1 FROM runtime_workers WHERE id=?', (self.node_id,)).fetchone():
                    db.execute('UPDATE runtime_workers SET role=?,last_seen=? WHERE id=?', (','.join(sorted(roles)), timestamp, self.node_id))
                else: db.execute('INSERT INTO runtime_workers VALUES(?,?,?,?,?)', (self.node_id, ','.join(sorted(roles)), self.app.source_fingerprint, timestamp, self.started_at))
        beat()
        self.operations.start()
        if self.thread: return
        def work():
            import logging
            while not self.app.stop_event.wait(2):
                try: beat()
                except Exception: logging.exception('Runtime heartbeat failed; readiness must fail stale workers')
        self.thread = threading.Thread(target=work, name='avenra-runtime-heartbeat', daemon=True);self.thread.start()

    def stop(self):
        if self.thread: self.thread.join(timeout=10)
        self.operations.stop()

    def readiness(self, include_operations=True):
        with self._health_lock:
            if self._health_cache and self._health_cache[2]==include_operations and time.monotonic() - self._health_cache[0] < 1: return self._health_cache[1]
            checks = []
            try:
                with self.store.reader() as db:
                    timestamp = self.clock(db)
                    checks.append({'component': 'control', 'ready': db.execute('SELECT 1').fetchone()[0] == 1})
                    worker = db.execute('SELECT 1 FROM runtime_workers WHERE last_seen>? AND role LIKE ? AND source_fingerprint=? LIMIT 1', (timestamp - 6, '%worker%', self.app.source_fingerprint)).fetchone()
                    checks.append({'component': 'worker', 'ready': bool(worker)})
            except Exception:
                checks.append({'component':'control','ready':False})
                checks.append({'component':'worker','ready':False,'reason':'control_unavailable'})
            try:
                self.app.accounting.health();checks.append({'component':'accounting','ready':True})
            except Exception:checks.append({'component':'accounting','ready':False})
            if include_operations:
                try:
                    monitor=self.operations.health();checks.append({'component':'operations','ready':monitor['ready']})
                except Exception:checks.append({'component':'operations','ready':False})
            result = {'ready': all(x['ready'] for x in checks), 'checks': checks, 'source_fingerprint': self.app.source_fingerprint}
            self._health_cache = time.monotonic(), result, include_operations
            return result

    def overview(self, tenant, env):
        self.app.environment(tenant, env)
        with self.store.reader() as db:
            policy = self.policy(db, tenant, env);timestamp = self.clock(db)
            rows = [dict(x) for x in db.execute('SELECT * FROM runtime_requests WHERE tenant_id=? AND environment_id=? AND started_at>? ORDER BY started_at DESC LIMIT 5000', (tenant, env, timestamp - 3600))]
            totals = db.execute('SELECT count(*) total,COALESCE(SUM(deadline_exceeded),0) deadlines FROM runtime_requests WHERE tenant_id=? AND environment_id=? AND started_at>?', (tenant, env, timestamp - 3600)).fetchone()
            active = db.execute('SELECT count(*) FROM runtime_requests WHERE tenant_id=? AND environment_id=? AND completed_at IS NULL AND expires_at>?', (tenant, env, timestamp)).fetchone()[0]
            grants = [dict(x) for x in db.execute('SELECT id,principal,capabilities_json,environments_json,expires_at,revoked_at,created_at FROM runtime_grants WHERE tenant_id=? ORDER BY created_at DESC LIMIT 100', (tenant,))]
            workers = [dict(x) for x in db.execute('SELECT id,role,source_fingerprint,last_seen FROM runtime_workers ORDER BY last_seen DESC LIMIT 100')]
            queue = [dict(x) for x in db.execute('SELECT kind,state,count(*) count FROM outbox WHERE tenant_id=? AND environment_id=? GROUP BY kind,state', (tenant, env))]
        latencies = sorted((r['completed_at'] - r['started_at']) * 1000 for r in rows if r['completed_at'] is not None)
        config = self.configuration()
        return {'policy': policy, 'capabilities': sorted(CAPABILITIES), 'roles': ROLE_CAPABILITIES, 'grants': grants, 'workers': workers, 'queue': queue,
                'deployment': {'identity_mode': self.app.identity.configuration()['mode'], 'granular_identity': config.get('identity', {}).get('granular', False), 'database_access': config.get('database', {}).get('mode', 'migration'),
                               'api_tls': bool(config.get('http', {}).get('tls_identity')), 'api_client_certificates': config.get('http', {}).get('require_client_certificate', False),
                               'accounting_tls': bool(config.get('transport', {}).get('accounting')), 'role': config.get('role', 'combined')},
                'metrics': {'requests_last_hour': totals['total'], 'active': active, 'deadline_exceeded': totals['deadlines'], 'latency_sample_size': len(latencies),
                            'latency_p95_ms': latencies[min(len(latencies)-1, int(len(latencies)*.95))] if latencies else None},
                'readiness': self.readiness(), 'operations':self.operations.overview(tenant,env), 'scope': 'Observed runtime policy, request and worker state. No independent assessment or bank conformance follows.'}
