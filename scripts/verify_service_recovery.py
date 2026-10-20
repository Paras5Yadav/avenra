#!/usr/bin/env python3
"""Drop real service response bytes, then recover original identities and effects."""
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.app import App
from avenra.util import application_fingerprint
from avenra.errors import DomainError
from avenra.gateways import FaultGateway
from avenra.services import Transport
from avenra.util import uid


def main():
    settings = ROOT / '.local/data/services.json'
    original = settings.read_bytes()
    config = json.loads(original)
    report = {'passed': False, 'checks': [], 'created_at': time.time()}
    app = None
    def save():
        settings.write_text(json.dumps(config, indent=2));settings.chmod(0o600)
    def check(name, value, detail=None):
        report['checks'].append({'name': name, 'passed': bool(value), 'detail': detail})
        if not value:
            raise AssertionError(name)
    def console(action):
        subprocess.run([sys.executable, str(ROOT / 'scripts/local.py'), action], check=True, stdout=subprocess.DEVNULL)
    console('stop-console')
    try:
        app = App(ROOT / '.local/data', control_dsn=(ROOT / '.local/data/control.dsn').read_text().strip())
        binding = next(iter(config['services']['banking_core']['tenant_bindings']))
        tenant, env = binding.split('/')
        client, _ = app.adapters.client('banking_core', tenant, env)
        savings = json.loads((ROOT / '.local/product-verification.json').read_text())['savings_id']
        before = app.adapters.core_read(tenant, env, 'savings', str(savings))['summary']['accountBalance']
        fault = FaultGateway(upstream='127.0.0.1:4841')
        proxy_id = uid('core_recovery')
        with fault.lost_response(proxy_id) as origin:
            config['services']['banking_core']['origin'] = origin;save()
            command = app.adapters.core_command(tenant, env, {'action': 'deposit_savings', 'resource_id': savings, 'request_key': uid('lost_deposit'), 'body': {'locale': 'en', 'dateFormat': 'dd MMMM yyyy', 'transactionDate': time.strftime('%d %B %Y'), 'transactionAmount': 100, 'paymentTypeId': 1}})
            unknown = app.adapters.process_core(tenant, env, command['id'])
            check('Lost core response preserves an unknown command', unknown['state'] == 'unknown')
            native = client.call('GET', '/fineract-provider/api/v1/savingsaccounts/' + str(savings))
            check('Native deposit actually committed before its response was lost', native['summary']['accountBalance'] == before + 100)
            config['services']['banking_core']['origin'] = 'http://127.0.0.1:4843';save()
            try:
                app.adapters.reconcile_core(tenant, env, command['id'])
            except DomainError as e:
                check('Recovery refuses a changed service binding', e.code == 'BINDING_CHANGED')
            else:
                check('Recovery refuses a changed service binding', False)
            config['services']['banking_core']['origin'] = origin;save()
            fault.http.call('DELETE', '/proxies/avn_' + proxy_id + '/toxics/drop_response')
            app.adapters.reconcile_core(tenant, env, command['id'])
            recovered = app.adapters.process_core(tenant, env, command['id'])
            native = client.call('GET', '/fineract-provider/api/v1/savingsaccounts/' + str(savings))
            check('Core reconciliation retrieves success using the original command key', recovered['state'] == 'succeeded', recovered.get('error'))
            check('Core reconciliation never posts the deposit twice', native['summary']['accountBalance'] == before + 100)
        config = json.loads(original);save()
        fixture = json.loads((ROOT / '.local/banking-http-verification.json').read_text())['environment_id']
        banking = app.banking.overview(tenant, fixture)
        payer = next(x for x in banking['accounts'] if x['vpa'] == 'payer@bank_a')['account_id']
        payee = next(x for x in banking['accounts'] if x['vpa'] == 'merchant@bank_b')['account_id']
        fault = FaultGateway(upstream='127.0.0.1:4842')
        with fault.lost_response(uid('analysis_recovery')) as origin:
            config['services']['monitoring']['origin'] = origin;save()
            operation = app.banking.submit(tenant, fixture, {'payer_id': payer, 'payee_id': payee, 'amount_paise': 1000, 'request_key': uid('monitored_payment')})
            operation = app.banking.process(tenant, fixture, operation['id'])
            analysis = app.monitoring.submit(tenant, fixture, {'operation_id': operation['id']})
            for _ in range(20):
                observed = app.monitoring.process(tenant, fixture, analysis['id'])
                if observed['state'] == 'succeeded':
                    break
                time.sleep(.3)
            check('Monitoring completes despite real response loss on both ingestions', observed['state'] == 'succeeded' and observed.get('evidence', {}).get('valid'), observed.get('error'))
            receipts = observed['evidence']['manifest']['ingestion_receipts']
            check('Monitoring proves both lost acknowledgements from its native inbox', receipts == {'008': 'recovered_from_native_inbox', '002': 'recovered_from_native_inbox'}, receipts)
            evaluation_id = observed['evidence']['manifest']['report']['report']['evaluationID']
            repeated = app.monitoring.process(tenant, fixture, analysis['id'])
            check('Monitoring replay preserves its completed native evaluation', repeated['evidence']['manifest']['report']['report']['evaluationID'] == evaluation_id)
        check('Recovery qualification retains a valid shared audit chain', app.store.verify_audit()['valid'])
        report['passed'] = True
    except Exception as error:
        import traceback
        report.update(error=str(error), traceback=traceback.format_exc())
    finally:
        settings.write_bytes(original);settings.chmod(0o600)
        if app:
            app.close()
        console('start')
    report['application_fingerprint'] = application_fingerprint()
    path = ROOT / '.local/service-recovery-verification.json'
    content = json.dumps(report, indent=2) + '\n'
    path.write_text(content)
    (ROOT / '.local/verification-attempts' / ('service-recovery-' + str(time.time_ns()) + '.json')).write_text(content)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'error': report.get('error'), 'report': str(path)}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
