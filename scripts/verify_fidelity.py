#!/usr/bin/env python3
"""Qualify the integrated synthetic lifecycle against actual local accounting/TLS."""
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.app import App
from avenra.errors import DomainError
from avenra.local_database import admin_dsn
from avenra.synthetic_lab import fixture, provision, start, stop, connect, prepare, advance
from avenra.util import canonical, uid
from avenra_fidelity.profile import PROFILE, PROFILE_HASH, REQUIREMENTS, fingerprint, case_pack_hash
from avenra_fidelity.runtime import TABLES
from avenra_fidelity.archive import encrypt_snapshot, recovery_key, restore_snapshot


def main():
    postgres = '--postgres' in sys.argv
    directory = ROOT / '.local/qa' / uid('fidelity')
    directory.mkdir(mode=0o700, parents=True)
    report = {'passed': False, 'checks': [], 'directory': str(directory), 'scope': PROFILE['scope'], 'official_conformance': False, 'production_ready': False, 'fingerprint_at_start': fingerprint(), 'case_pack_sha256': case_pack_hash()}
    app = restored = http_process = None
    bank_processes = []
    dsn = None

    def check(name, condition, requirements, detail=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'requirements': requirements.split(), 'detail': detail})
        if not condition: raise AssertionError(name)

    def denied(name, callback, code, requirements):
        try: callback()
        except DomainError as error: check(name, error.code == code, requirements, error.code)
        else: check(name, False, requirements)

    try:
        if postgres:
            import psycopg
            from psycopg import sql
            from psycopg.conninfo import conninfo_to_dict, make_conninfo
            database = 'avenra_fidelity_' + uid('pg').split('_')[1]
            with psycopg.connect(admin_dsn(ROOT), autocommit=True) as db:
                db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
            dsn = make_conninfo(**{**conninfo_to_dict(admin_dsn(ROOT)), 'dbname': database})
            (directory / 'control.dsn').write_text(dsn);(directory / 'control.dsn').chmod(0o600)
        app = App(directory, control_dsn=dsn)
        value = fixture(app);tenant, env = value['tenant_id'], value['environment_id']
        report.update(tenant_id=tenant, environment_id=env)
        u, lab = app.upi, app.fidelity
        expected = [1_000_000, 100_000]
        net = 0
        pin, merchant_pin = '739152', '651804'

        def clock(seconds): return app.simulation.clock(tenant, env, {'seconds': seconds, 'request_key': uid('clock')})
        def approve(payment, index=0, credential=None):
            return lab.authorize_payment(tenant, env, payment['id'], {'device_id': value['devices'][index], 'device_proof': value['proofs'][index], 'synthetic_pin': credential or (pin if index == 0 else merchant_pin), 'approved': True})
        def exact_balances():
            observed = {r['id']: r['balance_paise'] for r in app.accounts(tenant, env)}
            return all(observed[a] == expected[i] for i, a in enumerate(value['accounts']))
        def complete(payment, amount, index=0, state='succeeded'):
            nonlocal net
            result = advance(app, value, payment)
            check('Native participant outcome ' + payment['id'], result['state'] == state and result['evidence']['valid'], 'AUTH EVIDENCE')
            if state == 'succeeded':
                expected[index] -= amount;expected[1-index] += amount
                if result['id'] != previous['id']:
                    net += amount if index == 0 else -amount
            check('Exact independent opening-funds oracle ' + payment['id'], exact_balances(), 'EVIDENCE RECOVERY')
            return result
        def challenge(index=0, purpose='setup'):
            return lab.challenge(tenant, env, {'device_id': value['devices'][index], 'account_id': value['accounts'][index], 'device_proof': value['proofs'][index], 'purpose': purpose, 'request_key': uid('challenge'), 'synthetic_bank_verified': True})
        def finish(c, new_pin, index=0):
            return lab.complete_credential(tenant, env, c['id'], {'device_proof': value['proofs'][index], 'synthetic_challenge': c['synthetic_challenge'], 'new_synthetic_pin': new_pin})

        # An accepted intent predating enablement retains its original workflow.
        previous = prepare(app, value, 1000)
        with app.store.transaction() as db: db.execute('INSERT INTO simulation_clocks VALUES(?,?,?,0)', (env, tenant, 1_700_000_000.0))
        with patch('avenra_fidelity.runtime.now', return_value=previous['created_at']):
            lab.enable(tenant, env, {})
        with app.store.reader() as db: enabled_wall = db.execute('SELECT activated_at FROM fidelity_profiles WHERE environment_id=?', (env,)).fetchone()[0]
        u.authorize(tenant, env, previous['id'], {'device_id': value['devices'][0], 'device_proof': value['proofs'][0], 'approved': True})
        # Its real-time expiry is later than this explicitly selected virtual clock.
        complete(previous, 1000)
        check('Enabling the profile preserves old intents even with an identical wall timestamp', previous['created_at'] == enabled_wall, 'PRESERVE PROFILE')
        check('Profile replay preserves its immutable hash', lab.enable(tenant, env, {})['profile_hash'] == PROFILE_HASH, 'PROFILE')
        found = lab.discover(tenant, env, {'device_id': value['devices'][0], 'device_proof': value['proofs'][0]})
        check('Discovery returns only this proved customer account', [x['account_id'] for x in found['accounts']] == [value['accounts'][0]], 'DISCOVERY SCOPE')
        denied('Discovery refuses another device proof', lambda: lab.discover(tenant, env, {'device_id': value['devices'][1], 'device_proof': value['proofs'][0]}), 'DEVICE_PROOF', 'DISCOVERY SCOPE')
        denied('Cross-workspace discovery is refused', lambda: lab.discover('tenant_integration', env, {'device_id': value['devices'][0], 'device_proof': value['proofs'][0]}), 'NOT_FOUND', 'SCOPE')
        bad = challenge()
        for _ in range(3):
            denied('Wrong issuer challenge consumes a retained attempt', lambda: lab.complete_credential(tenant, env, bad['id'], {'device_proof': value['proofs'][0], 'synthetic_challenge': 'not-the-code', 'new_synthetic_pin': pin}), 'CHALLENGE_INVALID', 'CHALLENGE')
        denied('Exhausted issuer challenge stays locked', lambda: finish(bad, pin), 'CHALLENGE_LOCKED', 'CHALLENGE')
        expired = challenge();clock(120)
        denied('Issuer challenge expires at the exact boundary', lambda: finish(expired, pin), 'CHALLENGE_EXPIRED', 'CHALLENGE')
        c = challenge();credential = finish(c, pin)
        check('Setup completion is idempotent', finish(c, pin)['epoch'] == credential['epoch'] == 1, 'CREDENTIAL CHALLENGE')
        denied('A completed challenge rejects substituted credential content', lambda: finish(c, '124890'), 'CREDENTIAL_CONFLICT', 'CHALLENGE')
        finish(challenge(1), merchant_pin, 1)
        with app.store.reader() as db: row = db.execute('SELECT * FROM fidelity_credentials WHERE account_id=?', (value['accounts'][0],)).fetchone()
        check('Credential storage uses a random salt and scrypt hash', len(row['salt']) == 32 and len(row['pin_hash']) == 64 and row['pin_hash'] != pin, 'CREDENTIAL')
        payment = prepare(app, value, 1000)
        denied('Direct core authorization cannot bypass enabled lifecycle checks', lambda: u.authorize(tenant, env, payment['id'], {'device_id': value['devices'][0], 'device_proof': value['proofs'][0], 'approved': True}), 'CREDENTIAL_REQUIRED', 'AUTH')
        for number in range(3):
            denied('Wrong synthetic PIN persists failure ' + str(number + 1), lambda: approve(payment, credential='000000'), 'CREDENTIAL_LOCKED' if number == 2 else 'CREDENTIAL_INVALID', 'CREDENTIAL AUTH')
        clock(299)
        denied('PIN remains locked immediately before its release boundary', lambda: approve(payment), 'CREDENTIAL_LOCKED', 'CREDENTIAL')
        clock(1)
        # The first intent expired during lockout. It must not gain a late debit.
        expired_result = approve(payment)
        check('A correct PIN cannot revive an expired intent', expired_result['state'] == 'expired' and exact_balances(), 'AUTH CREDENTIAL')
        payment = prepare(app, value, 2000);approve(payment);complete(payment, 2000)
        pending = prepare(app, value, 2000);approve(pending)
        reset_old, reset_new = challenge(purpose='reset'), challenge(purpose='reset')
        pin = '628405';reset = finish(reset_new, pin)
        check('Reset advances the credential epoch and starts cooling', reset['epoch'] == 2 and reset['cooling_until'] == lab.time(tenant, env) + 60, 'RESET')
        denied('An older outstanding reset challenge cannot replace the new credential', lambda: finish(reset_old, '124890'), 'CHALLENGE_SUPERSEDED', 'RESET CHALLENGE')
        complete(pending, 0, state='declined')
        cooling = prepare(app, value, 1000)
        denied('Reset cooling blocks a new approval', lambda: approve(cooling), 'CREDENTIAL_COOLING', 'RESET AUTH')
        clock(60);approve(cooling);complete(cooling, 1000)

        workers = provision(app, value)
        for worker in workers: bank_processes.append(start(app, worker))
        revisions = connect(app, value, workers)
        check('The same lifecycle uses four real mutual-TLS bank-role adapters', len(revisions) == 4, 'AUTH EVIDENCE')
        lost = prepare(app, value, 5000, financial_fault='lost_credit_response');approve(lost)
        advance(app, value, lost, 'unknown')
        with app.store.reader() as db: guard = dict(db.execute('SELECT * FROM fidelity_guards WHERE payment_id=?', (lost['id'],)).fetchone())
        app.close();app = App(directory, control_dsn=dsn);u, lab = app.upi, app.fidelity
        clock(1);u.reconcile(tenant, env, lost['id'], {})
        complete(lost, 5000)
        with app.store.reader() as db: replay_guard = dict(db.execute('SELECT * FROM fidelity_guards WHERE payment_id=?', (lost['id'],)).fetchone())
        check('Process recreation retains the exact approval and financial intent', guard == replay_guard, 'RECOVERY AUTH')
        refund = u.refund(tenant, env, lost['id'], {'request_key': uid('refund'), 'amount_paise': 1000});approve(refund, 1);complete(refund, 1000, 1)

        clock(86400)
        concurrent = [prepare(app, value, 150_000) for _ in range(8)]
        def try_approval(p):
            try: approve(p);return p
            except DomainError as error:
                if error.code != 'DAILY_LIMIT': raise
                return None
        with ThreadPoolExecutor(max_workers=8) as pool: accepted = [p for p in pool.map(try_approval, concurrent) if p]
        check('Concurrent approvals atomically reserve the daily amount budget', len(accepted) == 3, 'LIMIT')
        with ThreadPoolExecutor(max_workers=8) as pool: list(pool.map(lambda _: approve(accepted[0]), range(8)))
        with app.store.reader() as db: count = db.execute('SELECT count(*) FROM fidelity_guards WHERE payment_id=?', (accepted[0]['id'],)).fetchone()[0]
        check('Concurrent authorization retries reserve one budget entry', count == 1, 'LIMIT RECOVERY')
        large = prepare(app, value, 200_001)
        denied('Per-payment limit is enforced independently of daily budget', lambda: approve(large), 'PAYMENT_LIMIT', 'LIMIT')
        uncertain = prepare(app, value, 50_000, financial_fault='lost_debit_response');approve(uncertain);advance(app, value, uncertain, 'unknown')
        extra = prepare(app, value, 1)
        denied('Uncertain financial effects retain their daily reservation', lambda: approve(extra), 'DAILY_LIMIT', 'LIMIT RECOVERY')
        clock(1);u.reconcile(tenant, env, uncertain['id'], {});complete(uncertain, 50_000)
        for p in accepted: complete(p, 150_000)
        clock(86400)
        def agreement():
            return lab.mandate(tenant, env, {'payer_id': value['accounts'][0], 'payee_id': value['accounts'][1], 'device_id': value['devices'][0], 'maximum_paise': 10_000, 'interval_seconds': 60, 'starts_at': lab.time(tenant, env) + 10, 'occurrences': 3, 'request_key': uid('agreement')})
        def mandate_action(m, action): return lab.mandate_action(tenant, env, m['id'], action, {'device_proof': value['proofs'][0], 'synthetic_pin': pin})
        mandate = agreement()
        denied('An unapproved agreement cannot schedule payments', lambda: lab.schedule(tenant, env, mandate['id'], {'occurrence': 0, 'amount_paise': 1000, 'request_key': uid('occurrence')}), 'MANDATE_INACTIVE', 'MANDATE')
        mandate_action(mandate, 'approve')
        scheduled_body = {'occurrence': 0, 'amount_paise': 1000, 'request_key': uid('occurrence')}
        execution = lab.schedule(tenant, env, mandate['id'], scheduled_body)
        check('Scheduled occurrence replay retains one identity', lab.schedule(tenant, env, mandate['id'], scheduled_body)['id'] == execution['id'], 'OCCURRENCE')
        denied('Changed occurrence amount is refused', lambda: lab.schedule(tenant, env, mandate['id'], {'occurrence': 0, 'amount_paise': 2000, 'request_key': uid('other')}), 'OCCURRENCE_CONFLICT', 'OCCURRENCE')
        denied('An occurrence cannot execute before due time', lambda: lab.execute_occurrence(tenant, env, execution['id']), 'NOT_DUE', 'OCCURRENCE')
        denied('An occurrence cannot exceed its mandate maximum', lambda: lab.schedule(tenant, env, mandate['id'], {'occurrence': 1, 'amount_paise': 10001, 'request_key': uid('over')}), 'MANDATE_LIMIT', 'MANDATE OCCURRENCE')
        mandate_action(mandate, 'pause');clock(10)
        check('A paused agreement does not dispatch its due occurrence', lab.execute_occurrence(tenant, env, execution['id'])['payment_id'] is None, 'REVOCATION')
        mandate_action(mandate, 'resume')
        with ThreadPoolExecutor(max_workers=4) as pool: results = list(pool.map(lambda _: lab.execute_occurrence(tenant, env, execution['id']), range(4)))
        check('Competing schedulers accept one participant payment', len({r['payment_id'] for r in results}) == 1, 'OCCURRENCE RECOVERY')
        mandate_action(mandate, 'revoke')
        complete({'id': results[0]['payment_id']}, 0, state='declined');lab.tick()
        check('Revocation before first debit leaves funds unchanged', exact_balances(), 'REVOCATION')
        m2 = agreement();mandate_action(m2, 'approve')
        e2 = lab.schedule(tenant, env, m2['id'], {'occurrence': 0, 'amount_paise': 2000, 'request_key': uid('occurrence')});clock(10)
        e2 = lab.execute_occurrence(tenant, env, e2['id'])
        stop(bank_processes[1]);advance(app, value, {'id': e2['payment_id']}, 'unknown')
        mandate_action(m2, 'revoke');bank_processes[1] = start(app, workers[1]);clock(1)
        u.reconcile(tenant, env, e2['payment_id'], {});complete({'id': e2['payment_id']}, 2000);lab.tick()
        check('Revoking an uncertain mandate payment does not discard original recovery', lab.execute_occurrence(tenant, env, e2['id'])['state'] == 'succeeded', 'REVOCATION RECOVERY')
        with app.store.reader() as db: notices = db.execute('SELECT * FROM fidelity_notifications WHERE resource_id=?', (e2['id'],)).fetchall()
        check('Pre-notification and final result have signed retained identities', len(notices) == 2 and all(lab.sign(json.loads(n['document_json'])) == (n['document_hash'], n['signature']) for n in notices), 'OCCURRENCE EVIDENCE')

        late = prepare(app, value, 3000, financial_fault='lost_credit_response');approve(late);advance(app, value, late, 'unknown')
        complaint = lab.complaint(tenant, env, {'payment_id': late['id'], 'reason': 'status_mismatch', 'request_key': uid('complaint')}, 'maker-test')
        lab.investigate(tenant, env, complaint['id'], {}, 'maker-test')
        denied('Complaint cannot invent a resolution for an uncertain payment', lambda: lab.propose_resolution(tenant, env, complaint['id'], {'resolution': 'no_financial_error', 'request_key': uid('resolution')}, 'maker-test'), 'RECOVERY_REQUIRED', 'COMPLAINT RESOLUTION RECOVERY')
        clock(1)
        cycle = lab.cycle(tenant, env, {'cutoff': lab.time(tenant, env), 'request_key': uid('cycle')}, 'maker-test')
        expected_positions = {value['banks'][0]['id']: -net, value['banks'][1]['id']: net}
        check('Cycle positions equal independently accumulated synthetic flows', cycle['manifest']['positions'] == expected_positions, 'CYCLE EVIDENCE')
        check('Unresolved committed payment remains outside allocated settlement items', any(p['payment_id'] == late['id'] for p in cycle['manifest']['unresolved']) and all(p['payment_id'] != late['id'] for p in cycle['manifest']['items']), 'CYCLE RECOVERY')
        mismatch = lab.reconcile(tenant, env, cycle['id'], {'observed_positions': {}, 'request_key': uid('mismatch')}, 'maker-test')
        check('Missing independent observations are retained as exceptions', len(json.loads(mismatch['exceptions_json'])) == 2, 'RECONCILE')
        denied('An unmatched report cannot be released', lambda: lab.propose_release(tenant, env, cycle['id'], {'request_key': uid('release')}, 'maker-test'), 'RECONCILIATION_REQUIRED', 'RECONCILE RELEASE')
        lab.reconcile(tenant, env, cycle['id'], {'observed_positions': expected_positions, 'request_key': uid('match')}, 'maker-test')
        release = lab.propose_release(tenant, env, cycle['id'], {'request_key': uid('release')}, 'maker-test')
        denied('A maker cannot approve its own settlement release', lambda: lab.approve(tenant, env, release['id'], {}, 'maker-test'), 'SECOND_PRINCIPAL_REQUIRED', 'RELEASE')
        check('A distinct principal releases the signed matched report', lab.approve(tenant, env, release['id'], {}, 'checker-test')['state'] == 'approved', 'RELEASE')
        denied('A released report cannot be released a second time', lambda: lab.propose_release(tenant, env, cycle['id'], {'request_key': uid('again')}, 'maker-test'), 'CYCLE_RELEASED', 'RELEASE')
        exported = list(csv.DictReader(io.StringIO(lab.csv_report(tenant, env, cycle['id']).decode())))
        check('CSV report preserves exact allocated identities and integer amounts', len(exported) == len(cycle['manifest']['items']) and all(r['amount_paise'].isdigit() for r in exported), 'CYCLE EVIDENCE')
        u.reconcile(tenant, env, late['id'], {});complete(late, 3000);clock(1)
        cycle2 = lab.cycle(tenant, env, {'cutoff': lab.time(tenant, env), 'request_key': uid('cycle')}, 'maker-test')
        check('Late completion is allocated once in a subsequent cycle', len(cycle2['manifest']['items']) == 1 and cycle2['manifest']['items'][0]['payment_id'] == late['id'] and cycle2['manifest']['items'][0]['late'], 'CYCLE RECOVERY')
        pos2 = {value['banks'][0]['id']: -3000, value['banks'][1]['id']: 3000}
        lab.reconcile(tenant, env, cycle2['id'], {'observed_positions': pos2, 'request_key': uid('match')}, 'maker-test')
        stale = lab.propose_release(tenant, env, cycle2['id'], {'request_key': uid('release')}, 'maker-test')
        lab.reconcile(tenant, env, cycle2['id'], {'observed_positions': {}, 'request_key': uid('changed')}, 'maker-test')
        denied('Changed reconciliation invalidates a pending release proposal', lambda: lab.approve(tenant, env, stale['id'], {}, 'checker-test'), 'PROPOSAL_STALE', 'RELEASE RECONCILE')
        clock(PROFILE['rules']['complaint_seconds']);lab.tick()
        check('Unresolved complaint deadline creates retained escalation', lab.complaint_read(tenant, env, complaint['id'])['escalated'] == 1, 'COMPLAINT')
        proposal = lab.propose_resolution(tenant, env, complaint['id'], {'resolution': 'no_financial_error', 'request_key': uid('resolution')}, 'maker-test')
        denied('A maker cannot close its own complaint proposal', lambda: lab.approve(tenant, env, proposal['id'], {}, 'maker-test'), 'SECOND_PRINCIPAL_REQUIRED', 'RESOLUTION')
        lab.approve(tenant, env, proposal['id'], {}, 'checker-test')
        check('Observed financial evidence and a second principal close the complaint', lab.complaint_read(tenant, env, complaint['id'])['state'] == 'resolved', 'COMPLAINT RESOLUTION')
        partial_case = lab.complaint(tenant, env, {'payment_id': lost['id'], 'reason': 'refund_pending', 'request_key': uid('complaint')}, 'maker-test');lab.investigate(tenant, env, partial_case['id'], {}, 'maker-test')
        denied('A partial refund cannot be represented as a full refund resolution', lambda: lab.propose_resolution(tenant, env, partial_case['id'], {'resolution': 'refunded', 'request_key': uid('resolution')}, 'maker-test'), 'REFUND_NOT_CONFIRMED', 'RESOLUTION')
        try:
            with app.store.transaction() as db: db.execute('UPDATE fidelity_guards SET amount_paise=1 WHERE payment_id=?', (lost['id'],))
        except Exception as error: check('Database guards prevent rewriting accepted lifecycle approvals', type(error).__name__ in {'IntegrityError', 'RaiseException'}, 'AUTH EVIDENCE')
        else: check('Database guards prevent rewriting accepted lifecycle approvals', False, 'AUTH')

        # HTTP uses the same app/ASGI module, on a temporary test port only.
        with socket.socket() as port: port.bind(('127.0.0.1', 0));number = port.getsockname()[1]
        log = (directory / 'http.log').open('wb');os.chmod(directory / 'http.log', 0o600)
        http_process = subprocess.Popen([sys.executable, '-m', 'avenra.asgi', '--data', str(directory), '--port', str(number)], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        opener = build_opener(ProxyHandler({}));origin = 'http://127.0.0.1:' + str(number)
        deadline = time.monotonic() + 15
        while True:
            try:
                with opener.open(origin + '/api/health', timeout=1) as r:
                    if r.status == 200: break
            except OSError:
                if time.monotonic() >= deadline: raise TimeoutError('Integrated HTTP server did not start.')
                time.sleep(.1)
        writer = app.create_api_key(tenant, 'Lifecycle verifier maker')
        checker = app.create_api_key(tenant, 'Lifecycle verifier checker')
        reader = app.create_api_key(tenant, 'Lifecycle verifier read only', ['read'])
        base = '/api/tenants/' + tenant + '/environments/' + env + '/banking/fidelity'
        def request(method, path, body=None, token=writer, headers=None, raw=None):
            head = {'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + token} if token else {}), **(headers or {})}
            encoded = raw if raw is not None else json.dumps(body).encode() if body is not None else None
            req = Request(origin + path, data=encoded, method=method, headers=head)
            try:
                with opener.open(req, timeout=8) as response: return response.status, json.loads(response.read())
            except HTTPError as error: return error.code, json.loads(error.read())
        status, overview = request('GET', base)
        check('Current application serves the lifecycle through its existing scoped API', status == 200 and overview['enabled'] and overview['profile_hash'] == PROFILE_HASH, 'HTTP')
        check('Public lifecycle metadata contains no credential verifier or device proof', 'pin_hash' not in json.dumps(overview) and 'device_proof' not in json.dumps(overview) and 'enrolment_challenge' not in json.dumps(overview), 'HTTP CREDENTIAL')
        check('Unauthenticated lifecycle read is refused', request('GET', base, token=None)[0] == 401, 'HTTP SCOPE')
        check('A read-only credential cannot mutate lifecycle state', request('POST', base + '/profile', {}, token=reader)[0] == 403, 'HTTP SCOPE')
        check('A foreign workspace key cannot read this lifecycle', request('GET', base.replace(tenant, 'tenant_integration'))[0] == 403, 'HTTP SCOPE')
        check('Unexpected Host is rejected by shared ingress', request('GET', base, headers={'Host': 'foreign.invalid'})[0] == 403, 'HTTP')
        check('Foreign Origin is rejected by shared ingress', request('GET', base, headers={'Origin': 'http://foreign.invalid'})[0] == 403, 'HTTP')
        check('Duplicate JSON fields are rejected before lifecycle dispatch', request('POST', base + '/profile', raw=b'{"x":1,"x":2}')[0] == 400, 'HTTP')
        check('Oversized lifecycle requests respect the shared body budget', request('POST', base + '/profile', raw=b' ' * 65537)[0] == 413, 'HTTP')
        code, case_http = request('POST', base + '/complaints', {'payment_id': lost['id'], 'reason': 'status_mismatch', 'request_key': uid('httpcase')})
        check('A scoped HTTP caller creates a complaint', code == 202, 'HTTP COMPLAINT')
        request('POST', base + '/complaints/' + case_http['id'] + '/investigate', {})
        code, proposal_http = request('POST', base + '/complaints/' + case_http['id'] + '/propose', {'resolution': 'no_financial_error', 'request_key': uid('httpproposal')})
        check('An HTTP resolution proposal binds the authenticated maker', code == 202 and proposal_http['maker'] == 'service:' + hashlib.sha256(('Bearer ' + writer).encode()).hexdigest(), 'HTTP RESOLUTION')
        check('The same HTTP credential cannot act as checker', request('POST', base + '/proposals/' + proposal_http['id'] + '/approve', {})[0] == 403, 'HTTP RESOLUTION')
        check('A distinct authenticated HTTP credential can approve', request('POST', base + '/proposals/' + proposal_http['id'] + '/approve', {}, token=checker)[0] == 200, 'HTTP RESOLUTION')
        http_process.terminate();http_process.wait(timeout=20);http_process = None;log.close()

        # Consistent isolated control snapshot, preserving the evidence key.
        restore_dir = directory / 'restored';restore_dir.mkdir(mode=0o700)
        shutil.copyfile(directory / 'evidence.key', restore_dir / 'evidence.key');(restore_dir / 'evidence.key').chmod(0o600)
        if not postgres:
            with sqlite3.connect(directory / 'avenra.db') as source, sqlite3.connect(restore_dir / 'avenra.db') as target: source.backup(target)
            restored = App(restore_dir)
        else:
            info = conninfo_to_dict(dsn);tool = Path('/opt/homebrew/opt/postgresql@16/bin')
            backup = directory / 'control.dump';backup.touch(mode=0o600)
            def cli(configuration): return ['--host=' + configuration['host'], '--port=' + str(configuration['port']), '--username=' + configuration['user'], '--dbname=' + configuration['dbname'], '--no-password']
            env_vars = {**os.environ, 'PGPASSWORD': info.get('password', '')}
            subprocess.run([str(tool/'pg_dump'), *cli(info), '--format=custom', '--no-owner', '--file=' + str(backup)], env=env_vars, check=True, capture_output=True)
            restored_name = 'avenra_fidelity_restore_' + uid('pg').split('_')[1]
            with psycopg.connect(admin_dsn(ROOT), autocommit=True) as db: db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restored_name)))
            restored_info = {**conninfo_to_dict(admin_dsn(ROOT)), 'dbname': restored_name}
            subprocess.run([str(tool/'pg_restore'), *cli(restored_info), '--exit-on-error', '--no-owner', str(backup)], env={**os.environ, 'PGPASSWORD': restored_info.get('password', '')}, check=True, capture_output=True)
            restored = App(restore_dir, control_dsn=make_conninfo(**restored_info))
        with app.store.reader() as source, restored.store.reader() as target:
            counts_match = all(source.execute('SELECT count(*) FROM ' + table).fetchone()[0] == target.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in TABLES)
            original = dict(source.execute('SELECT * FROM fidelity_credentials WHERE account_id=?', (value['accounts'][0],)).fetchone())
            copied = dict(target.execute('SELECT * FROM fidelity_credentials WHERE account_id=?', (value['accounts'][0],)).fetchone())
        check('All lifecycle control records survive a consistent isolated restore', counts_match and original == copied, 'RESTORE CREDENTIAL')
        check('Restored signed cycle and complaint observations still verify', restored.fidelity.cycle_read(tenant, env, cycle['id'])['valid'] and restored.fidelity.complaint_read(tenant, env, complaint['id'])['state'] == 'resolved', 'RESTORE EVIDENCE')
        # Rehearse the additive upgrade from the first installed extension schema.
        with restored.store.transaction() as db:
            db.execute('ALTER TABLE fidelity_profiles DROP COLUMN activation_sequence')
            db.execute('UPDATE fidelity_schema SET version=1')
        restored.close()
        restored = App(restore_dir, control_dsn=make_conninfo(**restored_info) if postgres else None)
        with restored.store.reader() as db:
            upgraded = db.execute('SELECT version FROM fidelity_schema').fetchone()[0] == 2
            upgraded_profile = db.execute('SELECT activation_sequence FROM fidelity_profiles WHERE environment_id=?', (env,)).fetchone()[0]
            original_activation = db.execute("SELECT sequence FROM audit_events WHERE environment_id=? AND kind='fidelity.profile.enabled'", (env,)).fetchone()[0]
        check('Installed lifecycle schema upgrades using the original activation transaction', upgraded and upgraded_profile == original_activation, 'RESTORE PROFILE PRESERVE')
        check('Schema upgrade preserves financial reports and credential verifiers', restored.fidelity.cycle_read(tenant, env, cycle['id'])['valid'] and restored.fidelity.overview(tenant, env)['credentials'], 'RESTORE CREDENTIAL')
        snapshot_dir = directory / 'archive-source';snapshot_dir.mkdir(mode=0o700)
        if postgres:
            backup_target = snapshot_dir / 'control.dump';backup_target.touch(mode=0o600);shutil.copyfile(backup, backup_target)
        else:
            with sqlite3.connect(directory / 'avenra.db') as source, sqlite3.connect(snapshot_dir / 'control.db') as target: source.backup(target)
            (snapshot_dir / 'control.db').chmod(0o600)
        key_target = snapshot_dir / 'evidence.key';key_target.touch(mode=0o600);shutil.copyfile(directory / 'evidence.key', key_target)
        archive_key = recovery_key(directory / 'recovery.key', create=True)
        encrypted = directory / 'control.avenra.enc'
        archived = encrypt_snapshot(snapshot_dir, encrypted, archive_key)
        recovered = restore_snapshot(encrypted, directory / 'archive-restored', archive_key)
        check('Completed native control snapshot encrypts and restores with authenticated encryption', archived['manifest_hash'] == recovered['manifest_hash'] and recovered['files'] == 2, 'ARCHIVE RESTORE')
        check('Restored recovery files and recovery key retain private permissions', all(p.stat().st_mode & 0o077 == 0 for p in (directory / 'archive-restored').rglob('*')) and (directory / 'recovery.key').stat().st_mode & 0o077 == 0, 'ARCHIVE')
        denied('A wrong recovery key is refused before extraction', lambda: restore_snapshot(encrypted, directory / 'wrong-key-restore', bytes(32)), 'ARCHIVE_AUTHENTICATION', 'ARCHIVE')
        tampered = directory / 'tampered.avenra.enc';payload = bytearray(encrypted.read_bytes());payload[-20] ^= 1;tampered.write_bytes(payload);tampered.chmod(0o600)
        denied('Changed ciphertext is refused before extraction', lambda: restore_snapshot(tampered, directory / 'tampered-restore', archive_key), 'ARCHIVE_AUTHENTICATION', 'ARCHIVE')
        check('Failed archive authentication writes no recovery directory', not (directory / 'wrong-key-restore').exists() and not (directory / 'tampered-restore').exists(), 'ARCHIVE')
        baseline = json.loads((ROOT / '.local/fidelity-baseline.json').read_text())
        original_files = json.loads(Path(baseline['manifest']).read_text())
        registration = {'avenra/app.py', 'avenra/asgi.py', 'avenra/worker.py', 'avenra/identity.py', 'avenra/lab_identity.py', 'avenra/postgres_store.py', 'avenra/services.py', 'avenra/bank_api.py', 'avenra/server.py', 'avenra/static/app.js', 'avenra/static/banking.js', 'avenra/static/index.html', 'avenra/static/upi.js', 'avenra/util.py', 'scripts/release_readiness.py', 'scripts/local.py', 'scripts/refresh_public_profiles.py', 'scripts/backup_restore.py', 'LICENSES/protocol-profiles.json', 'requirements.txt', 'BANKING_IMPLEMENTATION.md', 'README.md', 'VERIFICATION.md'}
        # The additive operational routes/runbook update these two existing
        # guides. The original baseline hashes and four financial modules stay
        # protected; this permits documentation, not financial-engine rewrites.
        registration.update({'API_REFERENCE.md','OPERATIONS.md'})
        # Recovery adds authenticated physical routing and a process-lifetime
        # capture fence at the existing bank transport/launcher boundary. The
        # logical contract and original financial modules remain protected.
        registration.update({'avenra/lab_bank_client.py','avenra/lab_bank.py'})
        # Versioned XML uses the existing adapter registry and scoped UPI route
        # dispatcher. These are additive registration boundaries; the payment,
        # banking, simulation and gateway engines remain byte-for-byte pinned.
        registration.update({'avenra/participant_connections.py','avenra/upi_api.py','docs/PARTICIPANT_CONNECTIONS.md','avenra/synthetic_lab.py'})
        # The existing scenario runner registers additional POC cases; its
        # original financial branches and the four pinned engines are retained.
        registration.add('avenra/scenarios.py')
        # The original HTTP regression explicitly selects its original six
        # cases. Hardware-backed POC cases have a separate acceptance pack.
        registration.add('scripts/verify.py')
        # Source publication added explicit environment-file exclusions. This
        # reviewed repository hygiene change does not alter financial engines.
        registration.add('.gitignore')
        changed = [p for p, hashed in original_files.items() if hashlib.sha256((ROOT/p).read_bytes()).hexdigest() != hashed]
        check('Existing financial modules are preserved; modifications are limited to additive integration points', set(changed) <= registration and all(x not in changed for x in ['avenra/upi.py', 'avenra/banking.py', 'avenra/simulation.py', 'avenra/gateways.py']), 'PRESERVE', {'registration_files': changed})
        check('The retained workspace audit chain verifies', app.store.verify_audit()['valid'], 'EVIDENCE')
        covered = {r for check_result in report['checks'] for r in check_result['requirements'] if check_result['passed']}
        check('Every declared synthetic lifecycle requirement has executed evidence', {r[0] for r in REQUIREMENTS} <= covered, 'PROFILE')
        check('Qualification source did not change while its cases executed', report['fingerprint_at_start'] == fingerprint() and report['case_pack_sha256'] == case_pack_hash(), 'EVIDENCE')
        lab.record_acceptance(tenant, env, report['checks'])
        check('Coverage resolves against this exact profile and application source', all(r['state'] == 'passed' for r in lab.coverage(tenant, env)['requirements']), 'PROFILE EVIDENCE')
        report['passed'] = True
    except Exception as error:
        report.update(error=str(error), error_type=type(error).__name__, traceback=traceback.format_exc())
        if app and 'env' in locals():
            try: app.fidelity.record_acceptance(tenant, env, report['checks'])
            except Exception: pass
    finally:
        if http_process and http_process.poll() is None: http_process.terminate();http_process.wait(timeout=20)
        for process in bank_processes: stop(process)
        if restored: restored.close()
        if app: app.close()
    report.update(fingerprint=fingerprint(), application_fingerprint=fingerprint(), profile_hash=PROFILE_HASH, created_at=time.time(), store='postgres' if postgres else 'sqlite')
    path = ROOT / '.local' / ('fidelity-postgres-verification.json' if postgres else 'fidelity-verification.json')
    archive = ROOT / '.local/verification-attempts' / (path.stem + '-' + str(time.time_ns()) + '.json');archive.parent.mkdir(parents=True, exist_ok=True)
    for p in [path, archive]: p.write_text(json.dumps(report, indent=2) + '\n');p.chmod(0o600)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'error': report.get('error'), 'report': str(path)}))
    return 0 if report['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
