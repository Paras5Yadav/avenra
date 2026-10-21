#!/usr/bin/env python3
"""Exercise banking behaviour against the real local accounting/fault engines."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.app import App
from avenra.util import application_fingerprint
from avenra.local_database import admin_dsn
from avenra import bank_cases
from avenra.banking import BankingBusy
from avenra.errors import DomainError
from avenra.util import uid


def main():
    postgres = '--postgres' in sys.argv
    dsn = None
    if postgres:
        import psycopg
        from psycopg import sql
        database = 'avenra_verify_' + uid('pg').split('_')[1]
        with psycopg.connect(admin_dsn(ROOT), autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        dsn = make_conninfo(**{**conninfo_to_dict(admin_dsn(ROOT)), 'dbname': database})
    directory = ROOT / '.local/qa' / uid('banking')
    directory.mkdir(parents=True, mode=0o700)
    report = {'passed': False, 'checks': [], 'directory': str(directory)}
    def check(name, condition, detail=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'detail': detail})
        if not condition:
            raise AssertionError(name)
    def rejected(name, callback, code=None):
        try:
            callback()
        except DomainError as error:
            check(name, not code or error.code == code, error.code)
        else:
            check(name, False)
    try:
        app = App(directory, control_dsn=dsn)
        app.bootstrap()
        tenant = 'tenant_avenra'
        with app.store.reader() as db:
            env = db.execute('SELECT id FROM environments WHERE tenant_id=?', (tenant,)).fetchone()[0]
        app.provision(tenant, env)
        b = app.banking
        a = b.bank(tenant, env, {'name': 'Avenra Bank A', 'code': 'bank_a', 'request_key': 'bank-a'})
        c = b.bank(tenant, env, {'name': 'Avenra Bank B', 'code': 'bank_b', 'request_key': 'bank-b'})
        check('Two configurable banks are persisted', len(b.overview(tenant, env)['banks']) == 2)
        check('Identical institution request is idempotent', b.bank(tenant, env, {'name': 'Avenra Bank A', 'code': 'bank_a', 'request_key': 'bank-a'})['id'] == a['id'])
        rejected('Institution payload conflict is rejected', lambda: b.bank(tenant, env, {'name': 'Different', 'code': 'bank_a', 'request_key': 'bank-a'}), 'IDEMPOTENCY_CONFLICT')
        payer_customer = b.customer(tenant, env, {'name': 'Test Payer', 'kind': 'customer', 'request_key': 'payer'})
        merchant_customer = b.customer(tenant, env, {'name': 'Test Merchant', 'kind': 'merchant', 'request_key': 'merchant'})
        payer = b.account(tenant, env, {'bank_id': a['id'], 'customer_id': payer_customer['id'], 'vpa': 'payer@bank_a', 'opening_paise': 500_000, 'request_key': 'payer-account'})['account_id']
        payee = b.account(tenant, env, {'bank_id': c['id'], 'customer_id': merchant_customer['id'], 'vpa': 'merchant@bank_b', 'opening_paise': 100_000, 'request_key': 'merchant-account'})['account_id']
        b.provision_account(tenant, env, payer)
        b.provision_account(tenant, env, payer)
        b.provision_account(tenant, env, payee)
        def balances():
            return {x['id']: x['balance_paise'] for x in app.accounts(tenant, env)}
        check('Opening funds are posted exactly once', balances()[payer] == 500_000 and balances()[payee] == 100_000)
        def payload(key, amount=25_000, fault='none'):
            return {'payer_id': payer, 'payee_id': payee, 'amount_paise': amount, 'request_key': key, 'fault': fault}
        body = payload('concurrent')
        with ThreadPoolExecutor(8) as pool:
            operations = list(pool.map(lambda _: b.submit(tenant, env, body), range(8)))
        identity = operations[0]['id']
        check('Eight concurrent requests create one banking operation', len({x['id'] for x in operations}) == 1)
        def process(_):
            try:
                return b.process(tenant, env, identity)
            except BankingBusy:
                return {'state': 'busy'}
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(process, range(4)))
        op = b.operation(tenant, env, identity)
        check('Interbank transfer confirms debit and credit separately', op['state'] == 'succeeded' and len(op['legs']) == 2 and all(x['state'] == 'confirmed' for x in op['legs']))
        check('Concurrent workers debit and credit exactly once', balances()[payer] == 475_000 and balances()[payee] == 125_000)
        rejected('Operation payload fingerprint detects changed amount', lambda: b.submit(tenant, env, {**body, 'amount_paise': 25_001}), 'IDEMPOTENCY_CONFLICT')
        check('Replay preserves original operation', b.submit(tenant, env, body)['id'] == identity)
        for bad in [True, 1.2, -1, 0]:
            rejected('Invalid banking amount ' + repr(bad), lambda bad=bad: b.submit(tenant, env, payload(uid('invalid'), bad)), 'INVALID_AMOUNT')
        foreign = app.create_environment(tenant, 'Foreign fixture')['id']
        app.provision(tenant, foreign)
        rejected('Account identity cannot cross environments', lambda: b.submit(tenant, foreign, body), 'NOT_FOUND')
        rejected('Operation cannot cross workspaces', lambda: b.operation('tenant_integration', env, identity), 'NOT_FOUND')
        rejected('Bank identity cannot cross environments', lambda: b.account(tenant, foreign, {'bank_id': a['id'], 'customer_id': payer_customer['id'], 'vpa': 'foreign@bank_a', 'request_key': 'foreign', 'opening_paise': 100}), 'NOT_FOUND')
        refund = b.adjust(tenant, env, identity, {'amount_paise': 15_000, 'request_key': 'refund-first'})
        rejected('Pending refunds reserve original refundable amount', lambda: b.adjust(tenant, env, identity, {'amount_paise': 15_000, 'request_key': 'refund-too-large'}), 'ADJUSTMENT_LIMIT')
        b.process(tenant, env, refund['id'])
        check('Partial refund returns funds to original payer', balances()[payer] == 490_000 and balances()[payee] == 110_000)
        check('Refund retry reuses its original identity', b.adjust(tenant, env, identity, {'amount_paise': 15_000, 'request_key': 'refund-first'})['id'] == refund['id'])
        before = balances()
        credit_loss = b.submit(tenant, env, payload('lost-credit', 10_000, 'lost_credit_response'))
        credit_loss = b.process(tenant, env, credit_loss['id'])
        check('Real credit response loss leaves outcome unknown', credit_loss['state'] == 'unknown' and credit_loss['legs'][1]['fault_applied'] == 1)
        check('Credit actually committed despite missing response', balances()[payee] == before[payee] + 10_000)
        recovered = b.process(tenant, env, credit_loss['id'])
        check('Credit recovery confirms existing effect', recovered['state'] == 'succeeded' and balances()[payer] == before[payer] - 10_000 and balances()[payee] == before[payee] + 10_000)
        ledger = app.environment(tenant, env)['ledger_name']
        check('Recovered credit has only one authoritative posting', len(app.accounting.transactions(ledger, recovered['legs'][1]['id'])) == 1)
        before = balances()
        debit_loss = b.submit(tenant, env, payload('lost-debit', 10_000, 'lost_debit_response'))
        debit_loss = b.process(tenant, env, debit_loss['id'])
        check('Real debit response loss stops beneficiary dispatch', debit_loss['state'] == 'unknown' and balances()[payer] == before[payer] - 10_000 and balances()[payee] == before[payee])
        debit_loss = b.process(tenant, env, debit_loss['id'])
        check('Debit recovery does not debit twice', debit_loss['state'] == 'succeeded' and balances()[payer] == before[payer] - 10_000 and balances()[payee] == before[payee] + 10_000)
        before = balances()
        rejected_credit = b.submit(tenant, env, payload('credit-reject', 10_000, 'reject_credit'))
        reversed_op = b.process(tenant, env, rejected_credit['id'])
        check('Definitive beneficiary rejection reverses original debit', reversed_op['state'] == 'reversed' and balances() == before)
        check('Reversal has a separate confirmed financial identity', reversed_op['legs'][2]['kind'] == 'reversal' and reversed_op['legs'][2]['state'] == 'confirmed')
        auth = b.submit(tenant, env, payload('auth-reject', 10_000, 'reject_authorization'))
        check('Authorization rejection creates no financial effect', auth['state'] == 'declined' and not b.operation(tenant, env, auth['id'])['legs'] and balances() == before)
        insufficient = b.submit(tenant, env, payload('insufficient', 99_000_000))
        insufficient = b.process(tenant, env, insufficient['id'])
        check('Insufficient funds decline preserves both balances', insufficient['state'] == 'declined' and balances() == before)
        b.account_state(tenant, env, payer, {'state': 'blocked'})
        rejected('Blocked account rejects a new transfer', lambda: b.submit(tenant, env, payload('blocked')), 'ACCOUNT_UNAVAILABLE')
        check('Blocking account does not invalidate previous request identity', b.submit(tenant, env, body)['id'] == identity)
        b.account_state(tenant, env, payer, {'state': 'active'})
        rule = b.rule(tenant, env, {'name': 'High-value test', 'threshold_paise': 30_000, 'action': 'block'})
        risk = b.submit(tenant, env, payload('risk-block', 35_000))
        check('Published synthetic blocking rule prevents all financial legs', risk['state'] == 'declined' and not b.operation(tenant, env, risk['id'])['legs'])
        b.rule(tenant, env, {'name': 'High-value test', 'threshold_paise': 30_000, 'action': 'alert'})
        risk = b.submit(tenant, env, payload('risk-alert', 35_000))
        risk = b.process(tenant, env, risk['id'])
        check('New rule version alerts without changing prior decision', risk['state'] == 'succeeded' and risk['risk'][0]['version'] == 2 and b.operation(tenant, env, risk['id'])['risk'][0]['matched'] == 1)
        try:
            with app.store.transaction() as db:
                db.execute('UPDATE bank_rules SET threshold_paise=1 WHERE id=?', (rule['id'],))
        except Exception as failure:
            check('Published rule versions cannot be silently modified', isinstance(failure, sqlite3.IntegrityError) or type(failure).__name__ == 'RaiseException')
        else:
            check('Published rule versions cannot be silently modified', False)
        timestamp = time.time()
        mandate = b.mandate(tenant, env, {'payer_id': payer, 'payee_id': payee, 'maximum_paise': 1000, 'starts_at': timestamp - 1, 'expires_at': timestamp + 300, 'request_key': 'mandate-one'})
        rejected('Mandate approval requires explicit synthetic authorization', lambda: b.mandate_action(tenant, env, mandate['id'], 'approve', {}), 'AUTHORIZATION_REQUIRED')
        b.mandate_action(tenant, env, mandate['id'], 'approve', {'synthetic_authorization': True})
        due = {'occurrence': 'installment-1', 'amount_paise': 500, 'due_at': timestamp}
        execution = b.mandate_action(tenant, env, mandate['id'], 'execute', due)
        check('Scheduled mandate occurrence is idempotent', b.mandate_action(tenant, env, mandate['id'], 'execute', due)['id'] == execution['id'])
        rejected('Mandate occurrence conflict is rejected', lambda: b.mandate_action(tenant, env, mandate['id'], 'execute', {**due, 'amount_paise': 501}), 'IDEMPOTENCY_CONFLICT')
        dispatched = b.execute_mandate(tenant, env, execution['id'])
        b.process(tenant, env, dispatched['id'])
        check('Due mandate executes a linked banking operation', b.operation(tenant, env, dispatched['id'])['state'] == 'succeeded')
        future = b.mandate_action(tenant, env, mandate['id'], 'execute', {'occurrence': 'installment-2', 'amount_paise': 500, 'due_at': timestamp + 100})
        b.mandate_action(tenant, env, mandate['id'], 'revoke', {})
        check('Revocation cancels a persisted future execution', b.execute_mandate(tenant, env, future['id'])['state'] == 'cancelled')
        rejected('Revoked mandate cannot schedule another debit', lambda: b.mandate_action(tenant, env, mandate['id'], 'execute', {'occurrence': 'installment-3', 'amount_paise': 500, 'due_at': timestamp + 110}), 'MANDATE_INACTIVE')
        before = balances()
        dispute = b.dispute(tenant, env, {'operation_id': identity, 'reason': 'Merchant delivery review', 'request_key': 'dispute-one'})
        rejected('Dispute cannot skip required review state', lambda: b.dispute_update(tenant, env, dispute['id'], {'state': 'resolved', 'resolution': 'Reviewed'}), 'INVALID_TRANSITION')
        b.dispute_update(tenant, env, dispute['id'], {'state': 'reviewing', 'resolution': 'Investigation opened'})
        b.dispute_update(tenant, env, dispute['id'], {'state': 'resolved', 'resolution': 'Resolved with documented partial refund'})
        check('Dispute workflow does not silently alter original money', balances() == before)
        cycle = b.cycle(tenant, env, {'name': 'First synthetic cycle', 'request_key': 'cycle-one'})
        positions = json.loads(cycle['positions_json'])
        check('Cycle starts prepared with balanced participant obligations', cycle['state'] == 'prepared' and sum(positions.values()) == 0)
        wrong = {**positions, a['id']: positions.get(a['id'], 0) + 1}
        check('External reconciliation discrepancy is retained', b.reconcile_cycle(tenant, env, cycle['id'], {'positions': wrong})['state'] == 'exceptions')
        check('Matching report reconciles the same cycle', b.reconcile_cycle(tenant, env, cycle['id'], {'positions': positions})['state'] == 'reconciled')
        check('Same settlement request does not allocate payments twice', b.cycle(tenant, env, {'name': 'First synthetic cycle', 'request_key': 'cycle-one'})['id'] == cycle['id'])
        second = b.cycle(tenant, env, {'name': 'Next cycle', 'request_key': 'cycle-two'})
        check('Settled operations cannot enter a second cycle', json.loads(second['positions_json']) == {})
        consent = b.consent(tenant, env, {'customer_id': payer_customer['id'], 'account_ids': [payer], 'purpose': 'Underwriting test', 'expires_at': time.time() + 300, 'request_key': 'consent-one'})
        rejected('Unapproved consent cannot release financial data', lambda: b.consent_data(tenant, env, consent['id'], ''), 'CONSENT_INACTIVE')
        consent = b.consent_action(tenant, env, consent['id'], 'approve', {'synthetic_authorization': True})
        data = b.consent_data(tenant, env, consent['id'], consent['access_token'])
        check('Consent releases only authorized accounts with actual balances', len(data['accounts']) == 1 and data['accounts'][0]['id'] == payer and data['accounts'][0]['balance_paise'] == balances()[payer])
        rejected('Forged consent token is rejected', lambda: b.consent_data(tenant, env, consent['id'], 'forged'), 'CONSENT_TOKEN')
        b.consent_action(tenant, env, consent['id'], 'revoke', {})
        rejected('Revocation invalidates previously issued data token', lambda: b.consent_data(tenant, env, consent['id'], consent['access_token']), 'CONSENT_INACTIVE')
        rejected('Consent cannot include another customer account', lambda: b.consent(tenant, env, {'customer_id': payer_customer['id'], 'account_ids': [payee], 'purpose': 'Invalid', 'expires_at': time.time() + 300, 'request_key': 'consent-wrong'}), 'CONSENT_SCOPE')
        plan = {'payer_opening_paise': 50_000, 'payee_opening_paise': 10_000, 'amount_paise': 1000, 'fault': 'lost_credit_response', 'reconcile': True, 'expected_state': 'succeeded', 'expected_payer_paise': 49_000, 'expected_payee_paise': 11_000}
        case = bank_cases.publish(app, tenant, env, {'name': 'Custom credit recovery', 'plan': plan})
        rejected('Scenario plans reject executable extensions', lambda: bank_cases.publish(app, tenant, env, {'name': 'Invalid executable', 'plan': {**plan, 'script': 'raise SystemExit()'}}), 'INVALID_PLAN')
        run = bank_cases.create_run(app, tenant, env, {'case_id': case['id'], 'request_key': 'run-one'})
        check('Run requests preserve identity across retries', bank_cases.create_run(app, tenant, env, {'case_id': case['id'], 'request_key': 'run-one'})['id'] == run['id'])
        run = bank_cases.execute(app, tenant, env, run['id'])
        check('Custom scenario runs against isolated real ledger effects', run['state'] == 'passed', run.get('error'))
        check('Custom scenario evidence integrity verifies', run['evidence']['valid'])
        check('Custom run fixture does not spend interactive funds', balances() == before)
        check('All banking operations preserve audit chain integrity', app.store.verify_audit()['valid'])
        app2 = App(directory, control_dsn=dsn)
        check('Operation and financial identities survive controller recreation', app2.banking.operation(tenant, env, recovered['id'])['legs'] == recovered['legs'])
        check('Evidence survives controller recreation', bank_cases.read_run(app2, tenant, env, run['id'])['evidence']['valid'])
        legacy = directory / 'migration-fixture'
        legacy.mkdir()
        with sqlite3.connect(legacy / 'avenra.db') as db:
            db.executescript((ROOT / 'avenra/schema.sql').read_text())
            db.execute("INSERT INTO tenants VALUES('preserve_me','Original workspace','2026-10-17')")
            db.execute('PRAGMA user_version=1')
        migrated = App(legacy)
        with migrated.store.reader() as db:
            check('Version-one migration preserves existing workspace', db.execute('SELECT name FROM tenants WHERE id=\'preserve_me\'').fetchone()[0] == 'Original workspace' and db.execute('PRAGMA user_version').fetchone()[0] == 10)
        capacity = app.create_environment(tenant, 'Pagination acceptance', fixture_profile='banking')['id']
        app.provision(tenant, capacity)
        capacity_ledger = app.environment(tenant, capacity)['ledger_name']
        app.accounting.post(capacity_ledger, {'postings': [{'source': 'world', 'destination': 'pagination:' + str(i), 'asset': 'INR/2', 'amount': 1} for i in range(111)], 'reference': 'account-capacity'}, 'account-capacity')
        native_accounts = {x['address']: x for x in app.accounting.accounts(capacity_ledger)}
        check('Accounting pagination includes every account beyond the first 100', all('pagination:' + str(i) in native_accounts for i in range(111)))
        for i in range(105):
            app.accounting.post(capacity_ledger, {'postings': [{'source': 'world', 'destination': 'pagination:tail', 'asset': 'INR/2', 'amount': 1}], 'reference': 'history-' + str(i)}, 'history-' + str(i))
        history = app.accounting.transactions(capacity_ledger, all_pages=True)
        check('History pagination retains all 106 distinct transaction references', len(history) == 106 and len({x['reference'] for x in history}) == 106)
        app2.close()
        app.close()
        report['passed'] = True
    except Exception as error:
        report['error'] = str(error)
        report['traceback'] = traceback.format_exc()
    report['application_fingerprint'] = application_fingerprint()
    destination = ROOT / ('.local/banking-postgres-verification.json' if postgres else '.local/banking-verification.json')
    destination.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'report': str(destination), 'error': report.get('error')}))
    if not report['passed']:
        print(report.get('traceback', ''))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
