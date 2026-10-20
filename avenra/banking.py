"""Stateful synthetic banking. Financial effects remain in the accounting engine."""
from contextlib import nullcontext
import hashlib
import hmac
import json
import re
import secrets
import time

from .errors import DomainError, require
from .gateways import EngineError
from .util import canonical, digest, label, money, now, reference, row, uid

PROFILE = 'avenra-interbank/1.0'
FAULTS = {'none', 'lost_debit_response', 'lost_credit_response', 'reject_credit', 'reject_authorization'}
ROLES = {'payer_psp', 'payee_psp', 'remitter', 'beneficiary'}
TERMINAL = {'succeeded', 'declined', 'reversed'}


class BankingBusy(Exception):
    pass


def instant(value, field):
    require(type(value) in {int, float} and 0 < value < 32_503_680_000, 'INVALID_TIME', field + ' must be a finite Unix timestamp.')
    return float(value)


class Banking:
    def __init__(self, app):
        self.app, self.store = app, app.store

    def business_time(self, tenant, env, db=None):
        from .simulation import business_time
        return business_time(self.store, tenant, env, db)

    def _existing(self, db, table, tenant, env, key, fingerprint):
        # Table names are constants at internal call sites, never request input.
        record = db.execute(f'SELECT * FROM {table} WHERE tenant_id=? AND environment_id=? AND request_key=?', (tenant, env, key)).fetchone()
        if record:
            require(hmac.compare_digest(record['request_hash'], fingerprint), 'IDEMPOTENCY_CONFLICT', 'Request key already has different content.', 409)
            return dict(record)

    def _get(self, db, table, tenant, env, identity, column='id'):
        require(isinstance(identity, str) and re.fullmatch('[A-Za-z0-9_]{1,80}', identity), 'INVALID_ID', 'Use a valid record identity.')
        record = row(db.execute(f'SELECT * FROM {table} WHERE {column}=? AND tenant_id=? AND environment_id=?', (identity, tenant, env)).fetchone())
        require(record, 'NOT_FOUND', 'Record not found in this environment.', 404)
        return record

    def bank(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        name, code = label(data.get('name')), label(data.get('code'), 'Bank code', 24).lower()
        require(re.fullmatch('[a-z][a-z0-9_]{1,23}', code), 'INVALID_CODE', 'Bank code must use 2–24 lowercase letters, digits or underscores.')
        roles = data.get('roles', sorted(ROLES))
        require(isinstance(roles, list) and roles and all(isinstance(r, str) and r in ROLES for r in roles), 'INVALID_ROLES', 'Select supported participant roles.')
        roles = sorted(set(roles))
        key, fingerprint = reference(data.get('request_key')), digest({'name': name, 'code': code, 'roles': roles})
        with self.store.transaction() as db:
            old = self._existing(db, 'bank_institutions', tenant, env, key, fingerprint)
            if old:
                return old
            require(not db.execute('SELECT 1 FROM bank_institutions WHERE environment_id=? AND code=?', (env, code)).fetchone(), 'DUPLICATE_BANK', 'Bank code is already in use.', 409)
            identity = uid('bank')
            db.execute('INSERT INTO bank_institutions VALUES(?,?,?,?,?,?,?,?,?,?)', (identity, tenant, env, name, code, canonical(roles), 'active', key, fingerprint, now()))
            self.store.audit(db, tenant, env, 'bank.created', identity, {'code': code, 'roles': roles})
            return self._get(db, 'bank_institutions', tenant, env, identity)

    def customer(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        name, kind = label(data.get('name')), data.get('kind', 'customer')
        require(isinstance(kind, str) and kind in {'customer', 'merchant'}, 'INVALID_KIND', 'Choose customer or merchant.')
        key, fingerprint = reference(data.get('request_key')), digest({'name': name, 'kind': kind})
        with self.store.transaction() as db:
            old = self._existing(db, 'bank_customers', tenant, env, key, fingerprint)
            if old:
                return old
            identity = uid('customer')
            db.execute('INSERT INTO bank_customers VALUES(?,?,?,?,?,?,?,?)', (identity, tenant, env, name, kind, key, fingerprint, now()))
            self.store.audit(db, tenant, env, 'customer.created', identity, {'kind': kind})
            return self._get(db, 'bank_customers', tenant, env, identity)

    def account(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        bank_id, customer_id = data.get('bank_id'), data.get('customer_id')
        opening = money(data.get('opening_paise', 0), allow_zero=True)
        vpa = label(data.get('vpa'), 'Local payment address', 120).lower()
        require(re.fullmatch('[a-z0-9][a-z0-9._-]{0,79}@[a-z][a-z0-9_-]{1,30}', vpa), 'INVALID_VPA', 'Use a valid local payment address.')
        key = reference(data.get('request_key'))
        fingerprint = digest({'bank_id': bank_id, 'customer_id': customer_id, 'vpa': vpa, 'opening_paise': opening})
        with self.store.transaction() as db:
            old = self._existing(db, 'bank_accounts', tenant, env, key, fingerprint)
            if old:
                return old
            bank = self._get(db, 'bank_institutions', tenant, env, bank_id)
            customer = self._get(db, 'bank_customers', tenant, env, customer_id)
            require(bank['status'] == 'active', 'BANK_UNAVAILABLE', 'Bank is suspended.', 409)
            require(not db.execute('SELECT 1 FROM accounts WHERE environment_id=? AND vpa=?', (env, vpa)).fetchone(), 'DUPLICATE_VPA', 'Local address already belongs to another account.', 409)
            identity = uid('acc')
            db.execute('INSERT INTO accounts VALUES(?,?,?,?,?,?,?,?)', (identity, tenant, env, customer['name'], vpa, 'bank:' + bank['code'] + ':' + identity, customer['kind'], now()))
            db.execute('INSERT INTO bank_accounts VALUES(?,?,?,?,?,?,?,?,?,?)', (identity, tenant, env, bank_id, customer_id, 'provisioning', opening, key, fingerprint, now()))
            self.store.enqueue(db, tenant, env, 'bank_account', identity, {}, 'fund:' + identity)
            self.store.audit(db, tenant, env, 'bank_account.requested', identity, {'bank_id': bank_id, 'opening_paise': opening})
            return self._get(db, 'bank_accounts', tenant, env, identity, 'account_id')

    def provision_account(self, tenant, env, identity):
        environment = self.app.environment(tenant, env, True)
        with self.store.reader() as db:
            account = self._get(db, 'bank_accounts', tenant, env, identity, 'account_id')
            address = db.execute('SELECT address FROM accounts WHERE id=?', (identity,)).fetchone()[0]
        if account['status'] != 'provisioning':
            return account
        if account['opening_paise']:
            self.app.accounting.post(environment['ledger_name'], {'postings': [{'source': 'world', 'destination': address, 'asset': 'INR/2', 'amount': account['opening_paise']}], 'reference': 'opening:' + identity}, 'opening:' + identity)
        with self.store.transaction() as db:
            if db.execute("UPDATE bank_accounts SET status='active' WHERE account_id=? AND status='provisioning'", (identity,)).rowcount:
                self.store.audit(db, tenant, env, 'bank_account.funded', identity, {'reference': 'opening:' + identity})
            return self._get(db, 'bank_accounts', tenant, env, identity, 'account_id')

    def account_state(self, tenant, env, identity, data):
        target = data.get('state')
        require(isinstance(target, str) and target in {'active', 'blocked'}, 'INVALID_STATE', 'Choose active or blocked.')
        with self.store.transaction() as db:
            account = self._get(db, 'bank_accounts', tenant, env, identity, 'account_id')
            require(account['status'] in {'active', 'blocked'}, 'INVALID_STATE', 'Account is not ready.', 409)
            db.execute('UPDATE bank_accounts SET status=? WHERE account_id=?', (target, identity))
            self.store.audit(db, tenant, env, 'bank_account.' + target, identity, {})
            return self._get(db, 'bank_accounts', tenant, env, identity, 'account_id')

    def _accounts(self, db, tenant, env, payer, payee):
        require(isinstance(payer, str) and isinstance(payee, str) and payer != payee, 'INVALID_ACCOUNT', 'Choose distinct banking accounts.')
        result = []
        for identity in (payer, payee):
            account = self._get(db, 'bank_accounts', tenant, env, identity, 'account_id')
            bank = self._get(db, 'bank_institutions', tenant, env, account['bank_id'])
            require(account['status'] == 'active' and bank['status'] == 'active', 'ACCOUNT_UNAVAILABLE', 'Selected bank/account is not active.', 409)
            result.append({**account, 'address': db.execute('SELECT address FROM accounts WHERE id=?', (identity,)).fetchone()[0], 'roles': json.loads(bank['roles_json'])})
        require('remitter' in result[0]['roles'] and 'beneficiary' in result[1]['roles'], 'UNSUPPORTED_ROLE', 'Banks do not support the required payment roles.', 409)
        return result

    def submit(self, tenant, env, data, kind='transfer', parent=None):
        self.app.environment(tenant, env, True)
        with self.store.transaction() as db:
            return self._submit(db, tenant, env, data, kind, parent)

    def _submit(self, db, tenant, env, data, kind='transfer', parent=None, executor='banking'):
        require(executor in {'banking', 'upi'}, 'INVALID_EXECUTOR', 'Unsupported financial executor.')
        amount, key = money(data.get('amount_paise')), reference(data.get('request_key'))
        fault = data.get('fault', 'none')
        require(isinstance(fault, str) and fault in FAULTS, 'INVALID_FAULT', 'Choose a supported banking fault.')
        payer, payee = data.get('payer_id'), data.get('payee_id')
        if data.get('payer_vpa'):
            resolved = self._resolve(db, tenant, env, data['payer_vpa'])['account_id']
            require(not payer or payer == resolved, 'ACCOUNT_ADDRESS_MISMATCH', 'Payer identity and address must refer to the same account.')
            payer = resolved
        if data.get('payee_vpa'):
            resolved = self._resolve(db, tenant, env, data['payee_vpa'])['account_id']
            require(not payee or payee == resolved, 'ACCOUNT_ADDRESS_MISMATCH', 'Payee identity and address must refer to the same account.')
            payee = resolved
        require(kind in {'transfer', 'refund', 'reversal'}, 'INVALID_KIND', 'Unsupported banking operation.')
        callback_id = data.get('callback_id')
        fingerprint = digest({'kind': kind, 'parent_id': parent, 'payer_id': payer, 'payee_id': payee, 'amount_paise': amount, 'fault': fault, 'profile': PROFILE, 'callback_id': callback_id})
        if executor != 'banking':
            fingerprint = digest({'payload': fingerprint, 'executor': executor})
        old = self._existing(db, 'bank_operations', tenant, env, key, fingerprint)
        if old:
            return old
        accounts = self._accounts(db, tenant, env, payer, payee)
        if callback_id:
            target = self._get(db, 'bank_callback_destinations', tenant, env, callback_id)
            require(target['status'] == 'active', 'DESTINATION_DISABLED', 'Callback destination is disabled.', 409)
        if parent:
            original = self._get(db, 'bank_operations', tenant, env, parent)
            require(original['kind'] == 'transfer' and original['state'] == 'succeeded', 'NOT_REFUNDABLE', 'Original transfer must be confirmed.', 409)
            require((payer, payee) == (original['payee_id'], original['payer_id']), 'INVALID_ACCOUNT', 'Adjustment must return funds to the original payer.')
            reserved = db.execute("SELECT COALESCE(SUM(amount_paise),0) FROM bank_operations WHERE parent_id=? AND state IN ('pending','processing','unknown','succeeded')", (parent,)).fetchone()[0]
            require(reserved + amount <= original['amount_paise'], 'ADJUSTMENT_LIMIT', 'Adjustments exceed the unreserved original amount.', 409)
        identity, timestamp = uid('op'), now()
        declined, reason = fault == 'reject_authorization', None
        if declined:
            reason = 'Synthetic authorization rejected.'
        db.execute('INSERT INTO bank_operations(id,tenant_id,environment_id,kind,parent_id,payer_id,payee_id,amount_paise,request_key,request_hash,profile,state,fault,reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (identity, tenant, env, kind, parent, payer, payee, amount, key, fingerprint, PROFILE, 'declined' if declined else 'pending', fault, reason, timestamp, timestamp))
        if executor != 'banking':
            db.execute('UPDATE bank_operations SET executor=? WHERE id=?', (executor, identity))
        if callback_id:
            db.execute('UPDATE bank_operations SET callback_id=? WHERE id=?', (callback_id, identity))
        rules = db.execute('SELECT r.* FROM bank_rules r WHERE tenant_id=? AND environment_id=? AND version=(SELECT MAX(x.version) FROM bank_rules x WHERE x.tenant_id=r.tenant_id AND x.environment_id=r.environment_id AND x.name=r.name) AND enabled=1', (tenant, env)).fetchall()
        for rule in rules:
            matched = amount >= rule['threshold_paise']
            db.execute('INSERT INTO bank_risk_results VALUES(?,?,?,?,?,?)', (uid('risk'), identity, rule['id'], int(matched), rule['action'], timestamp))
            if matched and rule['action'] == 'block':
                declined, reason = True, 'Blocked by synthetic policy ' + rule['name'] + ' version ' + str(rule['version'])
                db.execute("UPDATE bank_operations SET state='declined',reason=? WHERE id=?", (reason, identity))
        self._message(db, identity, 'request', 'payer_psp', 'simulation_network', {'amount_paise': amount, 'profile': PROFILE, 'payer_bank': accounts[0]['bank_id'], 'payee_bank': accounts[1]['bank_id']})
        if not declined:
            suspense = 'clearing:' + identity
            for ordinal, leg_kind, source, destination in [(1, 'debit', accounts[0]['address'], suspense), (2, 'credit', suspense, accounts[1]['address'])]:
                self._new_leg(db, identity, ordinal, leg_kind, source, destination, amount)
            if executor == 'banking':
                self.store.enqueue(db, tenant, env, 'bank_operation', identity, {}, 'bank:' + identity)
        self.store.audit(db, tenant, env, 'bank_operation.accepted', identity, {'kind': kind, 'fingerprint': fingerprint, 'profile': PROFILE})
        if declined:
            self.app.callbacks.enqueue(db, self._get(db, 'bank_operations', tenant, env, identity))
        return self._get(db, 'bank_operations', tenant, env, identity)

    def adjust(self, tenant, env, identity, data, kind='refund'):
        with self.store.reader() as db:
            original = self._get(db, 'bank_operations', tenant, env, identity)
        return self.submit(tenant, env, {**data, 'payer_id': original['payee_id'], 'payee_id': original['payer_id']}, kind, identity)

    def _resolve(self, db, tenant, env, vpa):
        require(isinstance(vpa, str) and 1 <= len(vpa) <= 120, 'INVALID_VPA', 'Use a local payment address.')
        value = row(db.execute('SELECT b.account_id,b.bank_id,b.status,a.vpa,a.name FROM bank_accounts b JOIN accounts a ON a.id=b.account_id WHERE b.tenant_id=? AND b.environment_id=? AND a.vpa=?', (tenant, env, vpa.lower())).fetchone())
        require(value, 'ADDRESS_NOT_FOUND', 'Payment address was not found in this environment.', 404)
        return value

    def resolve(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        with self.store.reader() as db:
            return self._resolve(db, tenant, env, data.get('vpa'))

    def _new_leg(self, db, operation, ordinal, kind, source, destination, amount):
        identity = 'leg_' + hashlib.sha256((operation + ':' + kind).encode()).hexdigest()[:32]
        payload = {'source': source, 'destination': destination, 'amount': amount, 'asset': 'INR/2'}
        db.execute("INSERT OR IGNORE INTO bank_legs(id,operation_id,ordinal,kind,source,destination,amount_paise,request_hash,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,'pending',?,?)", (identity, operation, ordinal, kind, source, destination, amount, digest(payload), now(), now()))

    def _message(self, db, operation, kind, sender, recipient, payload):
        db.execute('INSERT OR IGNORE INTO bank_messages VALUES(?,?,?,?,?,?,?,?)', (uid('msg'), operation, kind, sender, recipient, canonical(payload), digest(payload), now()))

    def operation(self, tenant, env, identity):
        self.app.environment(tenant, env)
        with self.store.reader() as db:
            value = self._get(db, 'bank_operations', tenant, env, identity)
            value['legs'] = [dict(x) for x in db.execute('SELECT * FROM bank_legs WHERE operation_id=? ORDER BY ordinal', (identity,))]
            value['messages'] = [dict(x) for x in db.execute('SELECT * FROM bank_messages WHERE operation_id=? ORDER BY created_at,id', (identity,))]
            value['risk'] = [dict(x) for x in db.execute('SELECT rr.*,r.name,r.version FROM bank_risk_results rr JOIN bank_rules r ON r.id=rr.rule_id WHERE rr.operation_id=?', (identity,))]
            value['adjustments'] = [dict(x) for x in db.execute('SELECT id,kind,amount_paise,state FROM bank_operations WHERE parent_id=?', (identity,))]
            value.pop('lease_token', None)
            return value

    def reconcile(self, tenant, env, identity):
        operation = self.operation(tenant, env, identity)
        require(operation['executor'] == 'banking', 'EXECUTOR_OWNED', 'Reconcile this financial intent through its participant payment.', 409)
        if operation['state'] not in TERMINAL:
            with self.store.transaction() as db:
                self.store.enqueue(db, tenant, env, 'bank_operation', identity, {}, 'reconcile:' + uid('attempt'))
                self.store.audit(db, tenant, env, 'bank_operation.reconciliation_requested', identity, {})
        return operation

    def _effect(self, operation, kind, ledger, gateway=None):
        gateway = gateway or self.app.accounting
        with self.store.reader() as db:
            leg = dict(db.execute('SELECT * FROM bank_legs WHERE operation_id=? AND kind=?', (operation['id'], kind)).fetchone())
        if leg['state'] in {'confirmed', 'declined'}:
            return leg['state']
        # Fence dispatch before leaving durable storage. A restart must not
        # interpret an interrupted external call as an unattempted debit.
        with self.store.transaction() as db:
            db.execute("UPDATE bank_legs SET state='unknown',updated_at=? WHERE id=? AND state='pending'", (now(), leg['id']))
        body = {'postings': [{'source': leg['source'], 'destination': leg['destination'], 'amount': leg['amount_paise'], 'asset': 'INR/2'}], 'reference': leg['id'], 'metadata': {'operation_id': operation['id'], 'leg': kind, 'profile': PROFILE}}
        state, reason, result = 'unknown', None, None
        try:
            observed = gateway.transactions(ledger, leg['id'])
            require(len(observed) <= 1, 'FINANCIAL_INVARIANT', 'Multiple effects share one financial identity.', 409)
            if observed:
                require(observed[0]['postings'] == body['postings'] and observed[0].get('reference') == leg['id'], 'FINANCIAL_INVARIANT', 'Stored financial effect does not match the authorized payload.', 409)
                result = observed[0]
            else:
                apply_fault = operation['fault'] == 'lost_' + kind + '_response' and not leg['fault_applied']
                if apply_fault:
                    with self.store.transaction() as db:
                        apply_fault = db.execute('UPDATE bank_legs SET fault_applied=1 WHERE id=? AND fault_applied=0', (leg['id'],)).rowcount == 1
                fault_provider = gateway if hasattr(gateway, 'lost_response') else self.app.fault
                with fault_provider.lost_response(leg['id']) if apply_fault else nullcontext(None) as origin:
                    result = gateway.post(ledger, body, leg['id'], origin)
            require(result['postings'] == body['postings'] and result.get('reference') == leg['id'], 'FINANCIAL_INVARIANT', 'Bank response differs from the exact accepted financial leg.', 409)
            state = 'confirmed'
        except EngineError as error:
            if error.code == 'INSUFFICIENT_FUND':
                state, reason = 'declined', 'Insufficient available synthetic funds.'
            else:
                reason = 'Financial effect is unconfirmed; recovery reuses this leg identity.'
        with self.store.transaction() as db:
            db.execute("UPDATE bank_legs SET state=?,ledger_id=?,reason=?,updated_at=? WHERE id=? AND state!='confirmed'", (state, str(result['id']) if result else None, reason, now(), leg['id']))
            for receipt in getattr(gateway, 'receipts', []):
                self._message(db, operation['id'], 'bank.transport:' + receipt['request']['request_id'], 'simulation_network', receipt['request']['scope']['participant_id'], receipt)
            self._message(db, operation['id'], kind + ':' + state, 'remitter_bank' if kind == 'debit' else 'beneficiary_bank', 'simulation_network', {'leg_id': leg['id'], 'state': state, 'ledger_id': str(result['id']) if result else None})
            self.store.audit(db, operation['tenant_id'], operation['environment_id'], 'bank_leg.' + state, leg['id'], {'kind': kind, 'ledger_id': str(result['id']) if result else None})
        return state

    def process(self, tenant, env, identity, phase='all', executor='banking', effect=None):
        effect = effect or self._effect
        require(phase in {'all', 'debit', 'credit'}, 'INVALID_PHASE', 'Unsupported financial phase.')
        token = secrets.token_hex(16)
        with self.store.transaction() as db:
            operation = self._get(db, 'bank_operations', tenant, env, identity)
            require(operation['executor'] == executor, 'EXECUTOR_OWNED', 'This financial intent has a different executor.', 409)
            require(executor == 'upi' or phase == 'all', 'INVALID_PHASE', 'Financial phase routing requires the participant executor.')
            if operation['state'] in TERMINAL:
                return operation
            if phase == 'credit':
                debit_record = db.execute("SELECT state FROM bank_legs WHERE operation_id=? AND kind='debit'", (identity,)).fetchone()
                require(debit_record and debit_record['state'] == 'confirmed', 'MESSAGE_ORDER', 'Beneficiary processing requires a confirmed remitter debit.', 409)
            acquired = db.execute("UPDATE bank_operations SET lease_token=?,lease_until=?,state='processing' WHERE id=? AND (lease_until IS NULL OR lease_until<?)", (token, time.time() + 45, identity, time.time())).rowcount
            if not acquired:
                raise BankingBusy()
            debit_record = db.execute("SELECT state FROM bank_legs WHERE operation_id=? AND kind='debit'", (identity,)).fetchone()
            if debit_record and debit_record['state'] == 'pending':
                try:
                    self._accounts(db, tenant, env, operation['payer_id'], operation['payee_id'])
                except DomainError as error:
                    if error.code not in {'ACCOUNT_UNAVAILABLE', 'UNSUPPORTED_ROLE'}:
                        raise
                    db.execute("UPDATE bank_legs SET state='declined',reason=?,updated_at=? WHERE operation_id=? AND kind='debit'", ('Participant account unavailable before financial dispatch.', now(), identity))
            credit_record = db.execute("SELECT state FROM bank_legs WHERE operation_id=? AND kind='credit'", (identity,)).fetchone()
            beneficiary = db.execute('SELECT a.status account_status,b.status bank_status FROM bank_accounts a JOIN bank_institutions b ON b.id=a.bank_id WHERE a.account_id=?', (operation['payee_id'],)).fetchone()
            beneficiary_unavailable = bool(credit_record and credit_record['state'] == 'pending' and beneficiary and (beneficiary['account_status'] != 'active' or beneficiary['bank_status'] != 'active'))
        try:
            ledger = self.app.environment(tenant, env, True)['ledger_name']
            debit = effect(operation, 'debit', ledger)
            state, reason = 'unknown', 'Awaiting confirmation of an existing financial leg.'
            if debit == 'declined':
                state, reason = 'declined', 'Payer debit declined.'
            elif debit == 'confirmed':
                if phase == 'debit':
                    state, reason = 'processing', 'Remitter debit confirmed; awaiting participant delivery to the beneficiary.'
                elif operation['fault'] == 'reject_credit' or beneficiary_unavailable:
                    with self.store.transaction() as db:
                        db.execute("UPDATE bank_legs SET state='declined',reason='Synthetic beneficiary rejection.',updated_at=? WHERE operation_id=? AND kind='credit' AND state='pending'", (now(), identity))
                        source = db.execute('SELECT destination FROM bank_legs WHERE operation_id=? AND kind=\'debit\'', (identity,)).fetchone()[0]
                        destination = db.execute('SELECT source FROM bank_legs WHERE operation_id=? AND kind=\'debit\'', (identity,)).fetchone()[0]
                        self._new_leg(db, identity, 3, 'reversal', source, destination, operation['amount_paise'])
                    reversal = effect(operation, 'reversal', ledger)
                    if reversal == 'confirmed':
                        state, reason = 'reversed', 'Beneficiary rejected; original debit returned from suspense.'
                else:
                    credit = effect(operation, 'credit', ledger)
                    if credit == 'confirmed':
                        state, reason = 'succeeded', None
                    elif credit == 'declined':
                        reason = 'Unexpected credit decline requires financial investigation.'
            with self.store.transaction() as db:
                changed = db.execute('UPDATE bank_operations SET state=?,reason=?,updated_at=? WHERE id=? AND lease_token=?', (state, reason, now(), identity, token)).rowcount
                if changed:
                    self.store.audit(db, tenant, env, 'bank_operation.' + state, identity, {'profile': PROFILE})
                    if state in TERMINAL:
                        self.app.callbacks.enqueue(db, self._get(db, 'bank_operations', tenant, env, identity))
                    if state == 'succeeded':
                        self.app.monitoring.enqueue(db, self._get(db, 'bank_operations', tenant, env, identity))
            return self.operation(tenant, env, identity)
        finally:
            with self.store.transaction() as db:
                db.execute('UPDATE bank_operations SET lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=?', (identity, token))

    def rule(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        name, threshold = label(data.get('name')), money(data.get('threshold_paise'))
        action, enabled = data.get('action'), data.get('enabled', True)
        require(isinstance(action, str) and action in {'alert', 'block'} and type(enabled) is bool, 'INVALID_RULE', 'Use alert or block and a boolean enabled value.')
        with self.store.transaction() as db:
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM bank_rules WHERE tenant_id=? AND environment_id=? AND name=?', (tenant, env, name)).fetchone()[0]
            identity = uid('rule')
            db.execute('INSERT INTO bank_rules VALUES(?,?,?,?,?,?,?,?,?)', (identity, tenant, env, name, version, threshold, action, int(enabled), now()))
            self.store.audit(db, tenant, env, 'rule.published', identity, {'version': version, 'engine': 'avenra-threshold/1'})
            return self._get(db, 'bank_rules', tenant, env, identity)

    def mandate(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        payer, payee = data.get('payer_id'), data.get('payee_id')
        amount, start, expiry = money(data.get('maximum_paise')), instant(data.get('starts_at'), 'Start'), instant(data.get('expires_at'), 'Expiry')
        require(start < expiry and expiry > self.business_time(tenant, env), 'INVALID_TIME', 'Mandate expiry must be after its start and in the future.')
        key = reference(data.get('request_key'))
        fingerprint = digest({'payer': payer, 'payee': payee, 'maximum': amount, 'starts': start, 'expires': expiry})
        with self.store.transaction() as db:
            old = self._existing(db, 'bank_mandates', tenant, env, key, fingerprint)
            if old:
                return old
            self._accounts(db, tenant, env, payer, payee)
            identity = uid('mandate')
            db.execute('INSERT INTO bank_mandates VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (identity, tenant, env, payer, payee, amount, start, expiry, 'pending', key, fingerprint, now()))
            self.store.audit(db, tenant, env, 'mandate.requested', identity, {'profile': PROFILE})
            return self._get(db, 'bank_mandates', tenant, env, identity)

    def mandate_action(self, tenant, env, identity, action, data):
        require(action in {'approve', 'revoke', 'execute'}, 'INVALID_ACTION', 'Unknown mandate action.')
        with self.store.transaction() as db:
            timestamp = self.business_time(tenant, env, db)
            mandate = self._get(db, 'bank_mandates', tenant, env, identity)
            if action == 'approve':
                require(data.get('synthetic_authorization') is True, 'AUTHORIZATION_REQUIRED', 'Explicit synthetic payer authorization is required.', 403)
                require(mandate['state'] in {'pending', 'active'} and mandate['expires_at'] > timestamp, 'INVALID_STATE', 'Mandate cannot be approved.', 409)
                db.execute("UPDATE bank_mandates SET state='active' WHERE id=?", (identity,))
            elif action == 'revoke':
                db.execute("UPDATE bank_mandates SET state='revoked' WHERE id=?", (identity,))
                db.execute("UPDATE bank_mandate_executions SET state='cancelled' WHERE mandate_id=? AND state='scheduled'", (identity,))
            else:
                occurrence = label(data.get('occurrence'), 'Occurrence identity', 80)
                require(re.fullmatch('[A-Za-z0-9_.:-]+', occurrence), 'INVALID_KEY', 'Occurrence has unsupported characters.')
                amount, due = money(data.get('amount_paise')), instant(data.get('due_at'), 'Due time')
                old = row(db.execute('SELECT * FROM bank_mandate_executions WHERE mandate_id=? AND occurrence=?', (identity, occurrence)).fetchone())
                if old:
                    require(old['amount_paise'] == amount and old['due_at'] == due, 'IDEMPOTENCY_CONFLICT', 'Occurrence already has different content.', 409)
                    return old
                require(mandate['state'] == 'active' and mandate['starts_at'] <= due < mandate['expires_at'] and mandate['expires_at'] > timestamp, 'MANDATE_INACTIVE', 'Mandate does not authorize this execution.', 409)
                require(amount <= mandate['maximum_paise'], 'MANDATE_LIMIT', 'Amount exceeds mandate limit.', 409)
                execution = uid('execution')
                db.execute('INSERT INTO bank_mandate_executions VALUES(?,?,?,?,?,?,?,?)', (execution, identity, occurrence, amount, due, 'scheduled', None, now()))
                virtual = db.execute('SELECT 1 FROM simulation_clocks WHERE tenant_id=? AND environment_id=?', (tenant, env)).fetchone()
                self.store.enqueue(db, tenant, env, 'bank_mandate', execution, {}, 'mandate:' + execution, 0 if virtual else max(0, due - timestamp))
                self.store.audit(db, tenant, env, 'mandate.scheduled', execution, {'mandate_id': identity, 'due_at': due})
                return dict(db.execute('SELECT * FROM bank_mandate_executions WHERE id=?', (execution,)).fetchone())
            self.store.audit(db, tenant, env, 'mandate.' + action, identity, {})
            return self._get(db, 'bank_mandates', tenant, env, identity)

    def execute_mandate(self, tenant, env, execution_id):
        with self.store.transaction() as db:
            timestamp = self.business_time(tenant, env, db)
            execution = row(db.execute('SELECT e.* FROM bank_mandate_executions e JOIN bank_mandates m ON m.id=e.mandate_id WHERE e.id=? AND m.tenant_id=? AND m.environment_id=?', (execution_id, tenant, env)).fetchone())
            require(execution, 'NOT_FOUND', 'Mandate execution not found.', 404)
            if execution['state'] != 'scheduled':
                return execution
            mandate = self._get(db, 'bank_mandates', tenant, env, execution['mandate_id'])
            if mandate['state'] != 'active' or not mandate['starts_at'] <= timestamp < mandate['expires_at']:
                db.execute("UPDATE bank_mandate_executions SET state='cancelled' WHERE id=?", (execution_id,))
                return {'state': 'cancelled'}
            require(timestamp >= execution['due_at'], 'NOT_DUE', 'Execution is not due.', 409)
            operation = self._submit(db, tenant, env, {'payer_id': mandate['payer_id'], 'payee_id': mandate['payee_id'], 'amount_paise': execution['amount_paise'], 'request_key': 'mandate:' + execution_id})
            db.execute("UPDATE bank_mandate_executions SET state='dispatched',operation_id=? WHERE id=?", (operation['id'], execution_id))
            self.store.audit(db, tenant, env, 'mandate.dispatched', execution_id, {'operation_id': operation['id']})
            return operation

    def dispute(self, tenant, env, data):
        operation_id, reason, key = data.get('operation_id'), label(data.get('reason'), 'Reason', 500), reference(data.get('request_key'))
        fingerprint = digest({'operation_id': operation_id, 'reason': reason})
        with self.store.transaction() as db:
            old = self._existing(db, 'bank_disputes', tenant, env, key, fingerprint)
            if old:
                return old
            self._get(db, 'bank_operations', tenant, env, operation_id)
            identity = uid('dispute')
            db.execute('INSERT INTO bank_disputes VALUES(?,?,?,?,?,?,?,?,?,?,?)', (identity, tenant, env, operation_id, reason, 'open', None, key, fingerprint, now(), now()))
            self.store.audit(db, tenant, env, 'dispute.opened', identity, {'operation_id': operation_id})
            return self._get(db, 'bank_disputes', tenant, env, identity)

    def dispute_update(self, tenant, env, identity, data):
        state, resolution = data.get('state'), label(data.get('resolution'), 'Resolution', 1000)
        require(isinstance(state, str), 'INVALID_STATE', 'Dispute state must be a string.')
        with self.store.transaction() as db:
            case = self._get(db, 'bank_disputes', tenant, env, identity)
            if case['state'] == state:
                require(case['resolution'] == resolution, 'STATE_CONFLICT', 'Existing resolution has different content.', 409)
                return case
            require(state in {'open': {'reviewing', 'rejected'}, 'reviewing': {'resolved', 'rejected'}}.get(case['state'], set()), 'INVALID_TRANSITION', 'Dispute transition is not permitted.', 409)
            db.execute('UPDATE bank_disputes SET state=?,resolution=?,updated_at=? WHERE id=?', (state, resolution, now(), identity))
            self.store.audit(db, tenant, env, 'dispute.' + state, identity, {'resolution': resolution})
            return self._get(db, 'bank_disputes', tenant, env, identity)

    def cycle(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        name, key = label(data.get('name')), reference(data.get('request_key'))
        fingerprint = digest({'name': name, 'profile': PROFILE})
        with self.store.transaction() as db:
            old = self._existing(db, 'bank_cycles', tenant, env, key, fingerprint)
            if old:
                return old
            operations = db.execute("SELECT o.*,a.bank_id payer_bank,b.bank_id payee_bank FROM bank_operations o JOIN bank_accounts a ON a.account_id=o.payer_id JOIN bank_accounts b ON b.account_id=o.payee_id WHERE o.tenant_id=? AND o.environment_id=? AND o.state='succeeded' AND NOT EXISTS(SELECT 1 FROM bank_cycle_items i WHERE i.operation_id=o.id)", (tenant, env)).fetchall()
            positions = {}
            for operation in operations:
                positions[operation['payer_bank']] = positions.get(operation['payer_bank'], 0) - operation['amount_paise']
                positions[operation['payee_bank']] = positions.get(operation['payee_bank'], 0) + operation['amount_paise']
            require(sum(positions.values()) == 0, 'FINANCIAL_INVARIANT', 'Participant positions do not balance.', 409)
            identity = uid('cycle')
            db.execute('INSERT INTO bank_cycles VALUES(?,?,?,?,?,?,?,?,?,?)', (identity, tenant, env, name, key, fingerprint, 'prepared', canonical(positions), '[]', now()))
            for operation in operations:
                db.execute('INSERT INTO bank_cycle_items VALUES(?,?,?)', (operation['id'], identity, operation['amount_paise']))
            self.store.audit(db, tenant, env, 'settlement.prepared', identity, {'operations': len(operations), 'positions': positions})
            return self._get(db, 'bank_cycles', tenant, env, identity)

    def reconcile_cycle(self, tenant, env, identity, data):
        observed = data.get('positions')
        require(isinstance(observed, dict) and len(observed) <= 1000 and all(isinstance(k, str) and type(v) is int and abs(v) <= 10**14 for k, v in observed.items()), 'INVALID_REPORT', 'Positions must map institution IDs to signed integer paise.')
        with self.store.transaction() as db:
            cycle = self._get(db, 'bank_cycles', tenant, env, identity)
            expected = json.loads(cycle['positions_json'])
            exceptions = [{'bank_id': k, 'expected': expected.get(k), 'observed': observed.get(k)} for k in sorted(set(expected) | set(observed)) if expected.get(k) != observed.get(k)]
            report = uid('report')
            db.execute('INSERT INTO bank_cycle_reports VALUES(?,?,?,?,?)', (report, identity, canonical(observed), canonical(exceptions), now()))
            db.execute('UPDATE bank_cycles SET state=?,exceptions_json=? WHERE id=?', ('exceptions' if exceptions else 'reconciled', canonical(exceptions), identity))
            self.store.audit(db, tenant, env, 'settlement.reconciled', identity, {'report_id': report, 'exceptions': len(exceptions)})
            return self._get(db, 'bank_cycles', tenant, env, identity)

    def consent(self, tenant, env, data):
        self.app.environment(tenant, env, True)
        customer, accounts = data.get('customer_id'), data.get('account_ids')
        require(isinstance(accounts, list) and 0 < len(accounts) <= 50 and all(isinstance(a, str) for a in accounts), 'INVALID_ACCOUNTS', 'Select 1–50 customer accounts.')
        accounts = sorted(set(accounts))
        purpose, expiry, key = label(data.get('purpose'), 'Purpose', 500), instant(data.get('expires_at'), 'Expiry'), reference(data.get('request_key'))
        timestamp = self.business_time(tenant, env)
        require(timestamp < expiry <= timestamp + 86400 * 365, 'INVALID_TIME', 'Consent must expire within one year.')
        fingerprint = digest({'customer': customer, 'accounts': accounts, 'purpose': purpose, 'expiry': expiry})
        with self.store.transaction() as db:
            old = self._existing(db, 'bank_consents', tenant, env, key, fingerprint)
            if old:
                return old
            self._get(db, 'bank_customers', tenant, env, customer)
            for account_id in accounts:
                account = self._get(db, 'bank_accounts', tenant, env, account_id, 'account_id')
                require(account['customer_id'] == customer, 'CONSENT_SCOPE', 'Account does not belong to this customer.', 403)
            identity = uid('consent')
            db.execute('INSERT INTO bank_consents VALUES(?,?,?,?,?,?,?,?,?,?,?)', (identity, tenant, env, customer, canonical(accounts), purpose, expiry, 'pending', key, fingerprint, now()))
            self.store.audit(db, tenant, env, 'consent.requested', identity, {'profile': 'avenra-consent/1'})
            return self._get(db, 'bank_consents', tenant, env, identity)

    def _consent_token(self, consent):
        return hmac.new(self.store.signing_key, ('consent:' + consent['id'] + ':' + consent['request_hash']).encode(), hashlib.sha256).hexdigest()

    def consent_action(self, tenant, env, identity, action, data):
        require(action in {'approve', 'revoke'}, 'INVALID_ACTION', 'Unknown consent action.')
        with self.store.transaction() as db:
            consent = self._get(db, 'bank_consents', tenant, env, identity)
            if action == 'approve':
                require(data.get('synthetic_authorization') is True, 'AUTHORIZATION_REQUIRED', 'Explicit synthetic customer authorization is required.', 403)
                require(consent['state'] in {'pending', 'active'} and consent['expires_at'] > self.business_time(tenant, env, db), 'CONSENT_INACTIVE', 'Consent cannot be approved.', 409)
                db.execute("UPDATE bank_consents SET state='active' WHERE id=?", (identity,))
            else:
                db.execute("UPDATE bank_consents SET state='revoked' WHERE id=?", (identity,))
            self.store.audit(db, tenant, env, 'consent.' + action, identity, {})
            result = self._get(db, 'bank_consents', tenant, env, identity)
            if action == 'approve':
                result['access_token'] = self._consent_token(result)
            return result

    def consent_data(self, tenant, env, identity, token):
        with self.store.reader() as db:
            consent = self._get(db, 'bank_consents', tenant, env, identity)
            require(consent['state'] == 'active' and consent['expires_at'] > self.business_time(tenant, env, db), 'CONSENT_INACTIVE', 'Consent is revoked, unapproved or expired.', 403)
            require(isinstance(token, str) and hmac.compare_digest(token, self._consent_token(consent)), 'CONSENT_TOKEN', 'Valid consent access token required.', 403)
            ids = json.loads(consent['account_ids_json'])
        accounts = [a for a in self.app.accounts(tenant, env) if a['id'] in ids]
        require(len(accounts) == len(ids), 'FINANCIAL_INVARIANT', 'A consented account is unavailable.', 409)
        addresses = {a['address'] for a in accounts}
        ledger = self.app.environment(tenant, env, True)['ledger_name']
        transactions = [t for t in self.app.accounting.transactions(ledger, all_pages=True) if any(p['source'] in addresses or p['destination'] in addresses for p in t['postings'])]
        with self.store.transaction() as db:
            current = self._get(db, 'bank_consents', tenant, env, identity)
            require(current['state'] == 'active' and current['expires_at'] > self.business_time(tenant, env, db), 'CONSENT_INACTIVE', 'Consent changed while data was being prepared.', 403)
            self.store.audit(db, tenant, env, 'consent.data_accessed', identity, {'accounts': ids})
        return {'profile': 'avenra-consent/1', 'consent_id': identity, 'accounts': accounts, 'transactions': transactions, 'scope': 'Synthetic local financial data; institutional AA conformance is not established.'}

    def overview(self, tenant, env):
        self.app.environment(tenant, env)
        tables = {'banks': 'bank_institutions', 'customers': 'bank_customers', 'operations': 'bank_operations', 'mandates': 'bank_mandates', 'disputes': 'bank_disputes', 'cycles': 'bank_cycles', 'consents': 'bank_consents', 'rules': 'bank_rules', 'cases': 'bank_case_definitions', 'test_runs': 'bank_test_runs', 'callback_destinations': 'bank_callback_destinations', 'core_commands': 'external_commands'}
        result = {'profile': PROFILE, 'scope': 'Synthetic interbank and consent profiles', 'official_upi_conformance': False}
        with self.store.reader() as db:
            for key, table in tables.items():
                result[key] = [dict(x) for x in db.execute(f'SELECT * FROM {table} WHERE tenant_id=? AND environment_id=? ORDER BY created_at DESC,id DESC LIMIT 100', (tenant, env))]
            result['accounts'] = [dict(x) for x in db.execute('SELECT ba.*,a.name,a.vpa,a.address,b.name bank_name FROM bank_accounts ba JOIN accounts a ON a.id=ba.account_id JOIN bank_institutions b ON b.id=ba.bank_id WHERE ba.tenant_id=? AND ba.environment_id=? ORDER BY ba.created_at', (tenant, env))]
            result['notifications'] = [dict(x) for x in db.execute('SELECT n.* FROM bank_notifications n JOIN bank_operations o ON o.id=n.operation_id WHERE o.tenant_id=? AND o.environment_id=? ORDER BY n.created_at DESC LIMIT 100', (tenant, env))]
            result['analysis'] = [dict(x) for x in db.execute("SELECT id,state,error,created_at FROM external_commands WHERE tenant_id=? AND environment_id=? AND service='monitoring' ORDER BY created_at DESC LIMIT 100", (tenant, env))]
            result['core_commands'] = [x for x in result['core_commands'] if x['service'] == 'banking_core']
            for operation in result['operations']:
                operation.pop('lease_token', None)
            result['capabilities'] = {'interbank': True, 'adjustments': True, 'mandates': True, 'disputes': True, 'settlement_reports': True, 'consent_simulation': True, 'custom_cases': True, 'bank_uat': False, 'live_upi': False}
        balances = {a['id']: a['balance_paise'] for a in self.app.accounts(tenant, env)} if result['accounts'] else {}
        for account in result['accounts']:
            account['balance_paise'] = balances.get(account['account_id'])
        result['simulation'] = self.app.simulation.overview(tenant, env)
        result['upi'] = self.app.upi.overview(tenant, env)
        return result
