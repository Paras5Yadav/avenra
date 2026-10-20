#!/usr/bin/env python3
"""Install a synthetic lifecycle journey into the existing Avenra application."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.app import App
from avenra.errors import DomainError, require
from avenra.synthetic_lab import fixture, provision, start, stop, connect, prepare, advance
from avenra.util import now, uid
from avenra_fidelity.profile import fingerprint

DATA = ROOT / '.local/data'
STATE = DATA / 'fidelity-demo.json'


def owned(entry):
    result = subprocess.run(['ps', '-p', str(entry['pid']), '-o', 'command='], capture_output=True, text=True)
    return result.returncode == 0 and 'avenra.lab_bank' in result.stdout and entry['config'] in result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'status', 'stop'])
    args = parser.parse_args()
    if args.action in {'status', 'stop'}:
        if not STATE.exists(): print(json.dumps({'installed': False}));return 0
        state = json.loads(STATE.read_text())
        for worker in state['workers']:
            worker['running'] = owned(worker)
            if args.action == 'stop' and worker['running']:
                os.kill(worker['pid'], signal.SIGTERM)
        if args.action == 'stop':
            state['services_stopped_at'] = now();STATE.write_text(json.dumps(state, indent=2));STATE.chmod(0o600)
        print(json.dumps({k: state[k] for k in ['environment_id', 'environment_name', 'workers']}));return 0
    require(not STATE.exists(), 'DEMO_EXISTS', 'The retained lifecycle demo already exists. Inspect its status; existing state will not be replaced.', 409)
    for filename in ['fidelity-verification.json', 'fidelity-postgres-verification.json']:
        report = json.loads((ROOT / '.local' / filename).read_text())
        require(report['passed'] and report['application_fingerprint'] == fingerprint(), 'QUALIFICATION_REQUIRED', 'Run both current lifecycle acceptance suites before installing the demonstration.', 409)
    app = App(DATA, control_dsn=(DATA / 'control.dsn').read_text().strip())
    processes, keys = [], []
    result = {'passed': False, 'checks': [], 'fingerprint': fingerprint(), 'official_conformance': False}
    def check(name, value, requirements):
        result['checks'].append({'name': name, 'passed': bool(value), 'requirements': requirements.split()})
        require(value, 'DEMO_ASSERTION', name, 409)
    try:
        keys = [app.create_api_key('tenant_avenra', 'Synthetic lifecycle demo ' + role, expires_at=time.time() + 3600) for role in ['maker', 'checker']]
        opener = build_opener(ProxyHandler({}))
        def api(path, body=None, checker=False):
            headers = {'Authorization': 'Bearer ' + keys[1 if checker else 0], 'Content-Type': 'application/json'}
            request = Request('http://127.0.0.1:4827' + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method='POST' if body is not None else 'GET')
            with opener.open(request, timeout=10) as response: return json.loads(response.read())
        value = fixture(app);tenant, env = value['tenant_id'], value['environment_id'];lab, u = app.fidelity, app.upi
        name = 'UPI lifecycle rehearsal ' + env[-6:]
        with app.store.transaction() as db:
            db.execute('UPDATE environments SET name=? WHERE id=?', (name, env))
            app.store.audit(db, tenant, env, 'fidelity.demo_named', env, {'name': name})
            db.execute('INSERT INTO simulation_clocks VALUES(?,?,?,0)', (env, tenant, float(int(time.time()))))
        result.update(environment_id=env, environment_name=name)
        prefix = '/api/tenants/' + tenant + '/environments/' + env + '/banking/fidelity'
        api(prefix + '/profile', {})
        check('The existing application enables its integrated lifecycle API', api(prefix)['enabled'], 'PROFILE HTTP')
        workers = provision(app, value)
        for worker in workers: processes.append(start(app, worker))
        connect(app, value, workers)
        for i, pin in enumerate(['739152', '651804']):
            c = lab.challenge(tenant, env, {'device_id': value['devices'][i], 'account_id': value['accounts'][i], 'device_proof': value['proofs'][i], 'purpose': 'setup', 'request_key': uid('setup'), 'synthetic_bank_verified': True})
            lab.complete_credential(tenant, env, c['id'], {'device_proof': value['proofs'][i], 'synthetic_challenge': c['synthetic_challenge'], 'new_synthetic_pin': pin})
        def approve(payment, i=0):
            deadline = time.monotonic() + 15
            while True:
                try:
                    return api(prefix + '/payments/' + payment['id'] + '/authorize', {'device_id': value['devices'][i], 'device_proof': value['proofs'][i], 'synthetic_pin': ['739152', '651804'][i], 'approved': True})
                except Exception as error:
                    if getattr(error, 'code', None) != 409 or time.monotonic() >= deadline: raise
                    time.sleep(.1)
        def clock(seconds): return app.simulation.clock(tenant, env, {'seconds': seconds, 'request_key': uid('clock')})
        payment = prepare(app, value, 50000, mode='collect', financial_fault='lost_credit_response')
        advance(app, value, payment, predicate=lambda p: any(m['step'] == 0 and m['receipt_json'] for m in p['messages']))
        approve(payment);advance(app, value, payment, 'unknown');clock(1);u.reconcile(tenant, env, payment['id'], {});done = advance(app, value, payment)
        check('Credential-authorized merchant collect recovers the original TLS credit', done['state'] == 'succeeded' and done['evidence']['valid'], 'AUTH RECOVERY EVIDENCE')
        refund = u.refund(tenant, env, payment['id'], {'amount_paise': 10000, 'request_key': uid('refund')});approve(refund, 1);returned = advance(app, value, refund)
        check('A separately approved partial refund returns through the participant engine', returned['state'] == 'succeeded', 'AUTH EVIDENCE')
        mandate = api(prefix + '/mandates', {'payer_id': value['accounts'][0], 'payee_id': value['accounts'][1], 'device_id': value['devices'][0], 'maximum_paise': 10000, 'interval_seconds': 60, 'starts_at': lab.time(tenant, env) + 60, 'occurrences': 3, 'request_key': uid('mandate')})
        api(prefix + '/mandates/' + mandate['id'] + '/approve', {'device_proof': value['proofs'][0], 'synthetic_pin': '739152'})
        occurrence = api(prefix + '/mandates/' + mandate['id'] + '/schedule', {'occurrence': 0, 'amount_paise': 5000, 'request_key': uid('schedule')});clock(60)
        occurrence = lab.execute_occurrence(tenant, env, occurrence['id']);recurring = advance(app, value, {'id': occurrence['payment_id']});lab.execute_occurrence(tenant, env, occurrence['id'])
        check('An approved due agreement executes one signed recurring participant payment', recurring['state'] == 'succeeded', 'MANDATE OCCURRENCE')
        case = api(prefix + '/complaints', {'payment_id': payment['id'], 'reason': 'status_mismatch', 'request_key': uid('complaint')})
        api(prefix + '/complaints/' + case['id'] + '/investigate', {})
        proposal = api(prefix + '/complaints/' + case['id'] + '/propose', {'resolution': 'no_financial_error', 'request_key': uid('resolution')})
        approved = api(prefix + '/proposals/' + proposal['id'] + '/approve', {}, checker=True)
        check('Distinct authenticated API principals resolve the observed complaint', approved['state'] == 'approved', 'COMPLAINT RESOLUTION HTTP')
        clock(1)
        cycle = api(prefix + '/cycles', {'cutoff': lab.time(tenant, env), 'request_key': uid('cycle')})
        expected_positions = {value['banks'][0]['id']: -45000, value['banks'][1]['id']: 45000}
        check('Synthetic cycle independently totals payment, refund and recurring execution', cycle['manifest']['positions'] == expected_positions and len(cycle['manifest']['items']) == 3, 'CYCLE EVIDENCE')
        api(prefix + '/cycles/' + cycle['id'] + '/reconcile', {'observed_positions': expected_positions, 'request_key': uid('reconcile')})
        release = api(prefix + '/cycles/' + cycle['id'] + '/propose', {'request_key': uid('release')})
        released = api(prefix + '/proposals/' + release['id'] + '/approve', {}, checker=True)
        check('A second authenticated API principal releases the matched synthetic report', released['state'] == 'approved', 'RECONCILE RELEASE HTTP')
        balances = {row['id']: row['balance_paise'] for row in app.accounts(tenant, env)}
        check('Original native balances equal the complete fixture oracle', balances[value['accounts'][0]] == 955000 and balances[value['accounts'][1]] == 145000, 'EVIDENCE')
        check('The installation audit chain remains valid', app.store.verify_audit()['valid'], 'EVIDENCE')
        result['passed'] = True
        result.update(payment_id=payment['id'], cycle_id=cycle['id'], workers=[{'pid': process.pid, 'config': worker['config'], 'endpoint': worker['endpoint'], 'worker_id': worker['worker_id']} for process, worker in zip(processes, workers)])
        # This demo retains its own observed cases. Comprehensive suite cases are
        # not relabelled as if they ran in this environment.
        lab.record_acceptance(tenant, env, result['checks'])
        STATE.write_text(json.dumps(result, indent=2) + '\n');STATE.chmod(0o600)
    except Exception as error:
        result.update(error_type=type(error).__name__, error=str(error))
        for process in processes: stop(process)
        attempt = ROOT / '.local/verification-attempts' / ('fidelity-demo-' + str(time.time_ns()) + '.json')
        attempt.write_text(json.dumps(result, indent=2));attempt.chmod(0o600)
        raise
    finally:
        try:
            if keys:
                with app.store.transaction() as db:
                    for token in keys: db.execute('UPDATE api_keys SET revoked_at=? WHERE token_hash=?', (now(), hashlib.sha256(token.encode()).hexdigest()))
                    app.store.audit(db, 'tenant_avenra', result.get('environment_id'), 'fidelity.demo_service_keys_revoked', result.get('environment_id') or 'fidelity-demo', {'count': len(keys)})
        finally: app.close()
    print(json.dumps({'passed': result['passed'], 'checks': len(result['checks']), 'environment_id': result['environment_id'], 'environment_name': result['environment_name'], 'url': 'http://127.0.0.1:4827/', 'report': str(STATE)}));return 0


if __name__ == '__main__': raise SystemExit(main())
