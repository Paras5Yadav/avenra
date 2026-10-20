#!/usr/bin/env python3
"""Validate the public API, callbacks and actual provider/core integrations."""
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.app import App
from avenra.util import application_fingerprint
from avenra.util import uid


def main():
    credentials = json.loads((ROOT / '.local/data/credentials.json').read_text())
    tenant, token = credentials['tenant_id'], credentials['api_key']
    report = {'passed': False, 'checks': [], 'created_at': time.time()}
    def check(name, condition, detail=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'detail': detail})
        if not condition:
            raise AssertionError(name + (': ' + str(detail) if detail else ''))
    def api(path, body=None, key=token, expected=None):
        headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
        request = Request('http://127.0.0.1:4827' + path, method='POST' if body is not None else 'GET', headers=headers, data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(request, timeout=15) as response:
                value, status = json.load(response), response.status
        except HTTPError as error:
            value, status = json.load(error), error.code
        if expected:
            check('HTTP ' + str(expected) + ' ' + path.split('/banking/')[-1], status == expected, value.get('error') if isinstance(value, dict) else None)
        elif status >= 400:
            raise RuntimeError(str(status) + ' ' + str(value.get('error', {}).get('code')))
        return value
    def wait(callback, predicate, seconds=30):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = callback()
            if predicate(value):
                return value
            time.sleep(.15)
        raise TimeoutError('Expected asynchronous outcome was not observed.')
    receiver, receiver_thread, control = None, None, None
    try:
        environment = api('/api/tenants/' + tenant + '/environments', {'name': 'Banking acceptance ' + time.strftime('%H:%M:%S'), 'fixture_profile': 'banking'})
        env = environment['id']
        report['environment_id'] = env
        base = '/api/tenants/' + tenant + '/environments/' + env
        wait(lambda: api(base + '/overview'), lambda x: x['environment']['status'] == 'active')
        check('Banking profile starts without fixed sample customers', api(base + '/overview')['accounts'] == [])
        bank = base + '/banking'
        first = api(bank + '/banks', {'name': 'Avenra Test Bank A', 'code': 'bank_a', 'request_key': 'bank-a'})
        second = api(bank + '/banks', {'name': 'Avenra Test Bank B', 'code': 'bank_b', 'request_key': 'bank-b'})
        payer_customer = api(bank + '/customers', {'name': 'Avenra Test Customer', 'kind': 'customer', 'request_key': 'customer-a'})
        merchant = api(bank + '/customers', {'name': 'Avenra Test Merchant', 'kind': 'merchant', 'request_key': 'customer-b'})
        payer = api(bank + '/accounts', {'bank_id': first['id'], 'customer_id': payer_customer['id'], 'vpa': 'payer@bank_a', 'opening_paise': 5_000_000, 'request_key': 'account-a'})
        payee = api(bank + '/accounts', {'bank_id': second['id'], 'customer_id': merchant['id'], 'vpa': 'merchant@bank_b', 'opening_paise': 250_000, 'request_key': 'account-b'})
        wait(lambda: api(bank), lambda x: len(x['accounts']) == 2 and all(a['status'] == 'active' for a in x['accounts']))
        check('HTTP account provisioning reaches actual ledger balances', sorted(x['balance_paise'] for x in api(bank)['accounts']) == [250_000, 5_000_000])
        api(bank + '/accounts', {'bank_id': {'invalid': True}, 'customer_id': payer_customer['id'], 'vpa': 'bad@bank_a', 'request_key': 'invalid-id'}, expected=400)
        api(bank + '/accounts/' + payer['account_id'] + '/state', {'state': []}, expected=400)
        api(bank + '/rules', {'name': 'Invalid', 'threshold_paise': 100, 'action': {}}, expected=400)
        check('VPA resolution returns only the scoped banking account', api(bank + '/resolve', {'vpa': 'payer@bank_a'})['account_id'] == payer['account_id'])
        payload = {'payer_vpa': 'payer@bank_a', 'payee_vpa': 'merchant@bank_b', 'amount_paise': 12345, 'request_key': 'vpa-transfer'}
        operation = api(bank + '/operations', payload)
        operation = wait(lambda: api(bank + '/operations/' + operation['id']), lambda x: x['state'] in {'succeeded', 'declined'})
        check('External client completes a VPA-routed interbank payment', operation['state'] == 'succeeded' and len(operation['legs']) == 2)
        api(bank + '/operations', {**payload, 'request_key': 'ambiguous-address', 'payer_id': payee['account_id']}, expected=400)
        analysis = api(bank + '/analysis', {'operation_id': operation['id']})
        evaluated = wait(lambda: api(bank + '/analysis/' + analysis['id']), lambda x: x['state'] == 'succeeded', seconds=45)
        check('Actual monitoring pipeline returns signed rule evidence through Avenra', evaluated['evidence']['valid'] and bool(evaluated['evidence']['manifest']['report']['report']['tadpResult']['typologyResult']))
        check('Monitoring replay preserves native message and report identities', api(bank + '/analysis', {'operation_id': operation['id']})['id'] == analysis['id'])
        other_environment = next(x['id'] for x in api('/api/catalog')['environments'] if x['tenant_id'] == tenant and x['id'] != env)
        api('/api/tenants/' + tenant + '/environments/' + other_environment + '/banking/analysis/' + analysis['id'], expected=404)
        check('HTTP replay preserves both financial leg identities', api(bank + '/operations', payload)['id'] == operation['id'])
        api(bank + '/operations', {**payload, 'amount_paise': 12346}, expected=409)
        api(bank + '/operations/' + operation['id'] + '/refund', {'amount_paise': 12346, 'request_key': 'excess-refund'}, expected=409)
        refund = api(bank + '/operations/' + operation['id'] + '/refund', {'amount_paise': 1000, 'request_key': 'partial-refund'})
        refund = wait(lambda: api(bank + '/operations/' + refund['id']), lambda x: x['state'] in {'succeeded', 'declined'})
        check('Linked HTTP refund completes through the actual ledger', refund['state'] == 'succeeded' and refund['parent_id'] == operation['id'])
        control = App(ROOT / '.local/data', control_dsn=(ROOT / '.local/data/control.dsn').read_text().strip())
        read_key = control.create_api_key(tenant, 'HTTP read-only qualification', ['read'], time.time() + 300)
        api(bank, key=read_key, expected=200)
        api(bank + '/customers', {'name': 'Forbidden', 'request_key': 'forbidden'}, key=read_key, expected=403)
        with control.store.transaction() as db:
            db.execute('UPDATE api_keys SET revoked_at=? WHERE token_hash=?', ('qualification-revoked', hashlib.sha256(read_key.encode()).hexdigest()))
        api(bank, key=read_key, expected=401)
        callback_state = {'secret': None, 'accepted': {}, 'duplicates': 0}
        class Sink(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                event, stamp = self.headers.get('X-Avenra-Event', ''), self.headers.get('X-Avenra-Timestamp', '')
                signed = event + '.' + stamp + '.' + body.decode()
                expected = 'v1=' + hmac.new(callback_state['secret'].encode(), signed.encode(), hashlib.sha256).hexdigest()
                valid = stamp.isdigit() and abs(time.time() - int(stamp)) < 300 and hmac.compare_digest(expected, self.headers.get('X-Avenra-Signature', ''))
                status = 204 if valid else 401
                if valid:
                    if event in callback_state['accepted']:
                        callback_state['duplicates'] += 1
                    callback_state['accepted'][event] = hashlib.sha256(body).hexdigest()
                self.send_response(status)
                self.end_headers()
                if status != 204:
                    self.wfile.write(b'{}')
        receiver = ThreadingHTTPServer(('127.0.0.1', 0), Sink)
        receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
        receiver_thread.start()
        origin = 'http://127.0.0.1:' + str(receiver.server_port)
        config_path = ROOT / '.local/data/services.json'
        config = json.loads(config_path.read_text())
        config.setdefault('callback_origins', []).append(origin)
        config_path.write_text(json.dumps(config, indent=2));config_path.chmod(0o600)
        destination = api(bank + '/callbacks', {'name': 'Qualification receiver', 'url': origin + '/notifications', 'request_key': 'receiver'})
        callback_state['secret'] = destination['signing_secret']
        api(bank + '/callbacks', {'name': 'Blocked receiver', 'url': 'http://127.0.0.1:4830/admin', 'request_key': 'unsafe-destination'}, expected=403)
        callback_op = api(bank + '/operations', {'payer_id': payer['account_id'], 'payee_id': payee['account_id'], 'amount_paise': 100, 'request_key': 'callback-payment', 'callback_id': destination['id']})
        deliveries = wait(lambda: api(bank)['notifications'], lambda x: any(n['operation_id'] == callback_op['id'] and n['state'] == 'delivered' for n in x))
        check('Actual external callback passes HMAC verification', len(callback_state['accepted']) == 1)
        event = next(n for n in deliveries if n['operation_id'] == callback_op['id'])
        with control.store.reader() as db:
            check('Callback evidence records the actual HTTP 204 acknowledgement', db.execute('SELECT http_status FROM bank_notification_attempts WHERE event_id=?', (event['id'],)).fetchone()[0] == 204)
        control.callbacks.deliver(tenant, env, event['id'])
        check('Delivered callback retry does not send a new effect', len(callback_state['accepted']) == 1 and callback_state['duplicates'] == 0)
        specification = {'openapi': '3.0.3', 'info': {'title': 'ProviderQualification', 'version': '1.0'}, 'paths': {'/account': {'get': {'operationId': 'readAccount', 'responses': {'200': {'description': 'Synthetic provider response', 'content': {'application/json': {'schema': {'type': 'object', 'properties': {'status': {'type': 'string'}}}, 'example': {'status': 'active'}}}}}}}}}
        imported = api(bank + '/contracts', {'specification': specification})
        check('Actual provider service imports an environment-namespaced contract', imported['name'].startswith('Avenra-' + env))
        services = api(bank + '/contracts')
        service = next(x for x in services if x['name'] == imported['name'])
        result = api(bank + '/contracts/' + service['id'] + '/invoke', {'method': 'GET', 'path': '/account'})
        check('External mock executes through Avenra contract adapter', result == {'status': 'active'}, result)
        api(bank + '/contracts', {'specification': {**specification, 'paths': {'/bad': {'$ref': 'http://169.254.169.254/latest'}}}}, expected=400)
        api(bank + '/core/clients', expected=503)
        config = json.loads(config_path.read_text())
        binding = config.get('services', {}).get('banking_core')
        if binding:
            product_env = next(k.split('/', 1)[1] for k in binding['tenant_bindings'] if k.startswith(tenant + '/'))
        else:
            product = api('/api/tenants/' + tenant + '/environments', {'name': 'Banking products', 'fixture_profile': 'banking'})
            product_env = product['id']
            wait(lambda: api('/api/tenants/' + tenant + '/environments/' + product_env + '/overview'), lambda x: x['environment']['status'] == 'active')
            secret = json.loads((ROOT / '.local/data/product-credentials.json').read_text())
            config.setdefault('services', {})['banking_core'] = {'origin': 'http://127.0.0.1:4841', 'classification': 'simulation', 'tenant_bindings': {tenant + '/' + product_env: 'default'}, **secret}
            config_path.write_text(json.dumps(config, indent=2));config_path.chmod(0o600)
        product_base = '/api/tenants/' + tenant + '/environments/' + product_env + '/banking'
        report['product_environment_id'] = product_env
        offices = api(product_base + '/core/offices')
        check('Avenra reaches the actual source-built banking core', isinstance(offices, list) and offices[0]['id'] == 1)
        today = time.strftime('%d %B %Y')
        command_body = {'action': 'create_client', 'request_key': uid('client'), 'body': {'officeId': 1, 'legalFormId': 1, 'firstname': 'Avenra', 'lastname': 'TestBorrower', 'externalId': uid('borrower'), 'active': True, 'activationDate': today, 'dateFormat': 'dd MMMM yyyy', 'locale': 'en'}}
        command = api(product_base + '/core-commands', command_body)
        command = wait(lambda: api(product_base + '/core-commands/' + command['id']), lambda x: x['state'] in {'succeeded', 'rejected'})
        check('Source-built banking core creates a real test client', command['state'] == 'succeeded', command.get('result_json'))
        check('Repeated core request preserves its durable identity', api(product_base + '/core-commands', command_body)['id'] == command['id'])
        native_client = json.loads(command['result_json'])['clientId']
        check('Core client reads remain within its configured tenant', api(product_base + '/core/clients/' + str(native_client))['id'] == native_client)
        api(product_base + '/core-commands', {**command_body, 'body': {**command_body['body'], 'lastname': 'Conflict'}}, expected=409)
        report['passed'] = True
    except Exception as error:
        report['error'] = str(error)
        import traceback
        report['traceback'] = traceback.format_exc()
    finally:
        if receiver:
            receiver.shutdown();receiver.server_close();receiver_thread.join()
        if 'origin' in locals():
            config_path = ROOT / '.local/data/services.json'
            config = json.loads(config_path.read_text())
            config['callback_origins'] = [x for x in config.get('callback_origins', []) if x != origin]
            config_path.write_text(json.dumps(config, indent=2));config_path.chmod(0o600)
        if control:
            control.close()
    report['application_fingerprint'] = application_fingerprint()
    path = ROOT / '.local/banking-http-verification.json'
    content = json.dumps(report, indent=2) + '\n'
    path.write_text(content)
    attempts = ROOT / '.local/verification-attempts'
    attempts.mkdir(exist_ok=True)
    (attempts / ('http-' + str(time.time_ns()) + '.json')).write_text(content)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'report': str(path), 'error': report.get('error')}))
    if not report['passed']:
        print(report.get('traceback', ''))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
