#!/usr/bin/env python3
"""Exercise the actual local HTTP, ledger, fault and durable-state boundaries."""
import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import subprocess
import socket
import stat
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:4827'
sys.path.insert(0,str(ROOT))
from avenra.util import application_fingerprint, uid
from avenra.private_config import write_document
REPORT = ROOT / '.local/verification.json'
checks = []


def check(name, condition, detail=None):
    checks.append({'name': name, 'passed': bool(condition), 'detail': detail})
    print(('PASS ' if condition else 'FAIL ') + name, flush=True)
    if not condition:
        raise AssertionError(name + ': ' + str(detail))


def run(restart=False, data_directory=None, restart_services=None):
    data_directory = Path(data_directory or ROOT / '.local/data')
    credentials = json.loads((data_directory / 'credentials.json').read_text())
    token = credentials['api_key']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    operator = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(method, path, body=None, auth=True, headers=None, client=None):
        request_headers = {'Content-Type': 'application/json'}
        if auth:
            request_headers['Authorization'] = 'Bearer ' + token
        request_headers.update(headers or {})
        req = urllib.request.Request(BASE + path, method=method, headers=request_headers,
                                     data=json.dumps(body, allow_nan=False).encode() if body is not None else None)
        try:
            with (client or opener).open(req, timeout=10) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    status, readiness = call('GET', '/health/ready', auth=False)
    check('The actual HTTP deployment runs the current candidate application', status == 200 and readiness.get('source_fingerprint') == application_fingerprint())
    status, catalog = call('GET', '/api/catalog')
    check('Scoped API catalogue is available', status == 200)
    check('API key sees only its workspace', [t['id'] for t in catalog['tenants']] == [credentials['tenant_id']])
    environment = next(e for e in catalog['environments'] if e['status'] == 'active')
    tenant, env = credentials['tenant_id'], environment['id']
    path = f'/api/tenants/{tenant}/environments/{env}'
    status, overview = call('GET', path + '/overview')
    check('Engine-backed account balances are available', status == 200 and len(overview['accounts']) == 3)
    accounts = {a['role']: a for a in overview['accounts']}
    before = {role: a['balance_paise'] for role, a in accounts.items()}
    body = {'payer_id': accounts['payer']['id'], 'payee_id': accounts['payee']['id'], 'amount_paise': 12345,
            'request_key': 'qa:' + uuid.uuid4().hex, 'fault': 'none'}
    with ThreadPoolExecutor(max_workers=8) as pool:
        submissions = list(pool.map(lambda _: call('POST', path + '/payments', body), range(8)))
    identities = {p['id'] for status, p in submissions if status == 202}
    check('Eight concurrent HTTP submissions create one intent', all(s == 202 for s, _ in submissions) and len(identities) == 1)
    payment_id = next(iter(identities))

    def wait_payment():
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            _, payment = call('GET', path + '/payments/' + payment_id)
            if payment['state'] in {'succeeded', 'declined'}:
                return payment
            time.sleep(.1)
        raise TimeoutError('Payment did not settle.')
    payment = wait_payment()
    check('Accepted HTTP intent becomes a confirmed ledger transaction', payment['state'] == 'succeeded' and payment['ledger_transaction_id'] is not None)
    _, updated = call('GET', path + '/overview')
    after = {a['role']: a['balance_paise'] for a in updated['accounts']}
    check('Actual balances change exactly once', after == {**before, 'payer': before['payer'] - 12345, 'payee': before['payee'] + 12345})
    status, replay = call('POST', path + '/payments', body)
    check('HTTP replay returns the persisted financial identity', status == 202 and replay['id'] == payment_id and replay['ledger_transaction_id'] == payment['ledger_transaction_id'])
    status, conflict = call('POST', path + '/payments', {**body, 'amount_paise': 12346})
    check('Changed amount with the same key is rejected', status == 409 and conflict['error']['code'] == 'IDEMPOTENCY_CONFLICT')
    for invalid in (True, 1.5, 0, -1, 100000001):
        status, _ = call('POST', path + '/payments', {**body, 'request_key': 'invalid:' + uuid.uuid4().hex, 'amount_paise': invalid})
        check('Reject invalid monetary input ' + repr(invalid), status == 400)
    status, _ = call('GET', '/api/catalog', auth=False)
    check('Unauthenticated API read is rejected', status == 401)
    status, _ = call('POST', '/api/session', {}, auth=False, headers={'Origin': 'https://untrusted.invalid', 'X-Avenra-Client': 'console'})
    check('Foreign-origin console bootstrap is rejected', status == 403)
    status, _ = call('GET', '/api/health', auth=False, headers={'Host': 'untrusted.invalid'})
    check('Unexpected Host header is rejected', status == 403)
    status, session = call('POST', '/api/session', {}, auth=False, headers={'Origin': BASE, 'X-Avenra-Client': 'console'}, client=operator)
    check('Same-origin console session is created', status == 200)
    status, _ = call('POST', path + '/payments', body, auth=False, headers={'Origin': BASE}, client=operator)
    check('Cookie mutation without CSRF is rejected', status == 403)
    _, all_catalog = call('GET', '/api/catalog', auth=False, client=operator)
    foreign = next(e for e in all_catalog['environments'] if e['tenant_id'] != tenant)
    foreign_path = f"/api/tenants/{foreign['tenant_id']}/environments/{foreign['id']}"
    status, _ = call('GET', foreign_path + '/overview')
    check('Scoped key cannot read another workspace', status == 403)
    _, foreign_accounts = call('GET', foreign_path + '/accounts', auth=False, client=operator)
    status, _ = call('POST', path + '/payments', {**body, 'request_key': 'foreign:' + uuid.uuid4().hex, 'payee_id': foreign_accounts['accounts'][0]['id']})
    check('Foreign account cannot enter an authorised payment', status == 404)
    status, _ = call('GET', f"/api/tenants/{tenant}/environments/{foreign['id']}/overview")
    check('Environment ownership is enforced independently of path identity', status == 404)
    run_ids = {}
    # Preserve this original six-case financial regression. Provider-specific
    # POC executions have a separate native/HTTP acceptance pack.
    original_scenarios={'payment_success','idempotency','insufficient_funds','lost_response','duplicate_callback','concurrent_replay'}
    for scenario in [s for s in catalog['scenarios'] if s['key'] in original_scenarios]:
        status, value = call('POST', path + '/runs', {'scenario_key': scenario['key'], 'request_key': 'qa-run:' + uuid.uuid4().hex})
        check('Queue scenario ' + scenario['key'], status == 202)
        run_ids[scenario['key']] = value['id']
    results = {}
    deadline = time.monotonic() + 90
    while len(results) < len(run_ids) and time.monotonic() < deadline:
        for key, run_id in run_ids.items():
            if key in results:
                continue
            _, value = call('GET', path + '/runs/' + run_id)
            if value['state'] in {'passed', 'failed'}:
                check('Complete scenario ' + key, value['state'] == 'passed', value.get('error'))
                results[key] = value
        time.sleep(.15)
    check('All six real-engine scenarios finish', len(results) == 6)
    evidence = results['lost_response']['evidence']
    raw = json.dumps(evidence['manifest'], sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    calculated = hashlib.sha256(raw.encode()).hexdigest()
    local_key = (data_directory / 'evidence.key').read_bytes()
    signed = hmac.new(local_key, calculated.encode(), hashlib.sha256).hexdigest()
    check('Evidence hash and HMAC verify independently', evidence['valid'] and calculated == evidence['manifest_hash'] and hmac.compare_digest(signed, evidence['signature']))
    tampered = json.loads(raw)
    tampered['state'] = 'forged'
    tampered_digest = hashlib.sha256(json.dumps(tampered, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    check('Altered evidence does not retain the verified digest', tampered_digest != evidence['manifest_hash'])
    status, audit = call('GET', '/api/audit/verify', auth=False, client=operator)
    check('Retained audit chain verifies', status == 200 and audit['valid'] and audit['checked'] > 0)
    check('VM execution stays unavailable on this host', next(s for s in catalog['services'] if s['key'] == 'execution')['state'] == 'unavailable')
    if restart:
        if restart_services:
            restart_services()
        else:
            subprocess.run([sys.executable, str(ROOT / 'scripts/local.py'), 'stop'], cwd=ROOT, check=True)
            subprocess.run([sys.executable, str(ROOT / 'scripts/local.py'), 'start'], cwd=ROOT, check=True)
        status, replay = call('POST', path + '/payments', body)
        check('Payment identity and ledger transaction survive process restart', status == 202 and replay['id'] == payment_id and replay['ledger_transaction_id'] == payment['ledger_transaction_id'])
        _, restarted = call('GET', path + '/overview')
        balances = {a['role']: a['balance_paise'] for a in restarted['accounts']}
        check('Restart and replay cause no additional debit', balances == after)
        _, retained = call('GET', path + '/runs/' + run_ids['lost_response'] + '/evidence')
        check('Signed evidence survives process restart', retained['valid'] and retained['manifest_hash'] == evidence['manifest_hash'])
    return {'environment_id': env, 'payment_id': payment_id, 'runs': run_ids, 'audit_events_checked': audit['checked'], 'restart_exercised': restart}


@contextmanager
def isolated():
    """Preserve the complete legacy regression against an owned candidate API."""
    from avenra.app import App
    folder = ROOT / '.local/qa' / uid('legacy_http')
    folder.mkdir(mode=0o700, parents=True)
    data = folder / 'data'
    data.mkdir(mode=0o700)
    app = App(data)
    try:
        app.bootstrap()
        with app.store.reader() as db:
            scopes = [(row['tenant_id'], row['id']) for row in db.execute('SELECT tenant_id,id FROM environments')]
        for tenant, environment in scopes:
            app.provision(tenant, environment)
    finally:
        app.close()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        number = listener.getsockname()[1]
    origin = 'http://127.0.0.1:' + str(number)
    process = None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def stop():
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    def start():
        nonlocal process
        log = folder / 'api.log'
        descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'ab') as output:
            process = subprocess.Popen([sys.executable, '-B', '-m', 'avenra.asgi', '--data', str(data), '--port', str(number), '--mode', 'combined'],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Isolated candidate API exited; inspect its private API log.')
            try:
                with opener.open(origin + '/health/ready', timeout=2) as response:
                    value = json.load(response)
                if value.get('ready') and value.get('source_fingerprint') == application_fingerprint():
                    return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(.1)
        raise TimeoutError('Isolated candidate API did not become ready.')
    def restart():
        stop()
        start()
    try:
        start()
        yield folder, data, origin, restart
    finally:
        stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restart', action='store_true', help='Restart only this installation’s tracked services and verify persistence.')
    parser.add_argument('--isolated', action='store_true', help='Use a fresh private control store, owned candidate API port and existing native loopback services.')
    args = parser.parse_args()
    failure = None
    details = {}
    case = None
    started_at = time.time()
    try:
        if args.isolated:
            with isolated() as (case, data, origin, restart_services):
                BASE = origin
                details = {**run(args.restart, data, restart_services), 'api_origin': origin, 'directory': str(case),
                    'scope': 'Fresh private control store and owned candidate ASGI process; existing native loopback accounting/fault services with separate fixture ledgers.'}
        else:
            details = run(args.restart)
    except Exception as error:
        failure = str(error)
        print('Verification stopped: ' + failure, file=sys.stderr)
    report = {'passed': failure is None, 'checks': checks, 'details': details, 'error': failure, 'application_fingerprint': application_fingerprint(),
              'created_at':started_at,'completed_at':time.time()}
    if case:
        write_document(case / 'verification.json', report)
    if REPORT.exists() or REPORT.is_symlink():
        descriptor = os.open(REPORT, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise ValueError('A qualification summary must be an ordinary owned single-link file.')
            # Earlier verifier versions created this report with the umask.
            # Narrow only this evidence file, never deployment configuration.
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
    write_document(REPORT, report)
    print(f'{sum(c["passed"] for c in checks)}/{len(checks)} checks passed. Report: {REPORT}')
    raise SystemExit(1 if failure else 0)
