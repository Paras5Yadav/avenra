#!/usr/bin/env python3
"""Back up control/accounting stores and qualify restores in isolated databases."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.app import App
from avenra.util import application_fingerprint
from avenra.gateways import AccountingGateway
from avenra.local_database import admin_dsn
from avenra.util import canonical, digest
from avenra.lab_identity import snapshot_private_banks, restore_private_banks
from avenra.deployment_backup import snapshot as snapshot_deployment, restore as restore_deployment
from avenra.aa.recovery import snapshot as snapshot_financial_data, restore as restore_financial_data
from avenra.private_config import write_document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-restore', action='store_true')
    args = parser.parse_args()
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    pg = Path('/opt/homebrew/opt/postgresql@16/bin')
    directory = ROOT / '.local/backups' / ('snapshot-' + str(time.time_ns()))
    directory.mkdir(parents=True, mode=0o700)
    report = {'passed': False, 'scope': 'Control/accounting stores and private synthetic bank identity/receipt snapshots. Off-host infrastructure, external bank, product and monitoring restore qualification is separate.', 'checks': [], 'directory': str(directory), 'created_at': time.time()}
    control_dsn = (ROOT / '.local/data/control.dsn').read_text().strip()
    accounting_dsn = (ROOT / '.local/data/accounting.dsn').read_text().strip()
    admin = admin_dsn(ROOT)
    environment = os.environ.copy()
    pgpass = directory / 'pgpass.private'
    lines = []
    for dsn in [admin, control_dsn, accounting_dsn]:
        info = conninfo_to_dict(dsn)
        def escape(value):
            return str(value).replace('\\', '\\\\').replace(':', '\\:')
        lines.append(':'.join(escape(info.get(x, '')) for x in ['host', 'port', 'dbname', 'user', 'password']))
    pgpass.write_text('\n'.join(lines) + '\n');pgpass.chmod(0o600)
    environment['PGPASSFILE'] = str(pgpass)
    def cli(dsn):
        info = conninfo_to_dict(dsn)
        return ['--host=' + info['host'], '--port=' + info['port'], '--username=' + info['user'], '--dbname=' + info['dbname'], '--no-password']
    def check(name, value, detail=None):
        report['checks'].append({'name': name, 'passed': bool(value), 'detail': detail})
        if not value:
            raise AssertionError(name)
    def console(action):
        subprocess.run([sys.executable, str(ROOT / 'scripts/local.py'), action], check=True, stdout=subprocess.DEVNULL)
    app = restored = process = None
    console('stop-console')
    try:
        app = App(ROOT / '.local/data', control_dsn=control_dsn)
        check('Source audit chain verifies before backup', app.store.verify_audit()['valid'])
        with app.store.reader() as db:
            fixture = db.execute("SELECT * FROM environments WHERE status='active' ORDER BY created_at LIMIT 1").fetchone()
            expected_counts = {t: db.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in ['tenants', 'environments', 'payments', 'bank_operations', 'external_commands', 'audit_events', 'evidence', 'bank_test_runs', 'simulation_profiles', 'simulation_plans', 'simulation_runs', 'simulation_steps', 'simulation_messages', 'simulation_clocks', 'simulation_clock_events', 'upi_participants', 'upi_keys', 'upi_devices', 'upi_links', 'upi_payments', 'upi_envelopes', 'upi_deliveries', 'upi_inbox', 'upi_actor_views', 'upi_events', 'participant_connections', 'participant_connection_checks', 'participant_connection_bindings']}
            from avenra_fidelity.runtime import TABLES as lifecycle_tables
            expected_counts.update({t: db.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in lifecycle_tables})
            from avenra.production import TABLES as runtime_tables
            expected_counts.update({t: db.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in runtime_tables})
            from avenra.aa.manager import TABLES as financial_data_tables
            expected_counts.update({t: db.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in financial_data_tables})
            from avenra.operations import TABLES as operations_tables
            expected_counts.update({t: db.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in operations_tables})
            operation_order = {'ops_schema':'version','ops_policies':'id','ops_bindings':'tenant_id,environment_id',
                               'ops_observations':'tenant_id,environment_id','ops_rejections':'tenant_id,environment_id,slot,code',
                               'ops_monitor':'singleton','ops_alerts':'sequence','ops_alert_events':'sequence'}
            def operational_hashes(connection):
                hashes = {}
                for table, order in operation_order.items():
                    hashed = hashlib.sha256()
                    for item in connection.execute('SELECT * FROM ' + table + ' ORDER BY ' + order):
                        hashed.update(canonical(dict(item)).encode() + b'\n')
                    hashes[table] = hashed.hexdigest()
                return hashes
            expected_operations = operational_hashes(db)
        expected_accounts = app.accounting.accounts(fixture['ledger_name'])
        expected_transactions = app.accounting.transactions(fixture['ledger_name'], all_pages=True)
        database_settings={}
        for name,dsn in [('control',control_dsn),('accounting',accounting_dsn)]:
            with psycopg.connect(dsn,autocommit=True) as source:database_settings[name]={'timezone':source.execute('SHOW timezone').fetchone()[0]}
        check('Accounting snapshot captures the qualified UTC session policy',database_settings['accounting']['timezone']=='UTC')
        write_document(directory/'database-settings.json',{'format':'avenra-database-settings/1','databases':database_settings})
        for name, dsn in [('control', control_dsn), ('accounting', accounting_dsn)]:
            target = directory / (name + '.dump')
            subprocess.run([str(pg / 'pg_dump'), *cli(dsn), '--format=custom', '--no-owner', '--file=' + str(target)], env=environment, check=True, capture_output=True)
            target.chmod(0o600)
            check(name + ' backup archive created', target.stat().st_size > 0)
        role_file = directory / 'database-roles.sql.private'
        role_file.touch(mode=0o600)
        role_arguments = [x for x in cli(admin) if not x.startswith('--dbname=')]
        subprocess.run([str(pg / 'pg_dumpall'), *role_arguments, '--database=' + conninfo_to_dict(admin)['dbname'], '--globals-only', '--file=' + str(role_file)], env=environment, check=True, capture_output=True)
        for name in ['control.dsn', 'accounting.dsn', 'database-admin.dsn', 'services.json', 'identity.json', 'product-credentials.json']:
            source = ROOT / '.local/data' / name
            if source.exists():
                shutil.copyfile(source, directory / (name + '.private'));(directory / (name + '.private')).chmod(0o600)
        shutil.copyfile(ROOT / '.local/data/evidence.key', directory / 'evidence.key')
        (directory / 'evidence.key').chmod(0o600)
        synthetic_manifest = snapshot_private_banks(ROOT/'.local/data',directory/'synthetic-components')
        report['synthetic_components'] = {'workers':len(synthetic_manifest['workers']),'identities':len(synthetic_manifest['identities'])}
        report['deployment_components'] = snapshot_deployment(ROOT/'.local/data', directory/'deployment-components')
        report['financial_data_components'] = snapshot_financial_data(ROOT/'.local/data', directory/'financial-data-components')
        report['artifacts'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir() if p.suffix == '.dump' or p.name == 'evidence.key'}
        if args.verify_restore:
            restore_name = 'avenra_restore_' + str(time.time_ns())
            connections = {}
            for name in ['control', 'accounting']:
                database = restore_name + '_' + name
                with psycopg.connect(admin, autocommit=True) as db:
                    db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
                dsn = make_conninfo(**{**conninfo_to_dict(admin), 'dbname': database})
                connections[name] = dsn
                info = conninfo_to_dict(dsn)
                with pgpass.open('a') as f:
                    f.write(':'.join(escape(info.get(x, '')) for x in ['host', 'port', 'dbname', 'user', 'password']) + '\n')
                subprocess.run([str(pg / 'pg_restore'), *cli(dsn), '--exit-on-error', '--no-owner', str(directory / (name + '.dump'))], env=environment, check=True, capture_output=True)
                with psycopg.connect(admin,autocommit=True) as db:
                    db.execute(sql.SQL('ALTER DATABASE {} SET timezone TO {}').format(sql.Identifier(database),sql.Literal(database_settings[name]['timezone'])))
            restore_data = directory / 'restored-control';restore_data.mkdir(mode=0o700)
            shutil.copyfile(directory / 'evidence.key', restore_data / 'evidence.key');(restore_data / 'evidence.key').chmod(0o600)
            restored = App(restore_data, control_dsn=connections['control'])
            synthetic_restored = restore_private_banks(directory/'synthetic-components',restore_data)
            deployment_restored = restore_deployment(directory/'deployment-components', directory/'inactive-deployment-recovery')
            check('Private deployment settings survive restore without activating network endpoints', deployment_restored['valid'] and deployment_restored['activation'] == 'inactive_staging_only', deployment_restored)
            financial_restored = restore_financial_data(directory/'financial-data-components', directory/'inactive-financial-data-recovery')
            check('Financial-data role checkpoints survive inactive restoration', financial_restored['valid'] and financial_restored['activation']=='inactive', financial_restored)
            check('Private synthetic bank identities and receipt databases survive isolated restore',synthetic_restored['valid'],synthetic_restored)
            with restored.store.reader() as db:
                check('Every retained financial/control count survives restore', all(db.execute('SELECT count(*) FROM ' + t).fetchone()[0] == count for t, count in expected_counts.items()))
                check('Operational policies, observations, counters and complete incident histories survive exact restoration', operational_hashes(db) == expected_operations)
                policy_integrity = []
                for policy in db.execute('SELECT * FROM ops_policies'):
                    hashed, signature = restored.runtime.signed(json.loads(policy['document_json']))
                    policy_integrity.append(hashed == policy['document_hash'] and hmac.compare_digest(signature, policy['signature']))
                check('Restored operational policy signatures retain their original installation authority', all(policy_integrity), {'policies_checked':len(policy_integrity)})
                signatures = []
                for item in db.execute('SELECT * FROM evidence'):
                    fingerprint = digest(json.loads(item['manifest_json']))
                    expected = hmac.new(restored.store.signing_key, fingerprint.encode(), hashlib.sha256).hexdigest()
                    signatures.append(fingerprint == item['manifest_hash'] and hmac.compare_digest(expected, item['signature']))
                for item in db.execute('SELECT evidence_json FROM bank_test_runs WHERE evidence_json IS NOT NULL'):
                    value = json.loads(item['evidence_json'])
                    expected = hmac.new(restored.store.signing_key, digest(value['manifest']).encode(), hashlib.sha256).hexdigest()
                    signatures.append(hmac.compare_digest(expected, value['signature']))
                for item in db.execute('SELECT evidence_json FROM simulation_runs WHERE evidence_json IS NOT NULL'):
                    value = json.loads(item['evidence_json'])
                    expected = hmac.new(restored.store.signing_key, digest(value['manifest']).encode(), hashlib.sha256).hexdigest()
                    signatures.append(digest(value['manifest']) == value['manifest_hash'] and hmac.compare_digest(expected, value['signature']))
                for item in db.execute('SELECT evidence_json FROM upi_payments WHERE evidence_json IS NOT NULL'):
                    value = json.loads(item['evidence_json'])
                    signatures.append(restored.verify_evidence(value)['valid'])
                for item in db.execute('SELECT * FROM participant_connection_checks'):
                    signatures.append(digest(json.loads(item['manifest_json'])) == item['manifest_hash'] and hmac.compare_digest(item['signature'], restored.upi.connections._signature(item['manifest_hash'])))
            check('Retained evidence signatures survive isolated restore', bool(signatures) and all(signatures))
            check('Restored audit chain is intact', restored.store.verify_audit()['valid'])
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0));port = listener.getsockname()[1]
            runtime_env = os.environ.copy();runtime_env['POSTGRES_URI'] = connections['accounting']
            log = (directory / 'restored-accounting.log').open('wb')
            process = subprocess.Popen([str(ROOT / '.local/bin/accounting'), 'serve', '--bind', '127.0.0.1:' + str(port), '--auto-upgrade', '--postgres-max-open-conns', '3'], env=runtime_env, stdout=log, stderr=subprocess.STDOUT)
            log.close()
            gateway = AccountingGateway('http://127.0.0.1:' + str(port))
            for _ in range(80):
                try:
                    gateway.health();break
                except Exception:
                    if process.poll() is not None:
                        raise RuntimeError('Restored accounting engine did not start; inspect its retained log.')
                    time.sleep(.25)
            else:
                raise TimeoutError('Restored accounting service did not become ready.')
            check('Restored financial authority returns exactly the original balances', digest(gateway.accounts(fixture['ledger_name'])) == digest(expected_accounts))
            check('Restored financial authority returns exactly the original journal', digest(gateway.transactions(fixture['ledger_name'], all_pages=True)) == digest(expected_transactions))
            from datetime import datetime
            ledger='avn_restore_clock_'+str(time.time_ns());gateway.create(ledger,ledger);reference='restore-clock-'+str(time.time_ns());before=time.time()
            posted=gateway.post(ledger,{'postings':[{'source':'world','destination':'fixture:clock','asset':'INR/2','amount':1}],'reference':reference},reference);after=time.time()
            epoch=datetime.fromisoformat(posted['timestamp'].replace('Z','+00:00')).timestamp()
            check('A fresh native write after restore preserves the qualified UTC timestamp policy',before-1<=epoch<=after+1)
            report['restored_databases'] = {k: conninfo_to_dict(v)['dbname'] for k, v in connections.items()}
        report['passed'] = True
    except Exception as e:
        import traceback
        report.update(error=type(e).__name__ + ': ' + str(e), traceback=traceback.format_exc())
        if isinstance(e, subprocess.CalledProcessError) and e.stderr:
            failure_log = directory / 'failed-command.log'
            failure_log.write_bytes(e.stderr);failure_log.chmod(0o600)
    finally:
        if process:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill();process.wait()
        if restored:restored.close()
        if app:app.close()
        pgpass.unlink(missing_ok=True)
        console('start')
    report['application_fingerprint'] = application_fingerprint()
    content = json.dumps(report, indent=2) + '\n'
    (directory / 'verification.json').write_text(content)
    (ROOT / '.local/backup-verification.json').write_text(content)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'directory': str(directory), 'error': report.get('error')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
