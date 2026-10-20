import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import time

from .errors import DomainError, require
from .gateways import AccountingGateway, EngineError, FaultGateway, execution_status
from .store import Store
from .util import application_fingerprint, canonical, digest, label, money, now, reference, row, uid

PROFILE = 'avenra-inr-lab/1.0'
FIXTURE = [('payer', 'Ananya Rao', 'ananya@avenra', 5_000_000),
           ('payee', 'Kite Coffee', 'kitecoffee@avenra', 250_000),
           ('treasury', 'Avenra Treasury', 'treasury@avenra', 100_000_000)]
SCENARIOS = [
    {'key': 'payment_success', 'name': 'Successful payment', 'group': 'Payments', 'description': 'Post a transfer and verify both authoritative account balances.'},
    {'key': 'idempotency', 'name': 'Replay & payload conflict', 'group': 'Correctness', 'description': 'Replay one request, reject changed amounts and prove one posting.'},
    {'key': 'insufficient_funds', 'name': 'Insufficient funds', 'group': 'Payments', 'description': 'Verify the engine rejects an overdraft without changing balances.'},
    {'key': 'lost_response', 'name': 'Commit, lose response, recover', 'group': 'Resilience', 'description': 'Drop real response bytes after commit, then reconcile the original intent.'},
    {'key': 'duplicate_callback', 'name': 'Duplicate notification', 'group': 'Correctness', 'description': 'Deliver a local notification three times; accept it once.'},
    {'key': 'concurrent_replay', 'name': 'Concurrent request replay', 'group': 'Concurrency', 'description': 'Submit eight concurrent copies and verify one financial operation.'}
]
from .poc_scenarios import CATALOG as POC_SCENARIOS,coverage as poc_coverage
SCENARIOS += POC_SCENARIOS


class App:
    def __init__(self, directory, accounting_url='http://127.0.0.1:4830', fault_url='http://127.0.0.1:4831', control_dsn=None):
        from .recovery_state import require_active, engine_bindings
        require_active(directory)
        accounting_url, fault_url = engine_bindings(directory, accounting_url, fault_url)
        self.source_fingerprint = application_fingerprint()
        if control_dsn:
            from .postgres_store import PostgresStore
            self.store = PostgresStore(directory, control_dsn)
        else:
            self.store = Store(directory)
        self.accounting = AccountingGateway(accounting_url)
        self.fault = FaultGateway(fault_url, accounting_url.removeprefix('http://'))
        self.stop_event = threading.Event()
        self.worker = None
        self.worker_available = False
        self.worker_lock = threading.Lock()
        self.worker_owner = uid('worker')
        from .banking import Banking
        self.banking = Banking(self)
        from .services import Services
        from .callbacks import Callbacks
        self.adapters = Services(self)
        self.callbacks = Callbacks(self)
        from .monitoring import Monitoring
        self.monitoring = Monitoring(self)
        from .identity import Identity
        self.identity = Identity(self)
        from .simulation import Simulation
        self.simulation = Simulation(self)
        from avenra_fidelity.runtime import GuardedUpi as Upi, Lifecycle
        self.upi = Upi(self)
        try:
            self.fidelity = Lifecycle(self)
            from .production import Runtime
            self.runtime = Runtime(self)
            from .engine_transport import configure
            configure(self)
            from .aa.manager import Aggregator
            self.aa = Aggregator(self)
        except BaseException:
            if hasattr(self.store, 'close'): self.store.close()
            raise

    def bootstrap(self):
        with self.store.transaction() as db:
            if not db.execute('SELECT 1 FROM tenants LIMIT 1').fetchone():
                for tenant_id, name in [('tenant_avenra', 'Avenra Lab'), ('tenant_integration', 'Integration Lab')]:
                    db.execute('INSERT INTO tenants VALUES(?,?,?)', (tenant_id, name, now()))
                    self.store.audit(db, tenant_id, None, 'tenant.created', tenant_id, {'name': name})
        with self.store.reader() as db:
            tenants = [dict(t) for t in db.execute('SELECT * FROM tenants ORDER BY created_at,id')]
        for tenant in tenants:
            with self.store.reader() as db:
                exists = db.execute("SELECT 1 FROM environments WHERE tenant_id=? AND purpose='interactive'", (tenant['id'],)).fetchone()
            if not exists:
                self.create_environment(tenant['id'], 'UPI development')
        credentials = self.store.directory / 'credentials.json'
        if not credentials.exists():
            token = self.create_api_key('tenant_avenra', 'Local development')
            fd = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as handle:
                json.dump({'tenant_id': 'tenant_avenra', 'api_key': token}, handle)

    def create_api_key(self, tenant_id, name, permissions=None, expires_at=None):
        permissions = permissions if permissions is not None else ['read', 'write']
        require(isinstance(permissions, list) and permissions and all(isinstance(p, str) and p in {'read', 'write'} for p in permissions), 'INVALID_PERMISSIONS', 'Choose read and/or write.')
        require(expires_at is None or (type(expires_at) in {float, int} and time.time() < expires_at <= time.time() + 86400 * 366), 'INVALID_EXPIRY', 'Key expiry must be within one year.')
        token = 'avn_' + secrets.token_urlsafe(32)
        with self.store.transaction() as db:
            require(db.execute('SELECT 1 FROM tenants WHERE id=?', (tenant_id,)).fetchone(), 'NOT_FOUND', 'Workspace not found.', 404)
            db.execute('INSERT INTO api_keys(token_hash,tenant_id,name,created_at,expires_at,permissions_json) VALUES(?,?,?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), tenant_id, label(name), now(), expires_at, canonical(permissions)))
        return token

    def environment(self, tenant, environment, active=False):
        with self.store.reader() as db:
            value = row(db.execute('SELECT * FROM environments WHERE id=? AND tenant_id=?', (environment, tenant)).fetchone())
        require(value, 'NOT_FOUND', 'Environment not found.', 404)
        if active:
            require(value['status'] == 'active', 'ENVIRONMENT_NOT_READY', 'Environment is still provisioning or needs recovery.', 409)
        return value

    def _new_environment(self, db, tenant, name, purpose, fixture_profile='legacy'):
        require(isinstance(fixture_profile, str) and fixture_profile in {'legacy', 'banking'}, 'INVALID_PROFILE', 'Select a supported fixture profile.')
        environment = uid('env')
        db.execute('INSERT INTO environments(id,tenant_id,name,purpose,ledger_name,status,created_at,fixture_profile) VALUES(?,?,?,?,?,?,?,?)',
                   (environment, tenant, name, purpose, 'avn_' + environment.removeprefix('env_'), 'provisioning', now(), fixture_profile))
        for role, account_name, vpa, _ in FIXTURE if fixture_profile == 'legacy' else []:
            db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?)',
                       (uid('acc'), tenant, environment, account_name, vpa, 'accounts:' + role, role, now()))
        self.store.audit(db, tenant, environment, 'environment.requested', environment, {'name': name, 'profile': PROFILE})
        return environment

    def create_environment(self, tenant, name, fixture_profile='legacy'):
        with self.store.transaction() as db:
            require(db.execute('SELECT 1 FROM tenants WHERE id=?', (tenant,)).fetchone(), 'NOT_FOUND', 'Workspace not found.', 404)
            environment = self._new_environment(db, tenant, label(name), 'interactive', fixture_profile)
            self.store.enqueue(db, tenant, environment, 'environment', environment, {}, 'env:' + environment)
        return self.environment(tenant, environment)

    def provision(self, tenant, environment):
        env = self.environment(tenant, environment)
        if env['status'] == 'active':
            return
        self.accounting.create(env['ledger_name'], environment)
        postings = [{'source': 'world', 'destination': 'accounts:' + role, 'asset': 'INR/2', 'amount': amount}
                    for role, _, _, amount in FIXTURE]
        if env['fixture_profile'] == 'legacy':
            self.accounting.post(env['ledger_name'], {'postings': postings, 'reference': 'fixture:' + environment,
                                 'metadata': {'profile': PROFILE, 'environment_id': environment}}, 'fixture:' + environment)
        with self.store.transaction() as db:
            current = db.execute('SELECT status FROM environments WHERE id=?', (environment,)).fetchone()[0]
            if current != 'active':
                db.execute("UPDATE environments SET status='active',error=NULL WHERE id=?", (environment,))
                self.store.audit(db, tenant, environment, 'environment.ready', environment, {'fixture': PROFILE})

    def accounts(self, tenant, environment):
        env = self.environment(tenant, environment, True)
        volumes = {a['address']: a.get('volumes', {}).get('INR/2', {}).get('balance', 0)
                   for a in self.accounting.accounts(env['ledger_name'])}
        with self.store.reader() as db:
            result = [dict(a) for a in db.execute('SELECT * FROM accounts WHERE tenant_id=? AND environment_id=? ORDER BY role', (tenant, environment))]
        for account in result:
            account['balance_paise'] = volumes.get(account['address'], 0)
            account['currency'] = 'INR'
        return result

    def payment(self, tenant, environment, payment_id):
        self.environment(tenant, environment)
        with self.store.reader() as db:
            payment = row(db.execute('SELECT * FROM payments WHERE id=? AND tenant_id=? AND environment_id=?', (payment_id, tenant, environment)).fetchone())
        require(payment, 'NOT_FOUND', 'Payment not found.', 404)
        return payment

    def submit_payment(self, tenant, environment, data):
        self.environment(tenant, environment, True)
        amount, key = money(data.get('amount_paise')), reference(data.get('request_key'))
        fault = data.get('fault', 'none')
        require(fault in {'none', 'lost_response'}, 'INVALID_FAULT', 'Choose a supported local fault.')
        payer, payee = data.get('payer_id'), data.get('payee_id')
        require(isinstance(payer, str) and isinstance(payee, str) and payer != payee, 'INVALID_ACCOUNT', 'Select different payer and beneficiary accounts.')
        fingerprint = digest({'payer_id': payer, 'payee_id': payee, 'amount_paise': amount, 'currency': 'INR', 'fault': fault})
        with self.store.transaction() as db:
            existing = db.execute('SELECT * FROM payments WHERE tenant_id=? AND environment_id=? AND request_key=?', (tenant, environment, key)).fetchone()
            if existing:
                require(hmac.compare_digest(existing['request_hash'], fingerprint), 'IDEMPOTENCY_CONFLICT', 'This request key already has a different financial payload.', 409)
                return {**dict(existing), 'replayed': True}
            for account_id in [payer, payee]:
                require(db.execute('SELECT 1 FROM accounts WHERE id=? AND tenant_id=? AND environment_id=?', (account_id, tenant, environment)).fetchone(), 'NOT_FOUND', 'Account not found in this environment.', 404)
            payment_id, timestamp = uid('pay'), now()
            db.execute('INSERT INTO payments(id,tenant_id,environment_id,payer_id,payee_id,amount_paise,request_key,request_hash,state,fault,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (payment_id, tenant, environment, payer, payee, amount, key, fingerprint, 'pending', fault, timestamp, timestamp))
            self.store.enqueue(db, tenant, environment, 'payment', payment_id, {}, 'pay:' + payment_id)
            self.store.audit(db, tenant, environment, 'payment.accepted', payment_id, {'amount_paise': amount, 'request_hash': fingerprint, 'fault': fault})
        return {**self.payment(tenant, environment, payment_id), 'replayed': False}

    def process_payment(self, tenant, environment, payment_id):
        payment = self.payment(tenant, environment, payment_id)
        if payment['state'] in {'succeeded', 'declined'}:
            return payment
        env = self.environment(tenant, environment, True)
        with self.store.reader() as db:
            addresses = {a['id']: a['address'] for a in db.execute('SELECT id,address FROM accounts WHERE environment_id=? AND tenant_id=?', (environment, tenant))}
        body = {'postings': [{'source': addresses[payment['payer_id']], 'destination': addresses[payment['payee_id']],
                             'asset': 'INR/2', 'amount': payment['amount_paise']}], 'reference': 'payment:' + payment_id,
                'metadata': {'payment_id': payment_id, 'environment_id': environment, 'profile': PROFILE}}
        state, reason, result = 'unknown', None, None
        apply_fault = payment['fault'] == 'lost_response' and not payment['fault_applied']
        if apply_fault:
            with self.store.transaction() as db:
                changed = db.execute('UPDATE payments SET fault_applied=1 WHERE id=? AND fault_applied=0', (payment_id,)).rowcount
                apply_fault = changed == 1
        try:
            if apply_fault:
                with self.fault.lost_response(payment_id) as origin:
                    result = self.accounting.post(env['ledger_name'], body, 'payment:' + payment_id, origin)
            else:
                result = self.accounting.post(env['ledger_name'], body, 'payment:' + payment_id)
            state = 'succeeded'
        except EngineError as error:
            if error.code == 'INSUFFICIENT_FUND':
                state, reason = 'declined', 'Insufficient simulated funds.'
            else:
                reason = 'Response unconfirmed. Reconcile this intent using its original identity.'
        with self.store.transaction() as db:
            current = db.execute('SELECT state FROM payments WHERE id=?', (payment_id,)).fetchone()[0]
            if current not in {'succeeded', 'declined'}:
                db.execute('UPDATE payments SET state=?,reason=?,ledger_transaction_id=?,ledger_result_json=?,updated_at=? WHERE id=?',
                           (state, reason, str(result['id']) if result else None, canonical(result) if result else None, now(), payment_id))
                self.store.audit(db, tenant, environment, 'payment.' + state, payment_id,
                                 {'ledger_transaction_id': str(result['id']) if result else None, 'fault_applied': apply_fault})
                if state in {'succeeded', 'declined'}:
                    event_id = uid('evt')
                    payload = {'event_id': event_id, 'payment_id': payment_id, 'state': state}
                    db.execute('INSERT OR IGNORE INTO callback_events VALUES(?,?,?,?,?)', (event_id, payment_id, canonical(payload), digest(payload), now()))
                    self.store.enqueue(db, tenant, environment, 'callback', event_id, {}, 'callback:' + payment_id)
        return self.payment(tenant, environment, payment_id)

    def reconcile(self, tenant, environment, payment_id):
        payment = self.payment(tenant, environment, payment_id)
        if payment['state'] not in {'succeeded', 'declined'}:
            with self.store.transaction() as db:
                self.store.enqueue(db, tenant, environment, 'payment', payment_id, {}, 'reconcile:' + uid('attempt'))
                self.store.audit(db, tenant, environment, 'payment.reconciliation_requested', payment_id, {})
        return payment

    def deliver_callback(self, tenant, environment, event_id):
        with self.store.transaction() as db:
            event = db.execute('SELECT c.* FROM callback_events c JOIN payments p ON p.id=c.payment_id WHERE c.id=? AND p.tenant_id=? AND p.environment_id=?', (event_id, tenant, environment)).fetchone()
            require(event, 'NOT_FOUND', 'Notification not found.', 404)
            existing = db.execute('SELECT * FROM callback_inbox WHERE event_id=?', (event_id,)).fetchone()
            if existing:
                require(hmac.compare_digest(existing['payload_hash'], event['payload_hash']), 'CALLBACK_CONFLICT', 'Notification identity has conflicting content.', 409)
                status = 'duplicate'
            else:
                db.execute('INSERT INTO callback_inbox VALUES(?,?,?)', (event_id, event['payload_hash'], now()))
                status = 'accepted'
            db.execute('INSERT INTO callback_attempts(event_id,result,created_at) VALUES(?,?,?)', (event_id, status, now()))
            self.store.audit(db, tenant, environment, 'notification.' + status, event_id, {'payment_id': event['payment_id'], 'sink': 'local'})
        return status

    def create_run(self, tenant, environment, data):
        self.environment(tenant, environment, True)
        scenario = next((s for s in SCENARIOS if s['key'] == data.get('scenario_key')), None)
        require(scenario, 'INVALID_SCENARIO', 'Select a supported scenario.')
        key = reference(data.get('request_key'))
        fingerprint = digest({'source_environment_id': environment, 'scenario_key': scenario['key'], 'profile': PROFILE})
        with self.store.transaction() as db:
            existing = db.execute('SELECT * FROM runs WHERE tenant_id=? AND request_key=?', (tenant, key)).fetchone()
            if existing:
                require(existing['request_hash'] == fingerprint, 'IDEMPOTENCY_CONFLICT', 'Run key has a different profile or scenario.', 409)
                return dict(existing)
            run_id = uid('run')
            run_env = self._new_environment(db, tenant, scenario['name'] + ' fixture', 'run')
            db.execute('INSERT INTO runs(id,tenant_id,environment_id,source_environment_id,name,scenario_key,profile_version,request_key,request_hash,seed,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (run_id, tenant, run_env, environment, scenario['name'], scenario['key'], PROFILE, key, fingerprint, 'fixed-inr-fixture-v1', 'queued', now()))
            self.store.enqueue(db, tenant, run_env, 'run', run_id, {}, 'run:' + run_id)
            self.store.audit(db, tenant, run_env, 'run.requested', run_id, {'scenario': scenario['key'], 'profile': PROFILE})
        return self.run(tenant, run_id)

    def run(self, tenant, run_id):
        with self.store.reader() as db:
            run = row(db.execute('SELECT * FROM runs WHERE id=? AND tenant_id=?', (run_id, tenant)).fetchone())
            require(run, 'NOT_FOUND', 'Run not found.', 404)
            run['steps'] = [dict(s) for s in db.execute('SELECT * FROM run_steps WHERE run_id=? ORDER BY created_at,id', (run_id,))]
            evidence = db.execute('SELECT * FROM evidence WHERE run_id=?', (run_id,)).fetchone()
            run['evidence'] = self.verify_evidence(dict(evidence)) if evidence else None
        return run

    def verify_evidence(self, evidence):
        manifest = json.loads(evidence['manifest_json'])
        calculated = digest(manifest)
        signature = hmac.new(self.store.signing_key, calculated.encode(), hashlib.sha256).hexdigest()
        return {'manifest': manifest, 'manifest_hash': evidence['manifest_hash'], 'signature': evidence['signature'],
                'valid': hmac.compare_digest(calculated, evidence['manifest_hash']) and hmac.compare_digest(signature, evidence['signature']),
                'verification_scope': 'Local integrity under this installation key; not independent certification.'}

    def overview(self, tenant, environment):
        env = self.environment(tenant, environment)
        accounts, transactions, unavailable = [], [], None
        if env['status'] == 'active':
            try:
                accounts = self.accounts(tenant, environment)
                transactions = self.accounting.transactions(env['ledger_name'])
            except EngineError:
                unavailable = 'Accounting engine is unavailable. Balances cannot be confirmed.'
        with self.store.reader() as db:
            payments = [dict(p) for p in db.execute('SELECT p.*,a.name payer_name,b.name payee_name FROM payments p JOIN accounts a ON a.id=p.payer_id JOIN accounts b ON b.id=p.payee_id WHERE p.tenant_id=? AND p.environment_id=? ORDER BY p.created_at DESC LIMIT 100', (tenant, environment))]
            runs = [dict(r) for r in db.execute('SELECT * FROM runs WHERE tenant_id=? AND source_environment_id=? ORDER BY created_at DESC LIMIT 100', (tenant, environment))]
            audit = [dict(a) for a in db.execute('SELECT sequence,kind,entity_id,created_at,event_hash FROM audit_events WHERE tenant_id=? AND (environment_id=? OR environment_id IN (SELECT environment_id FROM runs WHERE tenant_id=? AND source_environment_id=?)) ORDER BY sequence DESC LIMIT 30', (tenant, environment, tenant, environment))]
        return {'environment': env, 'accounts': accounts, 'payments': payments, 'transactions': transactions,
                'runs': runs, 'audit': audit, 'unavailable': unavailable,
                'poc': poc_coverage(self,tenant,environment),
                'metrics': {'settled_paise': sum(p['amount_paise'] for p in payments if p['state'] == 'succeeded'),
                            'unconfirmed': sum(p['state'] == 'unknown' for p in payments),
                            'passed_runs': sum(r['state'] == 'passed' for r in runs), 'total_runs': len(runs)}}

    def services(self):
        services = []
        root = Path(__file__).resolve().parents[1]
        for key, name, client in [('accounting', 'Accounting engine', self.accounting), ('fault', 'Network fault engine', self.fault)]:
            try:
                value = client.health()
                state, detail = 'connected', 'Local engine is responding.'
                version = value.get('version', 'local-build')
            except EngineError:
                state, detail, version = 'unavailable', 'Start the local engine to use this capability.', None
            services.append({'key': key, 'name': name, 'state': state, 'detail': detail, 'version': version,
                             'source_present': (root / 'engines' / key).is_dir()})
        services.append(execution_status())
        services.extend(self.adapters.status())
        services.append({'key': 'banking', 'name': 'Interbank simulation', 'state': 'connected', 'source_present': True,
                         'detail': 'Scoped institutions, multi-leg transfers, linked adjustments, mandates, disputes and synthetic settlement reports.'})
        services.append({'key': 'contracts', 'name': 'API contract engine', 'state': 'source_imported', 'source_present': True,
                         'detail': 'Source and test packs are available. Service qualification is pending.'})
        for key, name in [('aa', 'Institutional AA interoperability'), ('live_upi', 'Live UPI access')]:
            services.append({'key': key, 'name': name, 'state': 'not_configured', 'source_present': False,
                             'detail': 'No active adapter or conformance claim in this build.'})
        return services

    def catalog(self, allowed_tenant=None):
        with self.store.reader() as db:
            tenants = [dict(t) for t in db.execute('SELECT * FROM tenants ORDER BY created_at,id') if not allowed_tenant or t['id'] == allowed_tenant]
            environments = [dict(e) for e in db.execute("SELECT * FROM environments WHERE purpose='interactive' ORDER BY created_at") if not allowed_tenant or e['tenant_id'] == allowed_tenant]
        return {'name': 'Avenra', 'profile': PROFILE, 'mode': 'local_simulation', 'tenants': tenants,
                'environments': environments, 'scenarios': SCENARIOS, 'services': self.services()}

    def start_worker(self):
        with self.worker_lock:
            require(not self.stop_event.is_set(),'WORKER_STOPPED','A closed application needs a fresh worker process.',409)
            require(not self.worker or not self.worker.is_alive(),'WORKER_ACTIVE','This application already has an active durable worker.',409)
            self.worker_available = False
            self.worker = threading.Thread(target=self._work, name='avenra-worker', daemon=True)
            self.worker.start()
            self.runtime.heartbeat('worker')
            scheduler=getattr(self.fidelity,'lifecycle_worker',None)
            if not scheduler or not scheduler.is_alive():self.fidelity.start_scheduler()

    def _work(self):
        try:
            while not self.stop_event.is_set():
                try:self._claim_and_work()
                except Exception:
                    self.worker_available=False
                    logging.exception('Durable worker receipt/store failure; retain original job identities for fenced recovery')
                    self.stop_event.wait(.5)
        finally:self.worker_available=False

    def _claim_and_work(self):
        from .scenarios import execute
        from .banking import BankingBusy
        from . import bank_cases
        while not self.stop_event.is_set():
            job = self.store.claim(self.worker_owner, lease_seconds=60)
            self.worker_available = True
            if job is None:
                self.stop_event.wait(.15)
                continue
            try:
                tenant, env, aggregate = job['tenant_id'], job['environment_id'], job['aggregate_id']
                if job['kind'] == 'environment':
                    self.provision(tenant, env)
                elif job['kind'] == 'payment':
                    self.process_payment(tenant, env, aggregate)
                elif job['kind'] == 'callback':
                    self.deliver_callback(tenant, env, aggregate)
                elif job['kind'] == 'run':
                    execute(self, tenant, aggregate)
                elif job['kind'] == 'bank_account':
                    self.banking.provision_account(tenant, env, aggregate)
                elif job['kind'] == 'bank_operation':
                    result = self.banking.process(tenant, env, aggregate)
                    if result['state'] == 'unknown':
                        self.store.defer(job, 30)
                        continue
                elif job['kind'] == 'bank_mandate':
                    try:
                        self.banking.execute_mandate(tenant, env, aggregate)
                    except DomainError as error:
                        if error.code != 'NOT_DUE':
                            raise
                        self.store.defer(job, 2)
                        continue
                elif job['kind'] == 'bank_case':
                    bank_cases.execute(self, tenant, env, aggregate)
                elif job['kind'] == 'simulation_run':
                    self.simulation.execute(tenant, env, aggregate)
                elif job['kind'] == 'upi_payment':
                    result = self.upi.process(tenant, env, aggregate)
                    if result['pending']:
                        self.store.defer(job, .2)
                        continue
                elif job['kind'] == 'upi_expiry':
                    if self.upi.expire(tenant, env, aggregate)['pending']:
                        self.store.defer(job, 5)
                        continue
                elif job['kind'] == 'bank_callback':
                    self.callbacks.deliver(tenant, env, aggregate)
                elif job['kind'] == 'aa_delivery':
                    result = self.aa.process(tenant, env, aggregate)
                    if result['pending']:
                        self.store.defer(job, result.get('retry_after', .5))
                        continue
                elif job['kind'] == 'core_command':
                    self.adapters.process_core(tenant, env, aggregate)
                elif job['kind'] == 'bank_analysis':
                    result = self.monitoring.process(tenant, env, aggregate)
                    if self.monitoring.retryable(result):
                        self.store.defer(job, 5)
                        continue
                else:
                    raise RuntimeError('Unknown job kind.')
                self.store.finish(job)
            except BankingBusy:
                self.store.defer(job, 2)
            except Exception as error:
                logging.exception('Avenra worker operation failed: %s', job['id'])
                self.store.finish(job, type(error).__name__)
                if job['kind'] == 'bank_callback' and job['attempts'] >= 6:
                    with self.store.transaction() as db:
                        db.execute("UPDATE bank_notifications SET state='dead_letter' WHERE id=? AND state!='delivered'", (job['aggregate_id'],))
                if job['kind'] == 'environment' and job['attempts'] >= 6:
                    with self.store.transaction() as db:
                        db.execute("UPDATE environments SET status='failed',error=? WHERE id=? AND status!='active'", ('Engine provisioning failed; inspect local service logs.', job['environment_id']))

    def close(self):
        self.stop_event.set()
        self.runtime.stop()
        self.fidelity.stop_scheduler()
        if self.worker:
            self.worker.join(timeout=65)
            if self.worker.is_alive():
                raise RuntimeError('Worker did not drain; preserve its financial identities for lease recovery.')
        if hasattr(self.store, 'close'):
            self.store.close()
