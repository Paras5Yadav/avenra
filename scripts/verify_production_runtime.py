#!/usr/bin/env python3
"""Observe deployed synthetic IAM, TLS, shared quotas and worker failure.

Uses fresh PostgreSQL roles/database, real ASGI processes and native accounting.
No credentials or synthetic issuer passwords are written to the public report.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import time
import traceback
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, parse_qs, urlsplit
from urllib.request import HTTPSHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.app import App
from avenra.database_deployment import provision
from avenra.deployment_backup import snapshot as snapshot_deployment, restore as restore_deployment
from avenra.deployment_identity import issue, client_context, paths
from avenra.gateways import HTTP, NoRedirect
from avenra.errors import DomainError
from avenra.lab_identity import private_write
from avenra.local_database import admin_dsn
from avenra.production import POLICY_DEFAULT, ROLE_CAPABILITIES
from avenra.private_config import write_document
from avenra.synthetic_identity import provision as issuer_provision
from avenra.synthetic_lab import fixture, prepare, authorize, advance
from avenra.util import canonical, application_fingerprint, uid


def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0));return listener.getsockname()[1]


def main():
    import psycopg
    directory = ROOT / '.local/qa' / uid('production_runtime');directory.mkdir(mode=0o700, parents=True)
    report = {'passed': False, 'checks': [], 'directory': str(directory), 'created_at': time.time(),
              'scope': 'Local deployed synthetic identity/transport/admission qualification; no bank conformance, host failover or independent security assessment.',
              'production_ready': False, 'official_conformance': False, 'fingerprint_at_start': application_fingerprint()}
    processes, app, proxy_name = [], None, None
    fault = HTTP('http://127.0.0.1:4831')

    def check(name, condition):
        report['checks'].append({'name': name, 'passed': bool(condition)})
        if not condition: raise AssertionError(name)

    def start(module, arguments, name):
        log = directory / (name + '.log')
        with log.open('ab') as stream:
            log.chmod(0o600)
            child = subprocess.Popen([sys.executable, '-B', '-m', module, *arguments], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
        processes.append(child);return child

    def stop(child):
        if child.poll() is None:
            child.terminate()
            try: child.wait(timeout=15)
            except subprocess.TimeoutExpired: child.kill();child.wait(timeout=5)

    def wait(callback, predicate=lambda x: bool(x), timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                value = callback()
                if predicate(value): return value
            except (URLError, OSError): pass
            time.sleep(.05)
        raise TimeoutError('Deployment did not reach the required observation; inspect private process logs.')

    def call(opener, url, data=None, headers=None, form=False):
        body = urlencode(data).encode() if form else canonical(data).encode() if data is not None else None
        request = Request(url, data=body, headers={'Content-Type': 'application/x-www-form-urlencoded' if form else 'application/json', **(headers or {})})
        try: response = opener.open(request, timeout=12)
        except HTTPError as error: response = error
        with response:
            content = response.read(4_000_001)
            value = json.loads(content) if content and 'application/json' in response.headers.get('Content-Type', '') else content.decode()
            return response.status, response.headers, value

    def code(result):
        error = result[2].get('error') if isinstance(result[2], dict) else None
        return error.get('code') if isinstance(error, dict) else error

    try:
        receipt = provision(directory, admin_dsn(ROOT));report['database'] = receipt['database']
        runtime_dsn = (directory / 'control.dsn').read_text().strip()
        api_ports = [port(), port()];gateway_port, gateway_proxy_port, issuer_port = port(), port(), port()
        origin = 'https://127.0.0.1:' + str(api_ports[0]);origins = [origin, 'https://127.0.0.1:' + str(api_ports[1])]
        api_identity, engine_identity = issue(directory, 'portal'), issue(directory, 'accounting-ingress')
        issuer = issuer_provision(directory, issuer_port, origin)
        config = json.loads((directory / 'deployment.json').read_text())
        config.update(http={'tls_identity': api_identity['reference'], 'require_client_certificate': True, 'dispatch_timeout_seconds': 2, 'max_concurrency': 2},
                      identity={'granular': True, 'require_session_subject': True, 'max_session_seconds': 120}, role='api',
                      transport={'accounting': {'origin': 'https://127.0.0.1:' + str(gateway_proxy_port), 'identity_reference': engine_identity['reference']}})
        write_document(directory / 'deployment.json', config)
        identity_config = {'mode': 'institutional', 'issuer': issuer['issuer'], 'authorization_endpoint': issuer['issuer'] + '/authorize', 'token_endpoint': issuer['issuer'] + '/token',
                           'jwks_url': issuer['issuer'] + '/jwks', 'ca_file': issuer['ca_file'], 'external_origin': origin, 'audience': issuer['audience'], 'client_id': issuer['client_id'], 'client_secret': issuer['client_secret']}
        private_write(directory / 'identity.json', canonical(identity_config))
        gateway_config = {'format': 'avenra-service-gateway/1', 'service': 'accounting', 'upstream': 'http://127.0.0.1:4830', 'port': gateway_port, 'identity_reference': engine_identity['reference']}
        private_write(directory / 'gateway.json', canonical(gateway_config))
        proxy_name = uid('runtime_tls')
        fault.call('POST', '/proxies', {'name': proxy_name, 'listen': '127.0.0.1:' + str(gateway_proxy_port), 'upstream': '127.0.0.1:' + str(gateway_port), 'enabled': True})
        gateway = start('avenra.service_gateway', ['--data', str(directory), '--config', str(directory / 'gateway.json')], 'gateway')
        issuer_process = start('avenra.synthetic_identity', ['--config', issuer['config']], 'issuer')
        engine_opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=client_context(directory, engine_identity['reference'])))
        gateway_origin = 'https://127.0.0.1:' + str(gateway_proxy_port)
        wait(lambda: call(engine_opener, gateway_origin + '/_/info'), lambda x: x[0] == 200)
        check('Native accounting is reachable through a real mutual-TLS gateway', gateway.poll() is None)
        refused = False
        try: call(build_opener(ProxyHandler({}), HTTPSHandler(context=client_context(directory, engine_identity['reference'], False))), gateway_origin + '/_/info')
        except (OSError, URLError): refused = True
        check('Engine gateway refuses a missing client certificate', refused)
        check('Engine gateway rejects routes outside its accounting profile', call(engine_opener, gateway_origin + '/admin')[0] == 403)
        app = App(directory, control_dsn=runtime_dsn)
        value = fixture(app);tenant, env = value['tenant_id'], value['environment_id']
        before = {x['id']: x['balance_paise'] for x in app.accounts(tenant, env)}
        payment = prepare(app, value, 4500);authorize(app, value, payment);payment = advance(app, value, payment)
        app.fidelity.enable(tenant, env, {})
        after = {x['id']: x['balance_paise'] for x in app.accounts(tenant, env)}
        check('Runtime database role and mTLS transport complete a real participant payment', payment['state'] == 'succeeded' and payment['evidence']['valid'] and after[value['accounts'][0]] == before[value['accounts'][0]] - 4500 and after[value['accounts'][1]] == before[value['accounts'][1]] + 4500)
        report.update(tenant_id=tenant, environment_id=env, payment_id=payment['id'])
        quota_env = app.create_environment(tenant, 'Shared admission verification')['id'];app.provision(tenant, quota_env)
        for target, policy in [(env, {**POLICY_DEFAULT, 'burst': 100, 'rate_per_minute': 60000}), (quota_env, {**POLICY_DEFAULT, 'burst': 4, 'rate_per_minute': 1})]:
            app.runtime.publish_policy(tenant, target, {**policy, 'request_key': uid('policy')}, 'deployment-verifier')
        with psycopg.connect(runtime_dsn, autocommit=True) as db:
            for statement, label in [('CREATE TABLE forbidden_runtime_table(id int)', 'Runtime role cannot create tables'), ('ALTER TABLE tenants ADD COLUMN forbidden int', 'Runtime role cannot alter tables'), ('UPDATE avenra_schema_version SET version=0', 'Runtime role cannot rewrite migration versions'), ('DELETE FROM audit_events', 'Runtime role cannot delete financial audit history'), ('CREATE TEMP TABLE forbidden_temp(id int)', 'Runtime role cannot create temporary tables')]:
                denied = False
                try: db.execute(statement)
                except psycopg.errors.InsufficientPrivilege: denied = True
                check(label, denied)
        app.close();app = None
        servers = [start('avenra.asgi', ['--data', str(directory), '--port', str(number), '--mode', 'api'], 'api-' + str(i)) for i, number in enumerate(api_ports)]
        api_context = client_context(directory, api_identity['reference'])
        trust = ssl.create_default_context(cafile=issuer['ca_file'])
        for certificate in [paths(directory, api_identity['reference'])['ca']]: trust.load_verify_locations(str(certificate))
        # The client certificate authenticates to API ingress; the issuer itself
        # uses HTTPS plus registered client authentication, not this certificate.
        api_keys = paths(directory, api_identity['reference'], client=True)
        trust.load_cert_chain(str(api_keys['cert']), str(api_keys['key']))
        plain = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=api_context))
        for target in origins: wait(lambda: call(plain, target + '/health/live'), lambda x: x[0] == 200)
        check('API-only readiness refuses to claim a missing matching worker', call(plain, origin + '/health/ready')[0] == 503)
        workers = [start('avenra.worker', ['--data', str(directory)], 'worker-' + str(i)) for i in range(2)]
        wait(lambda: call(plain, origin + '/health/ready'), lambda x: x[0] == 200)
        check('Matching independent workers make the deployment ready', all(x.poll() is None for x in workers))
        for context, host, label in [(client_context(directory, api_identity['reference'], False), origin, 'API ingress refuses a missing client certificate'), (ssl.create_default_context(), origin, 'API ingress is rejected without its explicit test authority'), (api_context, origin.replace('127.0.0.1', 'localhost'), 'API ingress rejects a certificate hostname mismatch')]:
            refused = False
            try: call(build_opener(ProxyHandler({}), HTTPSHandler(context=context)), host + '/health/live')
            except (OSError, URLError): refused = True
            check(label, refused)

        class User:
            def __init__(self, role):
                self.jar = CookieJar();self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=trust), HTTPCookieProcessor(self.jar))
                login = call(self.opener, origin + '/auth/login')
                form = wait(lambda: call(self.opener, login[1]['Location']), lambda x: x[0] == 200)
                interaction = re.search('name="interaction" value="([A-Za-z0-9_-]+)"', form[2]).group(1)
                signed = call(self.opener, issuer['issuer'] + '/login', {'interaction': interaction, 'username': role, 'password': issuer['passwords'][role]}, form=True)
                check('Synthetic issuer authenticates registered ' + role + ' fixture', signed[0] == 303)
                callback = call(self.opener, signed[1]['Location'])
                check('HTTPS OIDC code/PKCE callback establishes a scoped ' + role + ' session', callback[0] == 302 and 'Secure' in callback[1].get('Set-Cookie', ''))
                self.csrf = self.api('/api/session', {})[2]['csrf']
                self.identity = self.api('/api/me')[2]

            def api(self, path, data=None, node=0):
                return call(self.opener, origins[node] + path, data, {'Origin': origin, 'X-Avenra-CSRF': self.csrf} if hasattr(self, 'csrf') else {'Origin': origin, 'X-Avenra-Client': 'console'})

        users = {role: User(role) for role in ROLE_CAPABILITIES};admin = users['administrator'];second_admin = User('administrator')
        probe = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=trust), HTTPCookieProcessor(CookieJar()))
        binding = call(probe, origin + '/auth/login');auth_url = binding[1]['Location']
        parameters = {k: v[0] for k, v in parse_qs(urlsplit(auth_url).query).items()}
        changed_redirect = call(probe, issuer['issuer'] + '/authorize?' + urlencode({**parameters, 'redirect_uri': 'https://unregistered.invalid/callback'}))
        check('Synthetic identity provider refuses an unregistered redirect without following it', changed_redirect[0] == 400)
        form = call(probe, auth_url);interaction = re.search('name="interaction" value="([A-Za-z0-9_-]+)"', form[2]).group(1)
        form_values = {'interaction': interaction, 'username': 'operator', 'password': issuer['passwords']['operator']}
        # The foreign browser trusts the issuer but has no interaction cookie.
        foreign = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=trust))
        check('Issuer interaction is bound to its original browser cookie', call(foreign, issuer['issuer'] + '/login', form_values, form=True)[0] == 403)
        signed = call(probe, issuer['issuer'] + '/login', form_values, form=True)
        authorization_code = parse_qs(urlsplit(signed[1]['Location']).query)['code'][0]
        with psycopg.connect(runtime_dsn, autocommit=True) as db:
            verifier = db.execute('SELECT verifier FROM identity_logins WHERE state_hash=%s', (hashlib.sha256(parameters['state'].encode()).hexdigest(),)).fetchone()[0]
        token_form = {'grant_type': 'authorization_code', 'client_id': issuer['client_id'], 'client_secret': issuer['client_secret'], 'redirect_uri': origin + '/auth/callback', 'code': authorization_code, 'code_verifier': verifier}
        check('Confidential code exchange refuses a different registered-client secret', call(probe, issuer['issuer'] + '/token', {**token_form, 'client_secret': 'different-secret'}, form=True)[0] == 401)
        check('Confidential code exchange refuses substituted PKCE proof', call(probe, issuer['issuer'] + '/token', {**token_form, 'code_verifier': 'x' * 43}, form=True)[0] == 401)
        tokens = call(probe, issuer['issuer'] + '/token', token_form, form=True)
        check('A valid proof can still consume the original authorization code once', tokens[0] == 200)
        check('A consumed authorization code cannot be replayed', call(probe, issuer['issuer'] + '/token', token_form, form=True)[0] == 401)
        check('API access token is accepted through the independently verified JWKS path', call(plain, origin + '/api/me', headers={'Authorization': 'Bearer ' + tokens[2]['access_token']})[0] == 200)
        check('An ID token cannot be substituted as an API access token', call(plain, origin + '/api/me', headers={'Authorization': 'Bearer ' + tokens[2]['id_token']})[0] == 401)
        scope = '/api/tenants/' + tenant + '/environments/' + env
        check('Two independent sign-ins preserve the same institutional subject', admin.identity['principal'] == second_admin.identity['principal'] and admin.identity['approval_principal'] == second_admin.identity['approval_principal'])
        check('Observer reads the original banking environment', users['observer'].api(scope + '/banking')[0] == 200)
        check('Observer cannot create a financial operation', users['observer'].api(scope + '/payments', {})[0] == 403)
        check('Reviewer cannot create participants', code(users['reviewer'].api(scope + '/banking/upi/participants', {})) == 'CAPABILITY_REQUIRED')
        check('Operator cannot grant itself an administrative role', code(users['operator'].api(scope + '/banking/runtime/grants', {})) == 'CAPABILITY_REQUIRED')
        check('Unmapped write routes fail authorization before dispatch', code(users['operator'].api(scope + '/banking/unmapped-function', {})) == 'CAPABILITY_REQUIRED')
        check('Signed-in workspace cannot read another workspace', admin.api('/api/tenants/tenant_integration/environments/' + env + '/overview')[0] == 403)
        restricted = {'principal': users['operator'].identity['principal'], 'capabilities': ['workspace.read', 'payments.create', 'payments.recover'], 'environment_ids': [env], 'expires_at': time.time() + 300}
        grant = admin.api(scope + '/banking/runtime/grants', restricted)
        check('Administrator publishes a bounded subject/environment grant', grant[0] == 202)
        check('Payment-only grant cannot bypass credential flows using the raw ledger API', code(users['operator'].api(scope + '/payments', {})) == 'CAPABILITY_REQUIRED')
        check('Payment recovery permission cannot resume a recurring mandate', code(users['operator'].api(scope + '/banking/fidelity/mandates/missing/resume', {})) == 'CAPABILITY_REQUIRED')
        check('Subject environment restriction applies on another API node', code(users['operator'].api('/api/tenants/' + tenant + '/environments/' + quota_env + '/overview', node=1)) == 'ENVIRONMENT_PERMISSION')
        check('Environment grants also filter the workspace discovery catalogue', [e['id'] for e in users['operator'].api('/api/catalog')[2]['environments']] == [env])
        check('Explicit grant revocation succeeds', admin.api(scope + '/banking/runtime/grants/' + grant[2]['id'] + '/revoke', {})[0] == 200)
        check('Revoked grant cannot fall back to broader signed role claims', code(users['operator'].api(scope + '/banking')) == 'GRANT_REVOKED')
        check('Revoked subject cannot retrieve catalogue metadata', code(users['operator'].api('/api/catalog')) == 'GRANT_REVOKED')
        admin.api(scope + '/banking/runtime/grants', {**restricted, 'capabilities': ROLE_CAPABILITIES['operator'], 'environment_ids': []})
        complaint = admin.api(scope + '/banking/fidelity/complaints', {'payment_id': payment['id'], 'reason': 'status_mismatch', 'request_key': uid('complaint')})[2]
        check('The current lifecycle API accepts the complaint', admin.api(scope + '/banking/fidelity/complaints/' + complaint['id'] + '/investigate', {})[0] == 200)
        proposal = admin.api(scope + '/banking/fidelity/complaints/' + complaint['id'] + '/propose', {'resolution': 'no_financial_error', 'request_key': uid('proposal')})[2]
        approval_path = scope + '/banking/fidelity/proposals/' + proposal['id'] + '/approve'
        check('Signing in again cannot approve the same person’s own proposal', code(second_admin.api(approval_path, {}, node=1)) == 'SECOND_PRINCIPAL_REQUIRED')
        check('Operator without checker permission cannot approve a proposal', code(users['operator'].api(approval_path, {})) == 'CAPABILITY_REQUIRED')
        check('A separately authenticated reviewer can approve observed financial evidence', users['reviewer'].api(approval_path, {})[0] == 200)
        observer_cookie = next(c.value for c in users['observer'].jar if c.name == 'avenra_session')
        check('Logout revokes the current institutional session', users['observer'].api('/api/session/logout', {})[0] == 200)
        check('A retained old session cookie cannot be replayed after logout', call(plain, origin + '/api/me', headers={'Cookie': 'avenra_session=' + observer_cookie})[0] == 401)
        quota_path = '/api/tenants/' + tenant + '/environments/' + quota_env + '/overview'
        outcomes = []
        with ThreadPoolExecutor(4) as executor:
            for batch in range(2): outcomes.extend(executor.map(lambda i: admin.api(quota_path, node=i % 2), range(4)))
        report['quota_observations'] = [{'status': x[0], 'code': code(x)} for x in outcomes]
        check('One shared four-token budget is enforced across both API processes', sum(x[0] == 200 for x in outcomes) == 4 and all(x[0] == 200 or code(x) == 'REQUEST_RATE' for x in outcomes))
        # An independently persisted future job exercises admission without a
        # race against the worker consuming it. It is never a financial effect.
        with psycopg.connect(runtime_dsn, autocommit=True) as db:
            pending_id = uid('job')
            db.execute("INSERT INTO outbox(id,tenant_id,environment_id,kind,aggregate_id,payload_json,dedupe_key,available_at,created_at) VALUES(%s,%s,%s,'verification_hold',%s,'{}',%s,%s,%s)", (pending_id, tenant, env, pending_id, pending_id, time.time() + 3600, 'verification'))
        policy = {**POLICY_DEFAULT, 'burst': 100, 'rate_per_minute': 60000, 'max_pending_jobs': 1, 'request_key': uid('policy')}
        check('Existing API publishes the explicit resource policy', admin.api(scope + '/banking/runtime/policies', policy)[0] == 202)
        check('Queue backpressure refuses new financial work', code(admin.api(scope + '/payments', {})) == 'QUEUE_CAPACITY')
        check('Queue backpressure preserves original recovery/status access', code(admin.api(scope + '/banking/upi/payments/missing/status', {})) != 'QUEUE_CAPACITY')
        with psycopg.connect(runtime_dsn, autocommit=True) as db: db.execute("UPDATE outbox SET state='done' WHERE id=%s", (pending_id,))
        admin.api(scope + '/banking/runtime/policies', {**policy, 'request_key': uid('policy'), 'max_inflight': 1})
        fault.call('POST', '/proxies/' + proxy_name + '/toxics', {'name': 'runtime_latency', 'type': 'latency', 'stream': 'downstream', 'toxicity': 1.0, 'attributes': {'latency': 3500, 'jitter': 0}})
        with ThreadPoolExecutor(2) as executor:
            slow = executor.submit(admin.api, scope + '/accounts')
            time.sleep(.3)
            check('Shared in-flight limit rejects a second node while native I/O is pending', code(second_admin.api(scope + '/overview', node=1)) == 'TENANT_CAPACITY')
            first = slow.result()
            check('HTTP dispatch deadline reports an uncertain result without inventing a decline', first[0] == 504 and code(first) == 'REQUEST_DEADLINE')
        # The first dispatch is still consuming a physical thread/resource permit
        # after its HTTP timeout. A second unrelated request occupies the other.
        with ThreadPoolExecutor(2) as executor:
            second = executor.submit(admin.api, '/api/catalog')
            time.sleep(.2)
            check('Timed-out dispatch retains its physical request permit until completion', call(plain, origin + '/api/me')[0] == 503)
            check('Liveness remains responsive while dispatch capacity is occupied', call(plain, origin + '/health/live')[0] == 200)
            second.result()
        fault.call('DELETE', '/proxies/' + proxy_name + '/toxics/runtime_latency')
        wait(lambda: admin.api(scope + '/banking/runtime'), lambda x: x[0] == 200)
        metrics = admin.api(scope + '/banking/runtime')[2]
        check('Runtime metrics retain deadline observations and eventual completion', metrics['metrics']['deadline_exceeded'] >= 1 and metrics['metrics']['requests_last_hour'] > 1)
        stop(workers[0]);check('One remaining worker preserves readiness', call(plain, origin + '/health/ready')[0] == 200)
        stop(workers[1]);wait(lambda: call(plain, origin + '/health/ready'), lambda x: x[0] == 503, timeout=10)
        check('Readiness detects an actually stopped worker without restarting the API', call(plain, origin + '/health/ready')[0] == 503 and call(plain, origin + '/health/live')[0] == 200)
        workers.append(start('avenra.worker', ['--data', str(directory)], 'worker-restarted'))
        wait(lambda: call(plain, origin + '/health/ready'), lambda x: x[0] == 200)
        check('A restarted independent worker restores readiness', workers[-1].poll() is None)
        observed = {x['id']: x['balance_paise'] for x in admin.api(scope + '/accounts')[2]['accounts']}
        check('Authorization, quota, timeout and process-failure probes leave exact native balances intact', observed == after)
        app = App(directory, control_dsn=runtime_dsn)
        check('All deployed actions preserve the financial audit chain', app.store.verify_audit()['valid'])
        captured = snapshot_deployment(directory, directory / 'deployment-recovery-snapshot')
        restored = restore_deployment(directory / 'deployment-recovery-snapshot', directory / 'inactive-restored-deployment')
        check('Live synthetic issuer/database/TLS files survive an inactive deployment restore', captured['files'] == restored['files'] and restored['valid'] and restored['files'] >= 15)
        refused = False
        try: App(directory / 'inactive-restored-deployment', control_dsn='deliberately-invalid-no-network')
        except DomainError as error: refused = error.code == 'RECOVERY_INACTIVE'
        check('Restored private files refuse application construction before any source database connection', refused)
        changed = directory / 'deployment-recovery-snapshot/deployment.json'
        changed.write_bytes(changed.read_bytes() + b' ')
        refused = False
        try: restore_deployment(directory / 'deployment-recovery-snapshot', directory / 'refused-deployment-restore')
        except DomainError as error: refused = error.code == 'RECOVERY_INTEGRITY'
        check('Changed private deployment recovery input is refused before any output is activated', refused and not (directory / 'refused-deployment-restore').exists())
        check('The tested application source remained fixed throughout qualification', application_fingerprint() == report['fingerprint_at_start'])
        report['passed'] = True
    except Exception as error:
        report.update(error=str(error), traceback=traceback.format_exc())
    finally:
        if app: app.close()
        for child in reversed(processes): stop(child)
        if proxy_name:
            try: fault.call('DELETE', '/proxies/' + proxy_name)
            except Exception: pass
    report['application_fingerprint'] = application_fingerprint()
    report['case_pack_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    content = json.dumps(report, indent=2) + '\n'
    target = ROOT / '.local/production-runtime-verification.json';write_document(target, report)
    attempts = ROOT / '.local/verification-attempts';attempts.mkdir(exist_ok=True)
    private_write(attempts / ('production-runtime-' + str(time.time_ns()) + '.json'), content)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'report': str(target), 'error': report.get('error')}))
    if not report['passed']: print(report.get('traceback', ''))
    return 0 if report['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
