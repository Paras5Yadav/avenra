"""Versioned synthetic participant contracts and durable multistep experiments.

The envelope below is an Avenra lab protocol, not an NPCI or ReBIT wire format.
Financial effects always go through the existing authoritative banking engine.
"""
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import time

from .banking import BankingBusy, FAULTS
from .errors import DomainError, require
from .util import application_fingerprint, canonical, digest, label, money, now, reference, uid


ROLES = ['payer_psp', 'network', 'remitter', 'beneficiary', 'payee_psp']
PROFILE = {
    'format': 'avenra-participant-lab/1', 'authority': 'synthetic',
    'roles': ROLES, 'maximum_message_lifetime_seconds': 3600,
    'requirements': [
        {'id': 'LAB-IDENTITY', 'rule': 'One stable identity per financial intent; changed payload is a conflict.'},
        {'id': 'LAB-MONEY', 'rule': 'Exact integer paise and independently observed journal effects.'},
        {'id': 'LAB-RECOVERY', 'rule': 'Uncertain outcomes recover under original leg identities.'},
        {'id': 'LAB-MESSAGE', 'rule': 'Scoped authenticated envelopes; expired and changed replays rejected.'},
        {'id': 'LAB-CLOCK', 'rule': 'Business time advances monotonically and idempotently.'},
        {'id': 'LAB-CONSENT', 'rule': 'Data disclosure requires an active unexpired account-scoped consent.'},
        {'id': 'LAB-MANDATE', 'rule': 'Mandate approval, occurrence identity, limit, expiry and revocation govern execution.'},
        {'id': 'LAB-SETTLEMENT', 'rule': 'Separate observations determine reconciliation exceptions.'},
        {'id': 'LAB-DISPUTE', 'rule': 'Case transitions retain the original financial operation.'},
    ],
    'scope': 'Synthetic lab contract; official UPI/AA conformance and bank acceptance are unqualified.',
}
OPERATIONS = {
    'upi.enrol': ({'account', 'verified'}, set()),
    'upi.prepare': ({'payer', 'payee', 'amount_paise', 'device'}, {'mode', 'financial_fault', 'delivery_fault', 'expires_in_seconds', 'qr'}),
    'upi.authorize': ({'payment', 'device', 'approved'}, set()),
    'upi.route': ({'payment'}, {'maximum_messages'}), 'upi.status': ({'payment'}, set()),
    'upi.refund': ({'payment', 'amount_paise'}, set()), 'upi.expire': ({'payment'}, set()),
    'upi.assert': ({'payment', 'state'}, set()), 'upi.qr': ({'payee', 'amount_paise'}, set()),
    'payment.transfer': ({'payer', 'payee', 'amount_paise'}, {'fault', 'process'}),
    'payment.reconcile': ({'operation'}, set()),
    'payment.refund': ({'operation', 'amount_paise'}, set()),
    'payment.reversal': ({'operation', 'amount_paise'}, set()),
    'assert.balances': ({'balances'}, set()), 'assert.operation': ({'operation', 'state'}, set()),
    'account.state': ({'account', 'state'}, set()), 'clock.advance': ({'seconds'}, set()),
    'message.send': ({'sender', 'recipient', 'kind', 'payload'}, {'message_key', 'lifetime_seconds', 'issued_offset_seconds', 'invalid_signature'}),
    'message.replay': ({'message'}, {'changed_payload'}),
    'mandate.create': ({'payer', 'payee', 'maximum_paise', 'valid_for_seconds'}, set()),
    'mandate.approve': ({'mandate'}, set()), 'mandate.revoke': ({'mandate'}, set()),
    'mandate.schedule': ({'mandate', 'occurrence', 'amount_paise', 'due_after_seconds'}, set()),
    'mandate.dispatch': ({'execution'}, set()),
    'consent.create': ({'account', 'purpose', 'valid_for_seconds'}, set()),
    'consent.approve': ({'consent'}, set()), 'consent.revoke': ({'consent'}, set()),
    'consent.read': ({'consent'}, set()),
    'settlement.prepare': ({'name'}, set()), 'settlement.reconcile': ({'cycle', 'positions'}, set()),
    'dispute.create': ({'operation', 'reason'}, set()),
    'dispute.review': ({'dispute', 'resolution'}, set()), 'dispute.resolve': ({'dispute', 'resolution'}, set()),
    'risk.rule': ({'name', 'threshold_paise', 'action'}, set()),
}


def postgres_migration():
    text = Path(__file__).with_name('migrations').joinpath('007_simulation.sql').read_text()
    def trigger(match):
        name, operation, table, condition = match.groups()
        when = ' WHEN (OLD.evidence_json IS NOT NULL)' if condition else ''
        return f'CREATE TRIGGER {name} BEFORE {operation} ON {table} FOR EACH ROW{when} EXECUTE FUNCTION avenra_immutable_record();'
    text = re.sub(r"CREATE TRIGGER ([a-z_]+) BEFORE (UPDATE|DELETE) ON ([a-z_]+) (WHEN OLD.evidence_json IS NOT NULL )?BEGIN SELECT RAISE\(ABORT,'[^']+'\); END;", trigger, text)
    return re.sub(r'\bREAL\b', 'DOUBLE PRECISION', re.sub(r'\bINTEGER\b', 'BIGINT', text))


def integer(value, field, minimum=0, maximum=31_536_000):
    require(type(value) is int and minimum <= value <= maximum, 'INVALID_SIMULATION', field + ' must be a bounded integer.')
    return value


def alias(value):
    require(isinstance(value, str) and re.fullmatch('[a-z][a-z0-9_]{1,23}', value), 'INVALID_SIMULATION', 'Use 2–24 lowercase characters for fixture and step aliases.')
    return value


def business_time(store, tenant, env, db=None):
    if db is not None:
        record = db.execute('SELECT epoch FROM simulation_clocks WHERE tenant_id=? AND environment_id=?', (tenant, env)).fetchone()
    else:
        with store.reader() as reader:
            record = reader.execute('SELECT epoch FROM simulation_clocks WHERE tenant_id=? AND environment_id=?', (tenant, env)).fetchone()
    return float(record[0]) if record else time.time()


class Simulation:
    def __init__(self, app):
        self.app, self.store = app, app.store

    def get(self, db, table, tenant, env, identity):
        return self.app.banking._get(db, table, tenant, env, identity)

    def profile(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        key = reference(data.get('profile_key', 'avenra-participant-lab/1'))
        document = data.get('document', PROFILE)
        require(document == PROFILE, 'UNQUALIFIED_PROFILE', 'Only the reviewed synthetic lab contract can execute. Official profiles require implemented adapters and qualification.', 409)
        with self.store.transaction() as db:
            old = db.execute('SELECT * FROM simulation_profiles WHERE tenant_id=? AND environment_id=? AND profile_key=?', (tenant, env, key)).fetchone()
            if old:
                require(old['document_hash'] == digest(document), 'PROFILE_CONFLICT', 'Publish a new immutable profile key.', 409)
                return dict(old)
            identity = uid('profile')
            db.execute('INSERT INTO simulation_profiles VALUES(?,?,?,?,?,?,?)', (identity, tenant, env, key, canonical(document), digest(document), now()))
            self.store.audit(db, tenant, env, 'simulation.profile_published', identity, {'profile_key': key, 'document_hash': digest(document)})
            return self.get(db, 'simulation_profiles', tenant, env, identity)

    def overview(self, tenant, env):
        self.app.environment(tenant, env)
        with self.store.reader() as db:
            values = {key: [dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE tenant_id=? AND environment_id=? ORDER BY created_at DESC,id DESC LIMIT 100', (tenant, env))] for key, table in [('profiles', 'simulation_profiles'), ('plans', 'simulation_plans'), ('runs', 'simulation_runs'), ('messages', 'simulation_messages')]}
        for run in values['runs']:
            run['blocking_reason'] = self._blocking_reason(run)
            for field in ['lease_token', 'context_json', 'evidence_json']:
                run.pop(field, None)
        values.update(operations=sorted(OPERATIONS), default_profile=PROFILE, template=example_plan(), upi_template=example_upi_plan(),
                      official_profiles=[{'name': 'UPI participant wire profile', 'state': 'unqualified', 'missing': 'Current role-specific wire/security adapter and independent acceptance pack'},
                                         {'name': 'AA/FIP/FIU interoperability', 'state': 'unqualified', 'missing': 'Role-specific APIs, signatures, encryption and interoperability cases'},
                                         {'name': 'RBI Regulatory Sandbox', 'state': 'institutional_process', 'missing': 'Admission/oversight is separate from a software simulator'}])
        return values

    def clock(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        seconds, key = integer(data.get('seconds'), 'Seconds'), reference(data.get('request_key'))
        with self.store.transaction() as db:
            clock = db.execute('SELECT * FROM simulation_clocks WHERE tenant_id=? AND environment_id=?', (tenant, env)).fetchone()
            require(clock, 'CLOCK_UNAVAILABLE', 'A virtual clock exists only in an isolated simulation fixture.', 409)
            old = db.execute('SELECT * FROM simulation_clock_events WHERE environment_id=? AND request_key=?', (env, key)).fetchone()
            if old:
                require(old['request_hash'] == digest({'seconds': seconds}), 'IDEMPOTENCY_CONFLICT', 'Clock event key has different content.', 409)
                return dict(old)
            before, after = float(clock['epoch']), float(clock['epoch']) + seconds
            require(after < 32_503_680_000, 'INVALID_TIME', 'Virtual time exceeds the supported range.')
            db.execute('INSERT INTO simulation_clock_events VALUES(?,?,?,?,?,?)', (env, key, digest({'seconds': seconds}), before, after, now()))
            db.execute('UPDATE simulation_clocks SET epoch=?,revision=revision+1 WHERE environment_id=?', (after, env))
            self.store.audit(db, tenant, env, 'simulation.clock_advanced', env, {'before': before, 'after': after, 'request_key': key})
            return {'before_epoch': before, 'after_epoch': after}

    def _signature(self, tenant, env, envelope):
        key = hmac.new(self.store.signing_key, ('participant:' + tenant + ':' + env + ':' + envelope['sender']).encode(), hashlib.sha256).digest()
        return hmac.new(key, canonical(envelope).encode(), hashlib.sha256).hexdigest()

    def sign_message(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        with self.store.reader() as db:
            profile = self.get(db, 'simulation_profiles', tenant, env, data.get('profile_id'))
        sender, recipient = data.get('sender'), data.get('recipient')
        require(sender in ROLES and recipient in ROLES and sender != recipient, 'INVALID_PARTICIPANT', 'Choose distinct supported synthetic participants.')
        issued = data.get('issued_at', business_time(self.store, tenant, env))
        require(type(issued) in {int, float} and 0 < issued < 32_503_680_000, 'INVALID_TIME', 'Message time is invalid.')
        lifetime = integer(data.get('lifetime_seconds', 60), 'Message lifetime', 1, PROFILE['maximum_message_lifetime_seconds'])
        payload = data.get('payload')
        require(isinstance(payload, dict), 'INVALID_MESSAGE', 'A bounded object payload is required.')
        try:
            require(len(canonical(payload).encode()) <= 8192, 'INVALID_MESSAGE', 'Message payload exceeds 8 KiB.')
        except (ValueError, TypeError):
            raise DomainError('INVALID_MESSAGE', 'Use finite JSON values.')
        envelope = {'format': PROFILE['format'], 'environment_id': env, 'profile_hash': profile['document_hash'],
                    'sender': sender, 'recipient': recipient, 'message_key': reference(data.get('message_key')),
                    'kind': label(data.get('kind'), 'Message kind', 80), 'issued_at': issued,
                    'expires_at': issued + lifetime, 'payload': payload}
        return {'profile_id': profile['id'], 'envelope': envelope, 'signature': self._signature(tenant, env, envelope)}

    def receive_message(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        require(set(data) == {'profile_id', 'envelope', 'signature'}, 'INVALID_MESSAGE', 'Use the published authenticated message wrapper.')
        envelope = data.get('envelope')
        required = {'format', 'environment_id', 'profile_hash', 'sender', 'recipient', 'message_key', 'kind', 'issued_at', 'expires_at', 'payload'}
        require(isinstance(envelope, dict) and set(envelope) == required, 'INVALID_MESSAGE', 'Use the published lab envelope fields.')
        require(envelope['sender'] in ROLES and envelope['recipient'] in ROLES and envelope['sender'] != envelope['recipient'], 'INVALID_PARTICIPANT', 'Unknown synthetic participant.')
        require(envelope['environment_id'] == env, 'MESSAGE_SCOPE', 'Message belongs to a different environment.', 403)
        reference(envelope['message_key']); label(envelope['kind'], 'Message kind', 80)
        require(isinstance(envelope['payload'], dict), 'INVALID_MESSAGE', 'Payload must be an object.')
        require(all(type(envelope[k]) in {int, float} and 0 < envelope[k] < 32_503_680_000 for k in ['issued_at', 'expires_at']), 'INVALID_TIME', 'Message times must be finite positive timestamps.')
        require(0 < envelope['expires_at'] - envelope['issued_at'] <= PROFILE['maximum_message_lifetime_seconds'], 'INVALID_TIME', 'Message lifetime exceeds this lab profile.')
        try:
            require(len(canonical(envelope).encode()) <= 10_240, 'INVALID_MESSAGE', 'Envelope exceeds the lab message budget.')
        except (ValueError, TypeError):
            raise DomainError('INVALID_MESSAGE', 'Use finite JSON values.')
        signature = data.get('signature')
        require(isinstance(signature, str) and re.fullmatch('[0-9a-f]{64}', signature), 'INVALID_SIGNATURE', 'Use a 64-character hexadecimal lab signature.')
        with self.store.transaction() as db:
            profile = self.get(db, 'simulation_profiles', tenant, env, data.get('profile_id'))
            virtual = business_time(self.store, tenant, env, db)
            fingerprint = digest(data)
            accepted = db.execute("SELECT * FROM simulation_messages WHERE tenant_id=? AND environment_id=? AND sender=? AND message_key=? AND state='accepted'", (tenant, env, envelope['sender'], envelope['message_key'])).fetchone()
            if accepted:
                require(accepted['envelope_hash'] == fingerprint, 'IDEMPOTENCY_CONFLICT', 'Accepted message identity has different content or authentication.', 409)
                return {**dict(accepted), 'replayed': True}
            old = db.execute('SELECT * FROM simulation_messages WHERE tenant_id=? AND environment_id=? AND sender=? AND message_key=? AND envelope_hash=? AND virtual_time=?', (tenant, env, envelope['sender'], envelope['message_key'], fingerprint, virtual)).fetchone()
            if old:
                return {**dict(old), 'replayed': True}
            reason = None
            if envelope['format'] != PROFILE['format'] or envelope['profile_hash'] != profile['document_hash']:
                reason = 'VERSION_UNSUPPORTED'
            elif not hmac.compare_digest(signature, self._signature(tenant, env, envelope)):
                reason = 'SIGNATURE_INVALID'
            elif envelope['issued_at'] > virtual:
                reason = 'MESSAGE_NOT_YET_VALID'
            elif envelope['expires_at'] <= virtual:
                reason = 'MESSAGE_EXPIRED'
            identity, state = uid('message'), 'rejected' if reason else 'accepted'
            db.execute('INSERT INTO simulation_messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (identity, tenant, env, profile['id'], envelope['sender'], envelope['recipient'], envelope['message_key'], canonical(data), fingerprint, state, reason, virtual, now()))
            self.store.audit(db, tenant, env, 'simulation.message_' + state, identity, {'sender': envelope['sender'], 'recipient': envelope['recipient'], 'reason': reason, 'envelope_hash': fingerprint})
            return dict(db.execute('SELECT * FROM simulation_messages WHERE id=?', (identity,)).fetchone())

    def publish(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        name, plan = label(data.get('name')), data.get('plan')
        require(isinstance(plan, dict) and set(plan) == {'clock_epoch', 'banks', 'accounts', 'steps'}, 'INVALID_PLAN', 'Use clock_epoch, banks, accounts and steps.')
        integer(plan['clock_epoch'], 'Initial epoch', 1, 32_000_000_000)
        banks, accounts, steps = plan['banks'], plan['accounts'], plan['steps']
        require(isinstance(banks, list) and 1 <= len(banks) <= 16 and len(set(alias(b) for b in banks)) == len(banks), 'INVALID_PLAN', 'Use unique bank aliases.')
        require(isinstance(accounts, list) and 2 <= len(accounts) <= 32, 'INVALID_PLAN', 'Declare 2–32 fixture accounts.')
        names = set()
        for account in accounts:
            require(isinstance(account, dict) and {'alias', 'bank', 'opening_paise'} <= set(account) <= {'alias', 'bank', 'opening_paise', 'kind'}, 'INVALID_PLAN', 'Account fixture fields are invalid.')
            require(isinstance(account.get('kind', 'customer'), str) and account.get('kind', 'customer') in {'customer', 'merchant'}, 'INVALID_PLAN', 'Account customer kind is invalid.')
            name_alias = alias(account['alias'])
            require(name_alias not in names and account['bank'] in banks, 'INVALID_PLAN', 'Account alias or bank is invalid.')
            names.add(name_alias); money(account['opening_paise'], allow_zero=True)
        require(isinstance(steps, list) and 1 <= len(steps) <= 64, 'INVALID_PLAN', 'Declare 1–64 reviewed steps.')
        ids = set()
        for step in steps:
            require(isinstance(step, dict) and {'id', 'operation', 'args'} <= set(step) <= {'id', 'operation', 'args', 'expect', 'expect_error'}, 'INVALID_PLAN', 'Step fields are invalid.')
            step_id = alias(step['id'])
            require(step_id not in ids and isinstance(step['operation'], str) and step['operation'] in OPERATIONS, 'INVALID_PLAN', 'Step identity or operation is invalid.')
            ids.add(step_id)
            required, optional = OPERATIONS[step['operation']]
            require(isinstance(step['args'], dict) and required <= set(step['args']) <= required | optional, 'INVALID_PLAN', 'Operation arguments do not match the bounded contract.')
            require(isinstance(step.get('expect', {}), dict), 'INVALID_PLAN', 'Expected output must be an object.')
            require(not ('expect' in step and 'expect_error' in step), 'INVALID_PLAN', 'Choose an expected result or an expected domain error.')
            if 'expect_error' in step:
                label(step['expect_error'], 'Expected domain error', 80)
        require(any(s['operation'].startswith('assert.') or 'expect' in s or 'expect_error' in s for s in steps), 'INVALID_PLAN', 'An independent expected result is required.')
        try:
            require(len(canonical(plan).encode()) <= 48_000, 'INVALID_PLAN', 'Plan exceeds the bounded profile.')
        except (TypeError, ValueError):
            raise DomainError('INVALID_PLAN', 'Use finite bounded JSON values in the plan.')
        with self.store.transaction() as db:
            profile = self.get(db, 'simulation_profiles', tenant, env, data.get('profile_id'))
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM simulation_plans WHERE tenant_id=? AND environment_id=? AND name=?', (tenant, env, name)).fetchone()[0]
            identity = uid('plan')
            db.execute('INSERT INTO simulation_plans VALUES(?,?,?,?,?,?,?,?,?)', (identity, tenant, env, profile['id'], name, version, canonical(plan), digest(plan), now()))
            self.store.audit(db, tenant, env, 'simulation.plan_published', identity, {'version': version, 'plan_hash': digest(plan), 'profile_hash': profile['document_hash']})
            return self.get(db, 'simulation_plans', tenant, env, identity)

    def create_run(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        key, plan_id = reference(data.get('request_key')), data.get('plan_id')
        fingerprint = digest({'plan_id': plan_id})
        with self.store.transaction() as db:
            old = self.app.banking._existing(db, 'simulation_runs', tenant, env, key, fingerprint)
            if old:
                return self._public_run(old)
            plan = self.get(db, 'simulation_plans', tenant, env, plan_id)
            fixture = self.app._new_environment(db, tenant, plan['name'] + ' simulation fixture', 'run', 'banking')
            identity = uid('simrun')
            db.execute('INSERT INTO simulation_runs(id,tenant_id,environment_id,plan_id,fixture_environment_id,request_key,request_hash,state,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (identity, tenant, env, plan_id, fixture, key, fingerprint, 'queued', now()))
            self.store.enqueue(db, tenant, env, 'simulation_run', identity, {}, 'simulation:' + identity)
            self.store.audit(db, tenant, env, 'simulation.run_queued', identity, {'plan_id': plan_id, 'fixture_environment_id': fixture})
            return self._public_run(self.get(db, 'simulation_runs', tenant, env, identity))

    def _public_run(self, run):
        value = {k: v for k, v in run.items() if k not in {'lease_token', 'context_json'}}
        value['blocking_reason'] = self._blocking_reason(run)
        if value.get('evidence_json'):
            evidence = json.loads(value.pop('evidence_json'))
            fingerprint = digest(evidence['manifest'])
            expected = hmac.new(self.store.signing_key, fingerprint.encode(), hashlib.sha256).hexdigest()
            evidence['valid'] = hmac.compare_digest(fingerprint, evidence['manifest_hash']) and hmac.compare_digest(expected, evidence['signature'])
            value['evidence'] = evidence
        return value

    def _blocking_reason(self, run):
        context = json.loads(run.get('context_json') or '{}')
        if not run.get('evidence_json') and context.get('application_fingerprint') not in {None, self.app.source_fingerprint}:
            return 'This interrupted experiment requires its original executor build. Publish a new run for the current build.'
        return None

    def read(self, tenant, env, identity):
        self.app.environment(tenant, env)
        with self.store.reader() as db:
            run = self.get(db, 'simulation_runs', tenant, env, identity)
            run['steps'] = [dict(r) for r in db.execute('SELECT * FROM simulation_steps WHERE run_id=? ORDER BY ordinal', (identity,))]
        return self._public_run(run)

    def _setup(self, tenant, fixture, plan, profile):
        self.app.provision(tenant, fixture)
        local_profile = self.profile(tenant, fixture, {'profile_key': profile['profile_key']})
        with self.store.transaction() as db:
            db.execute('INSERT OR IGNORE INTO simulation_clocks VALUES(?,?,?,0)', (fixture, tenant, plan['clock_epoch']))
        context = {'banks': {}, 'accounts': {}, 'customers': {}, 'results': {}, 'clock': plan['clock_epoch'], 'profile_id': local_profile['id'], 'application_fingerprint': self.app.source_fingerprint}
        for name in plan['banks']:
            context['banks'][name] = self.app.banking.bank(tenant, fixture, {'name': name, 'code': name, 'request_key': 'fixture-bank:' + name})['id']
        for account in plan['accounts']:
            name = account['alias']
            customer = self.app.banking.customer(tenant, fixture, {'name': name, 'kind': account.get('kind', 'customer'), 'request_key': 'fixture-customer:' + name})
            item = self.app.banking.account(tenant, fixture, {'bank_id': context['banks'][account['bank']], 'customer_id': customer['id'], 'vpa': name + '@' + account['bank'], 'opening_paise': account['opening_paise'], 'request_key': 'fixture-account:' + name})
            self.app.banking.provision_account(tenant, fixture, item['account_id'])
            context['accounts'][name], context['customers'][name] = item['account_id'], customer['id']
        return context

    def _perform(self, tenant, env, run_id, step, context):
        b, a, operation = self.app.banking, step['args'], step['operation']
        key = run_id + ':' + step['id']
        def account(name):
            require(isinstance(name, str) and name in context['accounts'], 'INVALID_REFERENCE', 'Account alias is not in this fixture.')
            return context['accounts'][name]
        def result(name):
            require(isinstance(name, str) and name in context['results'], 'INVALID_REFERENCE', 'Reference must name a completed earlier step.')
            return context['results'][name]
        def identity(name):
            value = result(name)
            require(isinstance(value.get('id'), str), 'INVALID_REFERENCE', 'Referenced step has no resource identity.')
            return value['id']
        if operation.startswith('upi.'):
            u = self.app.upi
            if operation == 'upi.enrol':
                require(a['verified'] is True, 'DEVICE_VERIFICATION', 'Declare a successful synthetic device verification for this test enrolment.')
                account_id = account(a['account'])
                topology = u.participants(tenant, env, {})
                with self.store.reader() as db:
                    bank_id = b._get(db, 'bank_accounts', tenant, env, account_id, 'account_id')['bank_id']
                actor = next(p for p in topology['participants'] if p['bank_id'] == bank_id and p['role'] == 'payer_psp')
                device = u.device(tenant, env, {'customer_id': context['customers'][a['account']], 'device_reference': 'fixture-' + a['account'], 'request_key': key})
                u.device_action(tenant, env, device['id'], 'activate', {'device_proof': device['enrolment_challenge'], 'synthetic_verification': True})
                linked = u.link(tenant, env, {'device_id': device['id'], 'account_id': account_id, 'psp_id': actor['id']})
                return {'id': device['id'], 'account_id': account_id, 'link_id': linked['id'], 'state': 'active'}
            if operation == 'upi.prepare':
                body = {k: a[k] for k in ['mode', 'financial_fault', 'delivery_fault', 'expires_in_seconds'] if k in a}
                if 'qr' in a:
                    body.update(mode='qr', qr_uri=result(a['qr'])['uri'])
                return u.submit(tenant, env, {**body, 'payer_id': account(a['payer']), 'payee_id': account(a['payee']), 'amount_paise': a['amount_paise'], 'device_id': identity(a['device']), 'request_key': key})
            if operation == 'upi.authorize':
                device = identity(a['device'])
                return u.authorize(tenant, env, identity(a['payment']), {'device_id': device, 'device_proof': u._challenge(device), 'approved': a['approved']})
            if operation == 'upi.qr':
                with self.store.reader() as db:
                    vpa = db.execute('SELECT vpa FROM accounts WHERE id=?', (account(a['payee']),)).fetchone()[0]
                return u.qr(tenant, env, {'payee_vpa': vpa, 'amount_paise': a['amount_paise'], 'reference': key})
            payment_id = identity(a['payment'])
            if operation == 'upi.refund':
                return u.refund(tenant, env, payment_id, {'amount_paise': a['amount_paise'], 'request_key': key})
            if operation == 'upi.status':
                u.reconcile(tenant, env, payment_id, {})
            elif operation == 'upi.expire':
                u.expire(tenant, env, payment_id)
            elif operation == 'upi.route':
                for _ in range(integer(a.get('maximum_messages', 16), 'Maximum routed messages', 1, 32)):
                    delivery = u.process(tenant, env, payment_id)
                    if not delivery['pending'] or delivery['state'] == 'unknown':
                        break
            value = u.read(tenant, env, payment_id)
            if operation == 'upi.assert':
                require(value['state'] == a['state'] and value['evidence'] and value['evidence']['valid'], 'ASSERTION_FAILED', 'Participant outcome or completed independent evidence differs from its declared expectation.', 409)
            return {'id': payment_id, 'operation_id': value['operation_id'], 'state': value['state'], 'accepted_messages': sum(bool(m['receipt_json']) for m in value['messages']), 'evidence_hash': (value['evidence'] or {}).get('manifest_hash')}
        if operation == 'clock.advance':
            return self.clock(tenant, env, {**a, 'request_key': key})
        if operation == 'account.state':
            return b.account_state(tenant, env, account(a['account']), {'state': a['state']})
        if operation == 'payment.transfer':
            require(type(a.get('process', True)) is bool and isinstance(a.get('fault', 'none'), str) and a.get('fault', 'none') in FAULTS, 'INVALID_PLAN', 'Payment fault or processing flag is invalid.')
            value = b.submit(tenant, env, {'payer_id': account(a['payer']), 'payee_id': account(a['payee']), 'amount_paise': a['amount_paise'], 'fault': a.get('fault', 'none'), 'request_key': key})
            return b.process(tenant, env, value['id']) if a.get('process', True) else value
        if operation == 'payment.reconcile':
            return b.process(tenant, env, identity(a['operation']))
        if operation in {'payment.refund', 'payment.reversal'}:
            value = b.adjust(tenant, env, identity(a['operation']), {'amount_paise': a['amount_paise'], 'request_key': key}, operation.split('.')[1])
            return b.process(tenant, env, value['id'])
        if operation == 'assert.balances':
            expected = a['balances']
            require(isinstance(expected, dict) and expected, 'INVALID_PLAN', 'Declare independently expected account balances.')
            actual = {r['id']: r['balance_paise'] for r in self.app.accounts(tenant, env)}
            observed = {name: actual.get(account(name)) for name in expected}
            for value in expected.values():
                integer(value, 'Expected balance', 0, 10**14)
            require(observed == expected, 'ASSERTION_FAILED', 'Observed journal balances differ from the independently declared amounts.', 409)
            return {'balances': observed, 'passed': True}
        if operation == 'assert.operation':
            value = b.operation(tenant, env, identity(a['operation']))
            require(value['state'] == a['state'], 'ASSERTION_FAILED', 'Operation state differs from its declared expectation.', 409)
            states = {leg['kind']: leg['state'] for leg in value['legs']}
            if value['state'] == 'succeeded':
                require(states.get('debit') == states.get('credit') == 'confirmed', 'FINANCIAL_INVARIANT', 'Successful operations require confirmed debit and credit legs.', 409)
            if value['state'] == 'reversed':
                require(states.get('debit') == states.get('reversal') == 'confirmed' and states.get('credit') == 'declined', 'FINANCIAL_INVARIANT', 'A reversed operation requires the original debit and its separate compensation.', 409)
            ledger = self.app.environment(tenant, env)['ledger_name']
            observations = []
            for leg in value['legs']:
                observed = self.app.accounting.transactions(ledger, leg['id'])
                expected = [{'source': leg['source'], 'destination': leg['destination'], 'asset': 'INR/2', 'amount': leg['amount_paise']}]
                require(len(observed) <= 1 and (not observed or observed[0]['postings'] == expected), 'FINANCIAL_INVARIANT', 'Journal effect has duplicate or changed financial content.', 409)
                require(leg['state'] != 'confirmed' or len(observed) == 1, 'FINANCIAL_INVARIANT', 'A confirmed leg has no authoritative posting.', 409)
                require(value['state'] != 'declined' or not observed, 'FINANCIAL_INVARIANT', 'A declined operation has an unexpected financial posting.', 409)
                observations.append({'kind': leg['kind'], 'state': leg['state'], 'transactions': observed})
            return {'id': value['id'], 'state': value['state'], 'observations': observations, 'passed': True}
        if operation == 'message.send':
            offset = a.get('issued_offset_seconds', 0)
            require(type(offset) is int and abs(offset) <= 31_536_000, 'INVALID_TIME', 'Use a bounded message-time offset.')
            invalid = a.get('invalid_signature', False)
            require(type(invalid) is bool, 'INVALID_PLAN', 'Invalid-signature flag must be boolean.')
            signed = self.sign_message(tenant, env, {**a, 'profile_id': context['profile_id'], 'message_key': a.get('message_key', key), 'issued_at': context['clock'] + offset})
            if invalid:
                signed['signature'] = '0' * 64
            return self.receive_message(tenant, env, signed)
        if operation == 'message.replay':
            signed = json.loads(result(a['message'])['envelope_json'])
            if 'changed_payload' in a:
                signed['envelope']['payload'] = a['changed_payload']
                signed['signature'] = self._signature(tenant, env, signed['envelope'])
            return self.receive_message(tenant, env, signed)
        if operation == 'mandate.create':
            expiry = context['clock'] + integer(a['valid_for_seconds'], 'Mandate lifetime', 1)
            return b.mandate(tenant, env, {'payer_id': account(a['payer']), 'payee_id': account(a['payee']), 'maximum_paise': a['maximum_paise'], 'starts_at': context['clock'], 'expires_at': expiry, 'request_key': key})
        if operation in {'mandate.approve', 'mandate.revoke'}:
            return b.mandate_action(tenant, env, identity(a['mandate']), operation.split('.')[1], {'synthetic_authorization': True})
        if operation == 'mandate.schedule':
            return b.mandate_action(tenant, env, identity(a['mandate']), 'execute', {'occurrence': a['occurrence'], 'amount_paise': a['amount_paise'], 'due_at': context['clock'] + integer(a['due_after_seconds'], 'Due delay')})
        if operation == 'mandate.dispatch':
            value = b.execute_mandate(tenant, env, identity(a['execution']))
            operation_id = value.get('operation_id') or (value.get('id') if value.get('kind') else None)
            return b.process(tenant, env, operation_id) if operation_id else value
        if operation == 'consent.create':
            account_id = account(a['account'])
            return b.consent(tenant, env, {'customer_id': context['customers'][a['account']], 'account_ids': [account_id], 'purpose': a['purpose'], 'expires_at': context['clock'] + integer(a['valid_for_seconds'], 'Consent lifetime', 1), 'request_key': key})
        if operation in {'consent.approve', 'consent.revoke'}:
            value = b.consent_action(tenant, env, identity(a['consent']), operation.split('.')[1], {'synthetic_authorization': True})
            value.pop('access_token', None)
            return value
        if operation == 'consent.read':
            with self.store.reader() as db:
                consent = b._get(db, 'bank_consents', tenant, env, identity(a['consent']))
            value = b.consent_data(tenant, env, consent['id'], b._consent_token(consent))
            return {'consent_id': consent['id'], 'data_hash': digest(value), 'accounts': len(value['accounts']), 'transactions': len(value['transactions'])}
        if operation == 'settlement.prepare':
            return b.cycle(tenant, env, {'name': a['name'], 'request_key': key})
        if operation == 'settlement.reconcile':
            require(isinstance(a['positions'], dict) and all(name in context['banks'] for name in a['positions']), 'INVALID_REPORT', 'Use fixture bank aliases for observed positions.')
            positions = {context['banks'][name]: value for name, value in a['positions'].items()}
            cycle_id = identity(a['cycle'])
            with self.store.reader() as db:
                old = db.execute('SELECT id FROM bank_cycle_reports WHERE cycle_id=? AND observed_json=?', (cycle_id, canonical(positions))).fetchone()
                cycle = b._get(db, 'bank_cycles', tenant, env, cycle_id)
            return cycle if old else b.reconcile_cycle(tenant, env, cycle_id, {'positions': positions})
        if operation == 'dispute.create':
            return b.dispute(tenant, env, {'operation_id': identity(a['operation']), 'reason': a['reason'], 'request_key': key})
        if operation in {'dispute.review', 'dispute.resolve'}:
            return b.dispute_update(tenant, env, identity(a['dispute']), {'state': 'reviewing' if operation.endswith('review') else 'resolved', 'resolution': a['resolution']})
        if operation == 'risk.rule':
            # Use a deterministic name so replay observes the already published rule.
            name = label(a['name'], maximum=70) + ' ' + step['id']
            with self.store.reader() as db:
                old = db.execute('SELECT * FROM bank_rules WHERE tenant_id=? AND environment_id=? AND name=? ORDER BY version DESC LIMIT 1', (tenant, env, name)).fetchone()
            return dict(old) if old else b.rule(tenant, env, {**a, 'name': name})
        raise DomainError('UNSUPPORTED_OPERATION', 'Operation is outside this bounded simulation profile.')

    def execute(self, tenant, env, identity):
        token = secrets.token_hex(16)
        with self.store.transaction() as db:
            run = self.get(db, 'simulation_runs', tenant, env, identity)
            if run['evidence_json']:
                return self._public_run(run)
            if not db.execute("UPDATE simulation_runs SET lease_token=?,lease_until=?,state='running' WHERE id=? AND (lease_until IS NULL OR lease_until<?)", (token, time.time() + 120, identity, time.time())).rowcount:
                raise BankingBusy()
            definition = self.get(db, 'simulation_plans', tenant, env, run['plan_id'])
            profile = self.get(db, 'simulation_profiles', tenant, env, definition['profile_id'])
        plan, fixture = json.loads(definition['plan_json']), run['fixture_environment_id']
        context, error = json.loads(run['context_json']), run['error']
        failed_step = plan['steps'][run['cursor'] - 1]['id'] if error and run['cursor'] else None
        try:
            if not context:
                context = self._setup(tenant, fixture, plan, profile)
                with self.store.transaction() as db:
                    self._lease(db, identity, token)
                    db.execute('UPDATE simulation_runs SET context_json=? WHERE id=?', (canonical(context), identity))
            require(context['application_fingerprint'] == self.app.source_fingerprint, 'RUN_VERSION_MISMATCH', 'Resume an interrupted experiment on its original executor build; publish a new run for a changed build.', 409)
            for ordinal, step in enumerate(plan['steps']):
                if error:
                    break
                if ordinal < run['cursor']:
                    continue
                with self.store.transaction() as db:
                    self._lease(db, identity, token)
                try:
                    output = self._perform(tenant, fixture, identity, step, context)
                except DomainError as failure:
                    if step.get('expect_error') == failure.code:
                        output = {'expected_error': failure.code, 'passed': True}
                    else:
                        error, failed_step = failure.code + ': ' + failure.message, step['id']
                        output = {'error': failure.code, 'detail': failure.message, 'passed': False}
                else:
                    if 'expect_error' in step:
                        error, failed_step = 'ASSERTION_FAILED: Expected domain error was not raised.', step['id']
                    for field, expected in step.get('expect', {}).items():
                        if field not in output or digest(output[field]) != digest(expected):
                            error, failed_step = 'ASSERTION_FAILED: Output differs for ' + field + '.', step['id']
                context['results'][step['id']] = output
                if step['operation'] == 'clock.advance' and not error:
                    context['clock'] = output['after_epoch']
                with self.store.transaction() as db:
                    self._lease(db, identity, token)
                    db.execute('INSERT INTO simulation_steps VALUES(?,?,?,?,?,?,?)', (identity, ordinal, step['id'], digest(step), canonical(output), business_time(self.store, tenant, fixture, db), now()))
                    db.execute('UPDATE simulation_runs SET cursor=?,context_json=?,error=? WHERE id=?', (ordinal + 1, canonical(context), error, identity))
                    self.store.audit(db, tenant, env, 'simulation.step_completed' if not error else 'simulation.step_failed', identity, {'ordinal': ordinal, 'step_id': step['id'], 'operation': step['operation'], 'input_hash': digest(step), 'output_hash': digest(output)})
                if error:
                    break
            with self.store.reader() as db:
                steps = [dict(r) for r in db.execute('SELECT * FROM simulation_steps WHERE run_id=? ORDER BY ordinal', (identity,))]
                messages = [dict(r) for r in db.execute('SELECT * FROM simulation_messages WHERE tenant_id=? AND environment_id=? ORDER BY created_at,id', (tenant, fixture))]
            from .upi import PROFILE_HASH as participant_profile_hash
            manifest = {'format': 'avenra-simulation-evidence/1', 'scope': PROFILE['scope'], 'official_conformance': False,
                        'participant_profile_hash': participant_profile_hash if any(s['operation'].startswith('upi.') for s in plan['steps']) else None,
                        'application_fingerprint': context['application_fingerprint'], 'profile': json.loads(profile['document_json']), 'profile_hash': profile['document_hash'],
                        'plan': plan, 'plan_hash': definition['plan_hash'], 'plan_version': definition['version'],
                        'tenant_id': tenant, 'environment_id': env, 'fixture_environment_id': fixture, 'run_id': identity,
                        'state': 'failed' if error else 'passed', 'failed_step': failed_step, 'error': error,
                        'completed_steps': len(steps), 'total_steps': len(plan['steps']), 'steps': steps, 'messages': messages,
                        'final_virtual_time': business_time(self.store, tenant, fixture), 'completed_at': now()}
            fingerprint = digest(manifest)
            evidence = {'manifest': manifest, 'manifest_hash': fingerprint, 'signature': hmac.new(self.store.signing_key, fingerprint.encode(), hashlib.sha256).hexdigest()}
            with self.store.transaction() as db:
                self._lease(db, identity, token)
                db.execute('UPDATE simulation_runs SET state=?,evidence_json=?,completed_at=?,lease_token=NULL,lease_until=NULL WHERE id=?', (manifest['state'], canonical(evidence), now(), identity))
                self.store.audit(db, tenant, env, 'simulation.run_' + manifest['state'], identity, {'manifest_hash': fingerprint, 'completed_steps': len(steps)})
            return self.read(tenant, env, identity)
        finally:
            with self.store.transaction() as db:
                db.execute('UPDATE simulation_runs SET lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=? AND evidence_json IS NULL', (identity, token))

    def _lease(self, db, identity, token):
        if not db.execute('UPDATE simulation_runs SET lease_until=? WHERE id=? AND lease_token=? AND evidence_json IS NULL', (time.time() + 120, identity, token)).rowcount:
            raise BankingBusy()


def example_plan():
    return {'clock_epoch': 1_800_000_000, 'banks': ['bank_a', 'bank_b'],
            'accounts': [{'alias': 'payer', 'bank': 'bank_a', 'opening_paise': 100_000}, {'alias': 'merchant', 'bank': 'bank_b', 'opening_paise': 10_000}],
            'steps': [
                {'id': 'request', 'operation': 'message.send', 'args': {'sender': 'payer_psp', 'recipient': 'network', 'kind': 'payment.request', 'payload': {'amount_paise': 50_000}}, 'expect': {'state': 'accepted'}},
                {'id': 'replay', 'operation': 'message.replay', 'args': {'message': 'request'}, 'expect': {'replayed': True}},
                {'id': 'payment', 'operation': 'payment.transfer', 'args': {'payer': 'payer', 'payee': 'merchant', 'amount_paise': 50_000, 'fault': 'lost_credit_response'}, 'expect': {'state': 'unknown'}},
                {'id': 'recovery', 'operation': 'payment.reconcile', 'args': {'operation': 'payment'}, 'expect': {'state': 'succeeded'}},
                {'id': 'effects', 'operation': 'assert.operation', 'args': {'operation': 'payment', 'state': 'succeeded'}},
                {'id': 'refund', 'operation': 'payment.refund', 'args': {'operation': 'payment', 'amount_paise': 20_000}, 'expect': {'state': 'succeeded'}},
                {'id': 'balances', 'operation': 'assert.balances', 'args': {'balances': {'payer': 70_000, 'merchant': 40_000}}},
                {'id': 'consent', 'operation': 'consent.create', 'args': {'account': 'payer', 'purpose': 'Synthetic data-access boundary test', 'valid_for_seconds': 60}},
                {'id': 'approval', 'operation': 'consent.approve', 'args': {'consent': 'consent'}, 'expect': {'state': 'active'}},
                {'id': 'disclosure', 'operation': 'consent.read', 'args': {'consent': 'consent'}, 'expect': {'accounts': 1}},
                {'id': 'deadline', 'operation': 'clock.advance', 'args': {'seconds': 60}},
                {'id': 'expiry', 'operation': 'consent.read', 'args': {'consent': 'consent'}, 'expect_error': 'CONSENT_INACTIVE'},
                {'id': 'late_message', 'operation': 'message.send', 'args': {'sender': 'network', 'recipient': 'beneficiary', 'kind': 'status.test', 'payload': {}, 'lifetime_seconds': 60, 'issued_offset_seconds': -60}, 'expect': {'state': 'rejected', 'reason': 'MESSAGE_EXPIRED'}},
                {'id': 'cycle', 'operation': 'settlement.prepare', 'args': {'name': 'Synthetic cycle'}},
                {'id': 'reconcile', 'operation': 'settlement.reconcile', 'args': {'cycle': 'cycle', 'positions': {'bank_a': -30_000, 'bank_b': 30_000}}, 'expect': {'state': 'reconciled'}},
            ]}


def example_upi_plan():
    """Independent expected amounts for a routed collect, refund and expiry."""
    def step(identity, operation, args, **extra):
        return {'id': identity, 'operation': operation, 'args': args, **extra}
    return {'clock_epoch': 1_800_000_000, 'banks': ['bank_a', 'bank_b'],
            'accounts': [{'alias': 'payer', 'bank': 'bank_a', 'opening_paise': 100000},
                         {'alias': 'merchant', 'bank': 'bank_b', 'opening_paise': 10000, 'kind': 'merchant'}],
            'steps': [
                step('payer_device', 'upi.enrol', {'account': 'payer', 'verified': True}),
                step('merchant_device', 'upi.enrol', {'account': 'merchant', 'verified': True}),
                step('collect', 'upi.prepare', {'payer': 'payer', 'payee': 'merchant', 'amount_paise': 10000, 'device': 'payer_device', 'mode': 'collect', 'financial_fault': 'lost_credit_response'}),
                step('presentation', 'upi.route', {'payment': 'collect'}, expect={'state': 'awaiting_authorization', 'accepted_messages': 2}),
                step('approval', 'upi.authorize', {'payment': 'collect', 'device': 'payer_device', 'approved': True}),
                step('uncertain', 'upi.route', {'payment': 'collect'}, expect={'state': 'unknown'}),
                step('retry_time', 'clock.advance', {'seconds': 2}),
                step('recovery', 'upi.route', {'payment': 'collect'}, expect={'state': 'succeeded'}),
                step('payment_proof', 'upi.assert', {'payment': 'collect', 'state': 'succeeded'}),
                step('refund', 'upi.refund', {'payment': 'collect', 'amount_paise': 4000}),
                step('refund_approval', 'upi.authorize', {'payment': 'refund', 'device': 'merchant_device', 'approved': True}),
                step('refund_route', 'upi.route', {'payment': 'refund'}, expect={'state': 'succeeded'}),
                step('refund_proof', 'upi.assert', {'payment': 'refund', 'state': 'succeeded'}),
                step('balances', 'assert.balances', {'balances': {'payer': 94000, 'merchant': 16000}}),
                step('cycle', 'settlement.prepare', {'name': 'Routed participant obligations'}),
                step('reconcile_cycle', 'settlement.reconcile', {'cycle': 'cycle', 'positions': {'bank_a': -6000, 'bank_b': 6000}}),
                step('unapproved', 'upi.prepare', {'payer': 'payer', 'payee': 'merchant', 'amount_paise': 2000, 'device': 'payer_device', 'expires_in_seconds': 1}),
                step('expiry_time', 'clock.advance', {'seconds': 1}),
                step('expire', 'upi.expire', {'payment': 'unapproved'}, expect={'state': 'expired'}),
                step('expiry_proof', 'upi.assert', {'payment': 'unapproved', 'state': 'expired'}),
                step('unchanged_balances', 'assert.balances', {'balances': {'payer': 94000, 'merchant': 16000}}),
            ]}
