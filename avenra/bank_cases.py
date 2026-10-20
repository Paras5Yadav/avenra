"""Bounded, versioned scenario plans and independent financial assertions."""
import hashlib
import hmac
import json

from .banking import FAULTS, PROFILE
from .errors import require
from .util import canonical, digest, label, money, now, reference, uid


def publish(app, tenant, env, data):
    app.environment(tenant, env, True)
    name, plan = label(data.get('name')), data.get('plan')
    require(isinstance(plan, dict), 'INVALID_PLAN', 'Scenario plan must be an object.')
    required = {'payer_opening_paise', 'payee_opening_paise', 'amount_paise', 'fault', 'reconcile', 'expected_state', 'expected_payer_paise', 'expected_payee_paise'}
    require(set(plan) == required, 'INVALID_PLAN', 'Scenario plan must contain the published banking-plan fields only.')
    for key in required - {'fault', 'reconcile', 'expected_state'}:
        money(plan[key], allow_zero=key != 'amount_paise')
    require(isinstance(plan['fault'], str) and plan['fault'] in FAULTS, 'INVALID_PLAN', 'Unsupported fault.')
    require(type(plan['reconcile']) is bool and isinstance(plan['expected_state'], str) and plan['expected_state'] in {'succeeded', 'declined', 'reversed', 'unknown'}, 'INVALID_PLAN', 'Expected state or reconciliation policy is invalid.')
    with app.store.transaction() as db:
        version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM bank_case_definitions WHERE tenant_id=? AND environment_id=? AND name=?', (tenant, env, name)).fetchone()[0]
        identity = uid('case')
        db.execute('INSERT INTO bank_case_definitions VALUES(?,?,?,?,?,?,?,?)', (identity, tenant, env, name, version, canonical(plan), digest(plan), now()))
        app.store.audit(db, tenant, env, 'bank_case.published', identity, {'version': version, 'plan_hash': digest(plan)})
        return app.banking._get(db, 'bank_case_definitions', tenant, env, identity)


def create_run(app, tenant, env, data):
    app.environment(tenant, env, True)
    key, case_id = reference(data.get('request_key')), data.get('case_id')
    fingerprint = digest({'case_id': case_id, 'profile': PROFILE})
    with app.store.transaction() as db:
        old = app.banking._existing(db, 'bank_test_runs', tenant, env, key, fingerprint)
        if old:
            return old
        case = app.banking._get(db, 'bank_case_definitions', tenant, env, case_id)
        fixture = app._new_environment(db, tenant, case['name'] + ' banking fixture', 'run', 'banking')
        identity = uid('bankrun')
        db.execute('INSERT INTO bank_test_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (identity, tenant, env, case_id, fixture, key, fingerprint, 'queued', None, None, now(), None))
        app.store.enqueue(db, tenant, env, 'bank_case', identity, {}, 'case:' + identity)
        app.store.audit(db, tenant, env, 'bank_case.queued', identity, {'case_id': case_id, 'fixture_environment_id': fixture})
        return app.banking._get(db, 'bank_test_runs', tenant, env, identity)


def read_run(app, tenant, env, identity):
    with app.store.reader() as db:
        run = app.banking._get(db, 'bank_test_runs', tenant, env, identity)
    if run['evidence_json']:
        evidence = json.loads(run.pop('evidence_json'))
        expected_hash = digest(evidence['manifest'])
        expected_signature = hmac.new(app.store.signing_key, expected_hash.encode(), hashlib.sha256).hexdigest()
        evidence['valid'] = hmac.compare_digest(expected_hash, evidence['manifest_hash']) and hmac.compare_digest(expected_signature, evidence['signature'])
        run['evidence'] = evidence
    return run


def execute(app, tenant, env, identity):
    banking = app.banking
    with app.store.transaction() as db:
        run = banking._get(db, 'bank_test_runs', tenant, env, identity)
        if run['state'] in {'passed', 'failed'}:
            return run
        case = banking._get(db, 'bank_case_definitions', tenant, env, run['case_id'])
        db.execute("UPDATE bank_test_runs SET state='running' WHERE id=?", (identity,))
    plan, fixture = json.loads(case['plan_json']), run['fixture_environment_id']
    checks, error, operation = [], None, None
    try:
        app.provision(tenant, fixture)
        first = banking.bank(tenant, fixture, {'name': 'Avenra Test Bank A', 'code': 'bank_a', 'request_key': 'bank-a'})
        second = banking.bank(tenant, fixture, {'name': 'Avenra Test Bank B', 'code': 'bank_b', 'request_key': 'bank-b'})
        customers, accounts = [], []
        for index, (bank, role, opening) in enumerate([(first, 'customer', plan['payer_opening_paise']), (second, 'merchant', plan['payee_opening_paise'])]):
            customer = banking.customer(tenant, fixture, {'name': 'Test ' + role, 'kind': role, 'request_key': 'customer-' + str(index)})
            account = banking.account(tenant, fixture, {'bank_id': bank['id'], 'customer_id': customer['id'], 'vpa': role + '@' + bank['code'], 'opening_paise': opening, 'request_key': 'account-' + str(index)})
            banking.provision_account(tenant, fixture, account['account_id'])
            customers.append(customer)
            accounts.append(account)
        operation = banking.submit(tenant, fixture, {'payer_id': accounts[0]['account_id'], 'payee_id': accounts[1]['account_id'], 'amount_paise': plan['amount_paise'], 'fault': plan['fault'], 'request_key': 'scenario-payment'})
        banking.process(tenant, fixture, operation['id'])
        if plan['reconcile']:
            banking.process(tenant, fixture, operation['id'])
        operation = banking.operation(tenant, fixture, operation['id'])
        balances = {a['id']: a['balance_paise'] for a in app.accounts(tenant, fixture)}
        for key, expected, observed in [('state', plan['expected_state'], operation['state']), ('payer_balance', plan['expected_payer_paise'], balances[accounts[0]['account_id']]), ('payee_balance', plan['expected_payee_paise'], balances[accounts[1]['account_id']])]:
            checks.append({'name': key, 'expected': expected, 'observed': observed, 'passed': expected == observed})
        ledger = app.environment(tenant, fixture)['ledger_name']
        for leg in operation['legs']:
            transactions = app.accounting.transactions(ledger, leg['id'])
            checks.append({'name': 'one_effect:' + leg['kind'], 'expected': {'maximum_transactions': 1}, 'observed': len(transactions), 'passed': len(transactions) <= 1})
        if operation['state'] in {'succeeded', 'declined', 'reversed'}:
            expected_total = plan['payer_opening_paise'] + plan['payee_opening_paise']
            observed_total = balances[accounts[0]['account_id']] + balances[accounts[1]['account_id']]
            checks.append({'name': 'customer_funds_conserved', 'expected': expected_total, 'observed': observed_total, 'passed': expected_total == observed_total})
    except Exception as failure:
        error = str(failure)[:300]
        checks.append({'name': 'execution_completed', 'expected': True, 'observed': type(failure).__name__, 'passed': False})
    state = 'passed' if checks and all(c['passed'] for c in checks) and not error else 'failed'
    manifest = {'format': 'avenra-banking-evidence/1', 'profile': PROFILE, 'run_id': identity, 'tenant_id': tenant, 'environment_id': env, 'fixture_environment_id': fixture, 'case_id': case['id'], 'case_version': case['version'], 'case_hash': case['plan_hash'], 'plan': plan, 'checks': checks, 'operation': operation, 'state': state, 'completed_at': now(), 'scope': 'Synthetic interbank simulation; no institutional conformance or live payment execution.'}
    fingerprint = digest(manifest)
    evidence = {'manifest': manifest, 'manifest_hash': fingerprint, 'signature': hmac.new(app.store.signing_key, fingerprint.encode(), hashlib.sha256).hexdigest()}
    with app.store.transaction() as db:
        db.execute('UPDATE bank_test_runs SET state=?,evidence_json=?,error=?,completed_at=? WHERE id=? AND evidence_json IS NULL', (state, canonical(evidence), error, now(), identity))
        app.store.audit(db, tenant, env, 'bank_case.' + state, identity, {'case_id': case['id']})
    return read_run(app, tenant, env, identity)
