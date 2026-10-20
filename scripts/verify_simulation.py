#!/usr/bin/env python3
"""Qualify the synthetic multistep lab against actual local financial engines."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import hmac
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.app import App
from avenra.banking import BankingBusy
from avenra.errors import DomainError
from avenra.local_database import admin_dsn
from avenra.simulation import business_time, example_plan
from avenra.util import application_fingerprint, canonical, digest, uid


def main():
    postgres = '--postgres' in sys.argv
    directory = ROOT / '.local/qa' / uid('simulation')
    directory.mkdir(parents=True, mode=0o700)
    report = {'passed': False, 'checks': [], 'directory': str(directory), 'persistence': 'postgresql' if postgres else 'sqlite', 'scope': 'Synthetic lab qualification; no official wire conformance or VM boot asserted.'}
    app, dsn = None, None
    def check(name, condition, detail=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'detail': detail})
        if not condition:
            raise AssertionError(name)
    def rejected(name, callback, code):
        try:
            callback()
        except DomainError as error:
            check(name, error.code == code, error.code)
        else:
            check(name, False)
    try:
        if postgres:
            import psycopg
            from psycopg import sql
            from psycopg.conninfo import conninfo_to_dict, make_conninfo
            database = 'avenra_sim_' + uid('pg').split('_')[1]
            with psycopg.connect(admin_dsn(ROOT), autocommit=True) as conn:
                conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
            dsn = make_conninfo(**{**conninfo_to_dict(admin_dsn(ROOT)), 'dbname': database})
        app = App(directory, control_dsn=dsn)
        app.bootstrap()
        tenant = 'tenant_avenra'
        env = app.create_environment(tenant, 'Institution lab acceptance', 'banking')['id']
        app.provision(tenant, env)
        lab = app.simulation
        profile = lab.profile(tenant, env, {})
        check('Profile installation is idempotent', lab.profile(tenant, env, {})['id'] == profile['id'])
        rejected('Unimplemented official profile cannot be activated', lambda: lab.profile(tenant, env, {'document': {'authority': 'official'}}), 'UNQUALIFIED_PROFILE')
        foreign = app.create_environment(tenant, 'Foreign lab', 'banking')['id']
        app.provision(tenant, foreign)
        rejected('Profile identity cannot cross environments', lambda: lab.publish(tenant, foreign, {'name': 'Foreign', 'profile_id': profile['id'], 'plan': example_plan()}), 'NOT_FOUND')
        def publish(name, plan):
            return lab.publish(tenant, env, {'name': name, 'profile_id': profile['id'], 'plan': plan})
        def create(definition, key=None):
            return lab.create_run(tenant, env, {'plan_id': definition['id'], 'request_key': key or uid('run-key')})
        plan = publish('Recovery and expiry', example_plan())
        check('Published plan hash binds its exact content', plan['plan_hash'] == digest(example_plan()))
        version = publish('Recovery and expiry', example_plan())
        check('Publishing produces a retained new version', version['version'] == plan['version'] + 1)
        run = create(plan, 'same-business-run')
        check('Run replay preserves its original fixture and identity', create(plan, 'same-business-run')['id'] == run['id'])
        rejected('Changed run payload is rejected', lambda: create(version, 'same-business-run'), 'IDEMPOTENCY_CONFLICT')
        result = lab.execute(tenant, env, run['id'])
        check('Full multistep recovery/refund/consent/settlement scenario passes', result['state'] == 'passed', result.get('error'))
        check('All declared steps have retained receipts', len(result['steps']) == 15 and result['cursor'] == 15)
        evidence = result['evidence']
        check('Evidence binds the exact application, profile and plan', evidence['manifest']['application_fingerprint'] == app.source_fingerprint and evidence['manifest']['profile_hash'] == profile['document_hash'] and evidence['manifest']['plan_hash'] == plan['plan_hash'])
        expected_signature = hmac.new(app.store.signing_key, digest(evidence['manifest']).encode(), hashlib.sha256).hexdigest()
        check('Independent evidence digest and signature verification', digest(evidence['manifest']) == evidence['manifest_hash'] and hmac.compare_digest(expected_signature, evidence['signature']))
        check('Sealed execution cannot run financial effects again', lab.execute(tenant, env, run['id'])['evidence']['manifest_hash'] == evidence['manifest_hash'])
        check('Final financial expectations come from observed balances', json.loads(result['steps'][6]['output_json'])['balances'] == {'payer': 70_000, 'merchant': 40_000})
        check('Data disclosure is denied at exact virtual expiry', json.loads(result['steps'][11]['output_json'])['expected_error'] == 'CONSENT_INACTIVE')
        check('Participant messages expire exactly at their deadline', json.loads(result['steps'][12]['output_json'])['reason'] == 'MESSAGE_EXPIRED')
        fixture = run['fixture_environment_id']
        rejected('Run cannot cross workspaces', lambda: lab.read('tenant_integration', env, run['id']), 'NOT_FOUND')
        rejected('Run cannot cross environments', lambda: lab.read(tenant, foreign, run['id']), 'NOT_FOUND')
        for table, identity in [('simulation_profiles', profile['id']), ('simulation_plans', plan['id']), ('simulation_runs', run['id'])]:
            try:
                with app.store.transaction() as db:
                    field = 'document_hash' if table.endswith('profiles') else 'plan_hash' if table.endswith('plans') else 'state'
                    db.execute(f'UPDATE {table} SET {field}=? WHERE id=?', ('tampered', identity))
            except Exception as failure:
                check(table + ' rejects retained-record mutation', any(text in str(failure).lower() for text in ['immutable', 'profile', 'version']))
            else:
                check(table + ' rejects retained-record mutation', False)
        before = business_time(app.store, tenant, fixture)
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda _: lab.clock(tenant, fixture, {'seconds': 5, 'request_key': 'concurrent-clock'}), range(8)))
        check('Concurrent clock replay advances once', business_time(app.store, tenant, fixture) == before + 5)
        rejected('Clock key rejects changed advancement', lambda: lab.clock(tenant, fixture, {'seconds': 6, 'request_key': 'concurrent-clock'}), 'IDEMPOTENCY_CONFLICT')
        rejected('Virtual clock cannot run backwards', lambda: lab.clock(tenant, fixture, {'seconds': -1, 'request_key': 'backwards'}), 'INVALID_SIMULATION')
        rejected('Interactive environments cannot gain an implicit virtual clock', lambda: lab.clock(tenant, env, {'seconds': 5, 'request_key': 'not-fixture'}), 'CLOCK_UNAVAILABLE')
        with app.store.reader() as db:
            local = dict(db.execute('SELECT * FROM simulation_profiles WHERE environment_id=?', (fixture,)).fetchone())
        signed = lab.sign_message(tenant, fixture, {'profile_id': local['id'], 'sender': 'remitter', 'recipient': 'network', 'kind': 'test.response', 'message_key': 'authenticated', 'payload': {'result': 'synthetic'}})
        corrupted = {**signed, 'signature': '0' * 64}
        check('Invalid signature is retained as a rejection', lab.receive_message(tenant, fixture, corrupted)['reason'] == 'SIGNATURE_INVALID')
        accepted = lab.receive_message(tenant, fixture, signed)
        check('Bad authentication cannot reserve a valid message identity', accepted['state'] == 'accepted')
        check('Exact authenticated replay returns original receipt', lab.receive_message(tenant, fixture, signed)['id'] == accepted['id'])
        changed = copy.deepcopy(signed); changed['envelope']['payload'] = {'changed': True}
        changed['signature'] = lab._signature(tenant, fixture, changed['envelope'])
        rejected('Changed authenticated replay is rejected', lambda: lab.receive_message(tenant, fixture, changed), 'IDEMPOTENCY_CONFLICT')
        rejected('Envelope cannot cross environment boundary', lambda: lab.receive_message(tenant, foreign, signed), 'MESSAGE_SCOPE')
        future = lab.sign_message(tenant, fixture, {'profile_id': local['id'], 'sender': 'network', 'recipient': 'payee_psp', 'kind': 'test', 'message_key': 'future', 'payload': {}, 'issued_at': business_time(app.store, tenant, fixture) + 1})
        check('Not-yet-valid message is rejected', lab.receive_message(tenant, fixture, future)['reason'] == 'MESSAGE_NOT_YET_VALID')
        lab.clock(tenant, fixture, {'seconds': 1, 'request_key': 'future-becomes-valid'})
        check('A rejected early attempt does not prevent later valid delivery', lab.receive_message(tenant, fixture, future)['state'] == 'accepted')
        wrong = copy.deepcopy(signed); wrong['envelope']['message_key'] = 'wrong-version'; wrong['envelope']['format'] = 'unknown'
        wrong['signature'] = lab._signature(tenant, fixture, wrong['envelope'])
        check('Unimplemented message version is rejected', lab.receive_message(tenant, fixture, wrong)['reason'] == 'VERSION_UNSUPPORTED')
        malformed = copy.deepcopy(signed); malformed['envelope']['payload'] = {'not_finite': float('nan')}
        rejected('Non-finite message payload is rejected', lambda: lab.receive_message(tenant, fixture, malformed), 'INVALID_MESSAGE')
        boundary = example_plan()
        def step(identity, operation, args, **expect):
            return {'id': identity, 'operation': operation, 'args': args, **expect}
        boundary['steps'] = [
            step('mandate', 'mandate.create', {'payer': 'payer', 'payee': 'merchant', 'maximum_paise': 1000, 'valid_for_seconds': 30}),
            step('approved', 'mandate.approve', {'mandate': 'mandate'}, expect={'state': 'active'}),
            step('scheduled', 'mandate.schedule', {'mandate': 'mandate', 'occurrence': 'first', 'amount_paise': 1000, 'due_after_seconds': 5}),
            step('too_early', 'mandate.dispatch', {'execution': 'scheduled'}, expect_error='NOT_DUE'),
            step('due_clock', 'clock.advance', {'seconds': 5}),
            step('dispatched', 'mandate.dispatch', {'execution': 'scheduled'}, expect={'state': 'succeeded'}),
            step('duplicate', 'mandate.schedule', {'mandate': 'mandate', 'occurrence': 'first', 'amount_paise': 1000, 'due_after_seconds': 0}, expect={'state': 'dispatched'}),
            step('end_clock', 'clock.advance', {'seconds': 25}),
            step('expired', 'mandate.schedule', {'mandate': 'mandate', 'occurrence': 'second', 'amount_paise': 1, 'due_after_seconds': 0}, expect_error='MANDATE_INACTIVE'),
            step('consent', 'consent.create', {'account': 'payer', 'purpose': 'Revocation check', 'valid_for_seconds': 60}),
            step('consented', 'consent.approve', {'consent': 'consent'}),
            step('disclosed', 'consent.read', {'consent': 'consent'}, expect={'accounts': 1}),
            step('revoked', 'consent.revoke', {'consent': 'consent'}, expect={'state': 'revoked'}),
            step('denied', 'consent.read', {'consent': 'consent'}, expect_error='CONSENT_INACTIVE'),
            step('dispute', 'dispute.create', {'operation': 'dispatched', 'reason': 'Synthetic delivery complaint'}),
            step('invalid', 'dispute.resolve', {'dispute': 'dispute', 'resolution': 'Not reviewed'}, expect_error='INVALID_TRANSITION'),
            step('reviewed', 'dispute.review', {'dispute': 'dispute', 'resolution': 'Evidence inspected'}),
            step('resolved', 'dispute.resolve', {'dispute': 'dispute', 'resolution': 'Synthetic case resolved'}, expect={'state': 'resolved'}),
            step('cycle', 'settlement.prepare', {'name': 'Observed reports'}),
            step('mismatch', 'settlement.reconcile', {'cycle': 'cycle', 'positions': {'bank_a': -999, 'bank_b': 999}}, expect={'state': 'exceptions'}),
            step('reconciled', 'settlement.reconcile', {'cycle': 'cycle', 'positions': {'bank_a': -1000, 'bank_b': 1000}}, expect={'state': 'reconciled'}),
            step('money', 'assert.balances', {'balances': {'payer': 99_000, 'merchant': 11_000}}),
        ]
        boundary_run = create(publish('Mandates, revocation and back office', boundary))
        result = lab.execute(tenant, env, boundary_run['id'])
        check('Mandate deadlines, duplicate occurrence, revocation, disputes and independent settlement reports pass', result['state'] == 'passed', result.get('error'))
        check('Entire boundary plan executed', result['cursor'] == len(boundary['steps']))
        aggregate = example_plan()
        aggregate['accounts'] = [{'alias': 'payer', 'bank': 'bank_a', 'opening_paise': 100_000_000}, {'alias': 'merchant', 'bank': 'bank_b', 'opening_paise': 100_000_000}]
        aggregate['steps'] = [step('transfer', 'payment.transfer', {'payer': 'payer', 'payee': 'merchant', 'amount_paise': 1}), step('aggregate', 'assert.balances', {'balances': {'payer': 99_999_999, 'merchant': 100_000_001}})]
        aggregate_result = lab.execute(tenant, env, create(publish('Aggregate balances exceed per-transfer limit', aggregate))['id'])
        check('Balance assertions distinguish aggregate balance from per-transfer limit', aggregate_result['state'] == 'passed', aggregate_result.get('error'))
        fixture_overview = app.banking.overview(tenant, fixture)
        pending = app.banking.submit(tenant, fixture, {'payer_id': fixture_overview['accounts'][0]['account_id'], 'payee_id': fixture_overview['accounts'][1]['account_id'], 'amount_paise': 1, 'request_key': 'forged-status-private-qa'})
        with app.store.transaction() as db:
            db.execute("UPDATE bank_operations SET state='succeeded' WHERE id=?", (pending['id'],))
        rejected('Success flag without financial legs cannot pass independent verification', lambda: lab._perform(tenant, fixture, 'private-qa', step('forged', 'assert.operation', {'operation': 'pending', 'state': 'succeeded'}), {'results': {'pending': {'id': pending['id']}}}), 'FINANCIAL_INVARIANT')
        invalid_plan = example_plan(); invalid_plan['steps'][0]['operation'] = 'execute.arbitrary_script'
        rejected('Unsupported executable operations are denied at publication', lambda: publish('Invalid execution', invalid_plan), 'INVALID_PLAN')
        false_expectation = example_plan(); false_expectation['steps'] = [step('successful', 'clock.advance', {'seconds': 1}, expect_error='ASSERTION_FAILED')]
        failed = lab.execute(tenant, env, create(publish('Missing expected error must fail', false_expectation))['id'])
        check('Missing expected error cannot manufacture a pass', failed['state'] == 'failed' and 'Expected domain error' in failed['error'])
        failing = example_plan(); failing['steps'] = [step('wrong_balance', 'assert.balances', {'balances': {'payer': 1}}), step('unreachable', 'clock.advance', {'seconds': 1})]
        failed = lab.execute(tenant, env, create(publish('Independent wrong balance must fail', failing))['id'])
        check('Wrong financial expectation is retained as a failed run', failed['state'] == 'failed' and failed['cursor'] == 1 and failed['evidence']['valid'])
        checkpointed = create(publish('Interrupted failure remains a failure', failing))
        original_lease, lease_calls = lab._lease, [0]
        def interrupt_before_sealing(*args):
            lease_calls[0] += 1
            if lease_calls[0] == 4:
                raise SystemExit('Controlled interruption after the failed receipt, before sealing')
            return original_lease(*args)
        lab._lease = interrupt_before_sealing
        try:
            lab.execute(tenant, env, checkpointed['id'])
        except SystemExit:
            pass
        lab._lease = original_lease
        recovered_failure = lab.execute(tenant, env, checkpointed['id'])
        check('Interrupted failed receipt cannot turn into a pass on resume', recovered_failure['state'] == 'failed' and recovered_failure['cursor'] == 1 and recovered_failure['evidence']['manifest']['failed_step'] == 'wrong_balance')
        interrupted = example_plan(); interrupted['steps'] = [step('transfer', 'payment.transfer', {'payer': 'payer', 'payee': 'merchant', 'amount_paise': 1000}), step('effect', 'assert.operation', {'operation': 'transfer', 'state': 'succeeded'}), step('balances', 'assert.balances', {'balances': {'payer': 99_000, 'merchant': 11_000}})]
        interrupted_run = create(publish('Interrupted financial handoff', interrupted))
        original = lab._perform
        def interrupt_after_effect(*args):
            value = original(*args)
            raise SystemExit('Controlled interruption after effect, before step receipt')
        lab._perform = interrupt_after_effect
        try:
            lab.execute(tenant, env, interrupted_run['id'])
        except SystemExit:
            pass
        check('Interrupted step did not forge a completed receipt', lab.read(tenant, env, interrupted_run['id'])['cursor'] == 0)
        original_fingerprint = app.source_fingerprint
        app.source_fingerprint = 'different-executor-build'
        rejected('Interrupted experiment cannot silently change executor build', lambda: lab.execute(tenant, env, interrupted_run['id']), 'RUN_VERSION_MISMATCH')
        check('Build incompatibility is visible to clients', bool(lab.read(tenant, env, interrupted_run['id'])['blocking_reason']))
        app.source_fingerprint = original_fingerprint
        app.close()
        app = App(directory, control_dsn=dsn); lab = app.simulation
        resumed = lab.execute(tenant, env, interrupted_run['id'])
        check('New application instance resumes original financial identity without another effect', resumed['state'] == 'passed', resumed.get('error'))
        check('Sealed evidence survives application restart', lab.read(tenant, env, run['id'])['evidence']['manifest_hash'] == evidence['manifest_hash'])
        concurrent = create(publish('Concurrent run lease', interrupted))
        original = lab._perform
        def slow(*args):
            time.sleep(.08)
            return original(*args)
        lab._perform = slow
        def drive(_):
            try:
                return lab.execute(tenant, env, concurrent['id'])['state']
            except BankingBusy:
                return 'busy'
        with ThreadPoolExecutor(4) as pool:
            states = list(pool.map(drive, range(4)))
        check('Concurrent executors respect the persistent run lease', 'passed' in states and all(s in {'passed', 'busy'} for s in states))
        check('Concurrent run retains one receipt per step', len(lab.read(tenant, env, concurrent['id'])['steps']) == 3)
        check('Audit chain remains valid', app.store.verify_audit()['valid'])
        report['passed'] = True
    except Exception as failure:
        report['error'], report['traceback'] = str(failure), traceback.format_exc()
    finally:
        if app:
            app.close()
    report['application_fingerprint'] = application_fingerprint()
    target = ROOT / '.local' / ('simulation-postgres-verification.json' if postgres else 'simulation-verification.json')
    content = json.dumps(report, indent=2) + '\n'
    target.write_text(content)
    attempts = ROOT / '.local/verification-attempts'; attempts.mkdir(exist_ok=True)
    (attempts / ('simulation-' + str(time.time_ns()) + '.json')).write_text(content)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'report': str(target), 'error': report.get('error')}))
    if not report['passed']:
        print(report.get('traceback', ''))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
