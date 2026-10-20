#!/usr/bin/env python3
"""Check durable financial identity across a restart of tracked local services."""
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    result_path = ROOT / '.local/verification.json'
    report = json.loads(result_path.read_text())
    if not report.get('passed'):
        raise SystemExit('Finish the integration verification before checking restart persistence.')
    credentials = json.loads((ROOT / '.local/data/credentials.json').read_text())
    path = f"/api/tenants/{credentials['tenant_id']}/environments/{report['details']['environment_id']}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def call(endpoint, body=None):
        req = urllib.request.Request('http://127.0.0.1:4827' + path + endpoint,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + credentials['api_key'], 'Content-Type': 'application/json'})
        with opener.open(req, timeout=8) as response:
            return json.load(response)
    p = call('/payments/' + report['details']['payment_id'])
    before = call('/overview')
    before_balances = {a['id']: a['balance_paise'] for a in before['accounts']}
    evidence_id = report['details']['runs']['lost_response']
    evidence = call('/runs/' + evidence_id + '/evidence')
    subprocess.run([sys.executable, str(ROOT / 'scripts/local.py'), 'stop'], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/local.py'), 'start'], cwd=ROOT, check=True)
    replay = call('/payments', {key: p[key] for key in ('payer_id','payee_id','amount_paise','request_key','fault')})
    after = call('/overview')
    retained = call('/runs/' + evidence_id + '/evidence')
    checks = [
        ('Financial identity survives process restart', replay['id'] == p['id'] and replay['ledger_transaction_id'] == p['ledger_transaction_id']),
        ('Restart replay causes no additional debit', {a['id']:a['balance_paise'] for a in after['accounts']} == before_balances),
        ('Retained evidence survives process restart', retained['valid'] and retained['manifest_hash'] == evidence['manifest_hash'])
    ]
    for name, passed in checks:
        print(('PASS ' if passed else 'FAIL ') + name)
        report['checks'].append({'name': name, 'passed': passed, 'detail': None})
    report['details']['restart_exercised'] = True
    report['passed'] = all(c['passed'] for c in report['checks'])
    result_path.write_text(json.dumps(report, indent=2) + '\n')
    if not report['passed']:
        raise SystemExit('Restart verification failed.')


if __name__ == '__main__':
    main()
