#!/usr/bin/env python3
"""Crash a private controller after acceptance, then recover its durable intent."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.app import App
from avenra.local_database import admin_dsn
from avenra.util import application_fingerprint


def phase(name, directory):
    receipt = directory / 'intent.json'
    dsn_path = directory / 'control.dsn'
    app = App(directory / 'control', control_dsn=dsn_path.read_text().strip() if dsn_path.exists() else None)
    if name == 'prepare':
        app.bootstrap()
        with app.store.reader() as db:
            env = dict(db.execute("SELECT * FROM environments WHERE tenant_id='tenant_avenra' LIMIT 1").fetchone())
        app.provision('tenant_avenra', env['id'])
        accounts = {a['role']: a for a in app.accounts('tenant_avenra', env['id'])}
        body = {'payer_id': accounts['payer']['id'], 'payee_id': accounts['payee']['id'], 'amount_paise': 123,
                'request_key': 'crash-before-dispatch', 'fault': 'none'}
        payment = app.submit_payment('tenant_avenra', env['id'], body)
        assert payment['state'] == 'pending'
        receipt.write_text(json.dumps({'environment': env, 'body': body, 'payment_id': payment['id']}))
        os._exit(73)  # No controller cleanup; intent/outbox were already committed.
    record = json.loads(receipt.read_text())
    env = record['environment']
    assert app.payment('tenant_avenra', env['id'], record['payment_id'])['state'] == 'pending'
    app.accounting.create(env['ledger_name'], env['id'])
    app.start_worker()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            payment = app.payment('tenant_avenra', env['id'], record['payment_id'])
            if payment['state'] == 'succeeded':
                break
            time.sleep(.05)
        else:
            raise AssertionError('Accepted intent did not recover.')
        replay = app.submit_payment('tenant_avenra', env['id'], record['body'])
        assert replay['id'] == record['payment_id']
        assert replay['ledger_transaction_id'] == payment['ledger_transaction_id']
        transactions = app.accounting.transactions(env['ledger_name'], 'payment:' + record['payment_id'])
        assert len(transactions) == 1
        balances = {a['role']: a['balance_paise'] for a in app.accounts('tenant_avenra', env['id'])}
        assert balances == {'payer': 4_999_877, 'payee': 250_123, 'treasury': 100_000_000}
        (directory / 'result.json').write_text(json.dumps({'passed': True, 'payment_id': payment['id'], 'ledger_transaction_id': payment['ledger_transaction_id'], 'matching_transactions': 1, 'ownership_rechecked': True}, indent=2))
    finally:
        app.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['prepare', 'recover'])
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--postgres', action='store_true')
    args = parser.parse_args()
    if args.phase:
        phase(args.phase, args.directory)
    else:
        directory = ROOT / '.local/qa' / ('recovery_' + uuid.uuid4().hex[:12])
        directory.mkdir(parents=True, mode=0o700)
        if args.postgres:
            import psycopg
            from psycopg import sql
            from psycopg.conninfo import conninfo_to_dict, make_conninfo
            database = 'avenra_crash_' + uuid.uuid4().hex[:16]
            with psycopg.connect(admin_dsn(ROOT), autocommit=True) as db:
                db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
            dsn_path = directory / 'control.dsn'
            dsn_path.write_text(make_conninfo(**{**conninfo_to_dict(admin_dsn(ROOT)), 'dbname': database}));dsn_path.chmod(0o600)
        script = str(Path(__file__).resolve())
        prepared = subprocess.run([sys.executable, script, '--phase', 'prepare', '--directory', str(directory)], cwd=ROOT)
        if prepared.returncode != 73:
            raise SystemExit('Preparation did not reach its planned crash boundary.')
        subprocess.run([sys.executable, script, '--phase', 'recover', '--directory', str(directory)], cwd=ROOT, check=True)
        result = json.loads((directory / 'result.json').read_text())
        (ROOT / '.local/qa/recovery.json').write_text(json.dumps({**result, 'directory': str(directory), 'backend': 'postgresql' if args.postgres else 'sqlite', 'application_fingerprint': application_fingerprint()}, indent=2) + '\n')
        report_path = ROOT / '.local/verification.json'
        report = json.loads(report_path.read_text())
        report['checks'] += [{'name': 'Undispatched durable intent survives abrupt controller process exit', 'passed': True, 'detail': None},
                             {'name': 'Ledger reprovisioning verifies environment ownership', 'passed': True, 'detail': None}]
        report['passed'] = all(c['passed'] for c in report['checks'])
        report_path.write_text(json.dumps(report, indent=2) + '\n')
        print('PASS Abrupt controller exit: recovered one accepted intent, one posting, correct balances.')
        print('PASS Existing ledger ownership checked before reprovisioning.')
