#!/usr/bin/env python3
"""Real predecessor migration and original-ID payment evidence retry recovery."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
import uuid
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra import release_bundle as bundle, release_runtime as releases
from avenra.database_deployment import upgrade_work_queue
from avenra.deployment_identity import issue, client_context
from avenra.errors import DomainError
from avenra.gateways import AccountingGateway, NoRedirect
from avenra.lab_identity import private_read, private_write
from avenra.private_config import read_document, write_document
from avenra.runtime_rehearsal import free_port, wait
from avenra.util import application_fingerprint, canonical, digest, uid
from scripts.qualification_storage import QualificationStorage, StorageRefused, record_refusal, harness_case_pack_hash


def case_pack_hash():
    return harness_case_pack_hash(ROOT, 'verify_work_queue.py')


def complete_evidence(value):
    """Readiness and matrix resume require the intact private attempt record."""
    try:
        path = Path(value['details']['path'])
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT / '.local/qa'):
            return False
        raw = private_read(path, maximum=2_000_000)
        full = json.loads(raw)
        if not isinstance(full, dict) or not isinstance(full.get('checks'), list) or not isinstance(full.get('payments'), dict):
            return False
        keys = ['passed', 'checks', 'directory', 'application_fingerprint', 'case_pack_sha256', 'predecessor_artifact', 'candidate_artifact', 'payments', 'created_at', 'completed_at', 'production_ready', 'official_conformance', 'scope']
        return (hashlib.sha256(raw).hexdigest() == value['details']['sha256'] and all(full.get(key) == value.get(key) for key in keys) and
                full.get('passed') is True and len(full['checks']) >= 35 and all(isinstance(check, dict) and check.get('passed') is True for check in full['checks']) and
                set(full.get('payments', {})) == {'predecessor', 'automatic', 'exhausted'} and len(set(full['payments'].values())) == 3)
    except (OSError, ValueError, KeyError, TypeError, DomainError):
        return False


def gateway_child(data, config_path, fault_path):
    """Fail real GETs after settlement; never change a financial request/result."""
    import psycopg
    import uvicorn
    from avenra.service_gateway import Gateway
    from avenra.deployment_identity import paths, server_context
    config = read_document(config_path)
    gateway = Gateway(config)
    original, lock = gateway.forward, threading.Lock()

    def forward(method, target, body, headers):
        with lock:
            rule = read_document(fault_path, {})
            if rule.get('mode') in {'once', 'always'} and method == 'GET' and '/transactions?' in target:
                match = json.loads(body or b'{}').get('$match', {}).get('reference')
                with psycopg.connect(private_read(Path(data) / 'control.dsn').decode().strip(), autocommit=True) as db:
                    row = db.execute('''SELECT p.state,p.evidence_json FROM upi_payments p
                        JOIN bank_legs l ON l.operation_id=p.operation_id WHERE p.id=%s AND l.id=%s''', (rule['payment_id'], match)).fetchone()
                if row and row[0] == 'succeeded' and row[1] is None:
                    events = rule.get('events', [])
                    if rule['mode'] == 'always' or not events:
                        rule['events'] = [*events, {'at': time.time(), 'reference': match, 'method': method, 'status': 502}]
                        write_document(fault_path, rule)
                        raise OSError('Qualification: original-payment evidence journal read unavailable')
        return original(method, target, body, headers)

    gateway.forward = forward
    identity = paths(data, config['identity_reference'])
    context = server_context(data, config['identity_reference'], True)
    uvicorn.run(gateway, host='127.0.0.1', port=config['port'], http='h11', ws='none', lifespan='off', access_log=False,
                proxy_headers=False, server_header=False, limit_concurrency=32, timeout_keep_alive=3,
                ssl_certfile=str(identity['cert']), ssl_keyfile=str(identity['key']), ssl_context_factory=lambda *_: context)


def crash_child(instructions):
    from avenra import outbox_retry
    original = outbox_retry.install
    def die(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(76)
    outbox_retry.install = die
    upgrade_work_queue(**json.loads(private_read(instructions)))
    return 9


def run(storage):
    import psycopg
    from psycopg import sql
    folder = ROOT / '.local/qa' / uid('work_queue')
    folder.mkdir(mode=0o700, parents=True)
    report = {'passed': False, 'checks': [], 'created_at': time.time(), 'directory': str(folder),
              'application_fingerprint': application_fingerprint(), 'case_pack_sha256': case_pack_hash(),
              'production_ready': False, 'official_conformance': False,
              'scope': 'Preserved control-v9 predecessor, restricted PostgreSQL control-v10 migration, packaged HTTPS/mTLS API/workers, actual journal read failures after native settlement, durable bounded errors, worker restart, stale lease fencing and original-ID evidence recovery. Local synthetic profile only.'}
    children = []
    def check(name, condition):
        report['checks'].append({'name': name, 'passed': bool(condition)})
        print(json.dumps({'check': name, 'passed': bool(condition)}), flush=True)
        if not condition: raise AssertionError(name)
    def denied(name, callback, code):
        try: callback()
        except DomainError as error: return check(name, error.code == code)
        check(name, False)
    def start(command, name):
        path = folder / (name + '.log')
        with path.open('ab') as output:
            path.chmod(0o600)
            child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        children.append(child)
        return child
    def stop(child):
        if child.poll() is None:
            child.terminate()
            try: child.wait(timeout=20)
            except subprocess.TimeoutExpired: child.kill();child.wait(timeout=5)
    try:
        baseline = json.loads(private_read(ROOT / '.local/release-baselines/before-work-queue.json'))
        old = bundle.verify_installed(baseline['directory'], baseline['trust_file'])
        releases.runtime_receipt(old, baseline['runtime_directory'])
        check('The predecessor is a genuine signed control-v9 release without the new error counter', old['artifact_id'] == baseline['artifact_id'] and old['manifest']['schemas'] == bundle.OPERATIONS_SCHEMAS and not (Path(old['directory']) / 'avenra/outbox_retry.py').exists())
        storage.checkpoint('signed_candidate')
        key, trust = folder / 'signing.raw', folder / 'trust.raw'
        bundle.new_key(key, trust)
        archive = folder / 'candidate.tar.gz'
        bundle.build(ROOT, ROOT / '.local/release-inputs/wheels', key, archive, 'worker-retry-qualification')
        new = bundle.install(archive, trust, folder / 'artifacts')
        runtime = folder / 'runtime'
        bundle.prepare_runtime(new['directory'], trust, runtime)
        data = folder / 'deployment'
        installer_code = '''import sys,json,hashlib,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from avenra.database_deployment import provision
from avenra.local_database import admin_dsn
from avenra.app import App
from avenra.production import ROLE_CAPABILITIES,POLICY_DEFAULT
from avenra.private_config import write_document
from avenra.synthetic_lab import fixture
p=Path(sys.argv[2]);provision(p,admin_dsn(sys.argv[3]))
key=json.loads((p/'credentials.json').read_text())['api_key']
app=App(p,control_dsn=(p/'control.dsn').read_text().strip())
try:
 value=fixture(app,'tenant_avenra')
 app.runtime.grant('tenant_avenra',value['environment_id'],{'principal':'service:'+hashlib.sha256(('Bearer '+key).encode()).hexdigest(),'capabilities':ROLE_CAPABILITIES['administrator'],'environment_ids':[value['environment_id']],'expires_at':time.time()+3600},'offline-worker-retry-installer')
 app.runtime.publish_policy('tenant_avenra',value['environment_id'],{**POLICY_DEFAULT,'burst':1000,'rate_per_minute':60000,'request_key':'worker-retry-qualification'},'offline-worker-retry-installer')
 write_document(p/'qualification-fixture.json',value)
finally:app.close()
'''
        installer = start([str(Path(baseline['runtime_directory']) / 'bin/python'), '-I', '-B', '-c', installer_code, old['directory'], str(data), str(ROOT)], 'predecessor-installer')
        wait(lambda: installer.poll() is not None, timeout=60)
        check('The predecessor installs its own database, identities and original participant fixture', installer.returncode == 0)
        fixture = read_document(data / 'qualification-fixture.json')
        dsn = private_read(data / 'control.dsn').decode().strip()
        api_identity, engine_identity = issue(data, 'retry-api'), issue(data, 'retry-engine')
        port, gateway_port = free_port(), free_port()
        config = read_document(data / 'deployment.json')
        config.update(http={'tls_identity': api_identity['reference'], 'require_client_certificate': True}, transport={'accounting': {'origin': 'https://127.0.0.1:' + str(gateway_port), 'identity_reference': engine_identity['reference']}})
        write_document(data / 'deployment.json', config)
        gateway_config, fault_path = folder / 'gateway.json', folder / 'fault.json'
        write_document(gateway_config, {'format': 'avenra-service-gateway/1', 'service': 'accounting', 'upstream': 'http://127.0.0.1:4830', 'port': gateway_port, 'identity_reference': engine_identity['reference']})
        write_document(fault_path, {})
        start([sys.executable, '-B', str(Path(__file__).resolve()), '--gateway', str(data), str(gateway_config), str(fault_path)], 'evidence-read-gateway')
        probe = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=client_context(data, engine_identity['reference'])))
        wait(lambda: probe.open('https://127.0.0.1:' + str(gateway_port) + '/_/info', timeout=2).close() or True)
        releases.transition(data, old['directory'], baseline['trust_file'], baseline['runtime_directory'], None, str(uuid.uuid4()), 'worker-retry-verifier')
        old_command = releases.command(data, baseline['trust_file'], 'worker')
        api = start(releases.command(data, baseline['trust_file'], 'api', port), 'predecessor-api')
        worker = start(old_command, 'predecessor-worker')
        token, context = read_document(data / 'credentials.json')['api_key'], client_context(data, api_identity['reference'])
        scope = '/api/tenants/tenant_avenra/environments/' + fixture['environment_id']
        def call(path, body=None):
            opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))
            request = Request('https://127.0.0.1:' + str(port) + path, data=canonical(body).encode() if body is not None else None, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
            try: response = opener.open(request, timeout=10)
            except HTTPError as error: response = error
            with response: return response.status, json.load(response)
        wait(lambda: call('/health/ready'), lambda result: result[0] == 200)
        def payment(identity): return call(scope + '/banking/upi/payments/' + identity)[1]
        def jobs(identity):
            with psycopg.connect(dsn, autocommit=True, row_factory=psycopg.rows.dict_row) as db:
                return db.execute("SELECT * FROM outbox WHERE kind='upi_payment' AND aggregate_id=%s ORDER BY created_at,id", (identity,)).fetchall()
        def submit(mode):
            nonlocal worker
            # Admit and authorize before leasing so this controlled case has
            # one wakeup job. This removes a second-worker/wakeup race from the
            # specific error-budget assertion without changing payment IDs.
            stop(worker)
            body = {'payer_id': fixture['accounts'][0], 'payee_id': fixture['accounts'][1], 'device_id': fixture['devices'][0], 'amount_paise': 37, 'request_key': uid('original_retry_payment'), 'expires_in_seconds': 900}
            status, value = call(scope + '/banking/upi/payments', body)
            check('An original payment is durably admitted for ' + mode, status == 202)
            write_document(fault_path, {'payment_id': value['id'], 'mode': mode, 'events': []})
            status, _ = call(scope + '/banking/upi/payments/' + value['id'] + '/authorize', {'device_id': fixture['devices'][0], 'device_proof': fixture['proofs'][0], 'approved': True})
            check('The original device authorizes the payment for ' + mode, status in {200, 202})
            worker = start(worker_command, 'payment-worker-' + value['id'])
            return value['id'], body
        native = AccountingGateway('http://127.0.0.1:4830')
        with psycopg.connect(dsn, autocommit=True) as db:
            ledger = db.execute('SELECT ledger_name FROM environments WHERE id=%s', (fixture['environment_id'],)).fetchone()[0]
        def effects(identity):
            value = payment(identity)
            return {leg['id']: [{'id': str(tx['id']), 'postings': tx['postings']} for tx in native.transactions(ledger, leg['id'])] for leg in value['operation']['legs']}
        worker_command = old_command
        original, original_body = submit('once')
        failed = wait(lambda: jobs(original), lambda rows: rows and any(row['state'] == 'failed' for row in rows), timeout=35)
        failed_job = next(row for row in failed if row['state'] == 'failed')
        original_state, original_effects = payment(original), effects(original)
        check('The actual predecessor reproduces one sealing error exhausting productive claims', original_state['state'] == 'succeeded' and original_state['evidence'] is None and len(read_document(fault_path)['events']) == 1 and failed_job['attempts'] >= 7 and failed_job['last_error'] == 'EngineError')
        check('The failed predecessor has exactly one original native debit and credit', len(original_effects) == 2 and all(len(value) == 1 for value in original_effects.values()))
        arguments = {'directory': str(data), 'artifact_directory': new['directory'], 'trusted_public_file': str(trust), 'expected': old['artifact_id'], 'request_id': str(uuid.uuid4()), 'actor': 'worker-retry-verifier'}
        denied('Live predecessor nodes fence the offline retry migration', lambda: upgrade_work_queue(**arguments), 'RELEASE_NODES_ACTIVE')
        stop(worker);stop(api)
        storage.checkpoint('offline_control_migration')
        with psycopg.connect(dsn, autocommit=True) as db:
            columns = [row[0] for row in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='outbox' AND table_schema='public' ORDER BY ordinal_position")]
        def original_rows():
            with psycopg.connect(dsn, autocommit=True) as db:
                rows = db.execute(sql.SQL('SELECT {} FROM outbox ORDER BY id').format(sql.SQL(',').join(map(sql.Identifier, columns)))).fetchall()
                pointer = db.execute('SELECT artifact_id,application_fingerprint,generation FROM release_active').fetchone()
                payment_rows = db.execute('SELECT * FROM upi_payments ORDER BY id').fetchall()
            return digest(rows), pointer, digest(payment_rows)
        before = original_rows()
        from avenra.postgres_store import PostgresStore
        constructor_refused = False
        # An explicit migration caller has its own private configuration. The
        # original deployment keeps its captured runtime/release policy. The
        # managed database itself must refuse this constructor bypass attempt.
        operator_data = folder / 'migration-constructor'
        operator_data.mkdir(mode=0o700)
        write_document(operator_data / 'deployment.json', {'format': 'avenra-deployment/1', 'database': {'mode': 'migration'}})
        private_write(operator_data / 'evidence.key', private_read(data / 'evidence.key'))
        try:
            unexpected = PostgresStore(operator_data, private_read(data / 'migration.dsn').decode().strip())
        except RuntimeError as error:
            constructor_refused = 'reviewed offline worker retry migration' in str(error)
        else:
            unexpected.close()
        check('A migrator constructor cannot bypass the attributed managed migration', constructor_refused and original_rows() == before)
        denied('Candidate activation refuses the missing control-v10 migration', lambda: releases.transition(data, new['directory'], trust, runtime, old['artifact_id'], str(uuid.uuid4()), 'worker-retry-verifier'), 'RELEASE_SCHEMA')
        denied('A predecessor cannot supply an unreviewed retry migration', lambda: upgrade_work_queue(**{**arguments, 'artifact_directory': old['directory'], 'trusted_public_file': baseline['trust_file']}), 'MIGRATION_ARTIFACT')
        instructions = folder / 'migration-instructions.json';write_document(instructions, arguments)
        crashed = start([sys.executable, '-B', str(Path(__file__).resolve()), '--crash', str(instructions)], 'operator-killed-in-ddl')
        wait(lambda: crashed.poll() is not None)
        with psycopg.connect(dsn, autocommit=True) as db:
            rolled_back = db.execute("SELECT to_regclass('outbox_retry_schema')").fetchone()[0] is None and db.execute('SELECT version FROM avenra_schema_version').fetchone()[0] == 9
        check('Actual operator death rolls back DDL, migration history and original rows', crashed.returncode == 76 and rolled_back and original_rows() == before)
        migrated = upgrade_work_queue(**arguments)
        check('Migration preserves original claims, payloads, payment rows and release selection', migrated['installed'] and not migrated['already_installed'] and original_rows() == before)
        check('The exact migration request replays without another migration', upgrade_work_queue(**arguments)['replayed'])
        denied('Changed attribution cannot reuse the migration request', lambda: upgrade_work_queue(**{**arguments, 'actor': 'changed-actor'}), 'IDEMPOTENCY_CONFLICT')
        with psycopg.connect(dsn, autocommit=True) as db:
            inherited = db.execute('SELECT error_attempts,state,attempts,last_error FROM outbox WHERE id=%s', (failed_job['id'],)).fetchone()
            check('Legacy errors retain terminal status without invented error counts', inherited == (None, 'failed', failed_job['attempts'], 'EngineError'))
            for statement in ['UPDATE outbox_retry_schema SET version=2', 'DELETE FROM outbox_retry_schema', 'ALTER TABLE outbox ADD COLUMN forbidden int']:
                refused = False
                try: db.execute(statement)
                except psycopg.errors.InsufficientPrivilege: refused = True
                check('Runtime refuses migration history mutation or DDL: ' + statement.split()[0], refused)
        stale = start(old_command, 'old-worker-after-migration');wait(lambda: stale.poll() is not None, timeout=25)
        check('An actual old worker refuses the upgraded schema before dispatch', stale.returncode != 0 and original_rows() == before)
        releases.transition(data, new['directory'], trust, runtime, old['artifact_id'], str(uuid.uuid4()), 'worker-retry-verifier')
        denied('Rollback cannot bypass the new retry semantics', lambda: releases.transition(data, old['directory'], baseline['trust_file'], baseline['runtime_directory'], new['artifact_id'], str(uuid.uuid4()), 'worker-retry-verifier', 'rollback'), 'RELEASE_SCHEMA')
        api = start(releases.command(data, trust, 'api', port), 'candidate-api')
        worker_command = releases.command(data, trust, 'worker')
        worker = start(worker_command, 'candidate-worker')
        wait(lambda: call('/health/ready'), lambda result: result[0] == 200)
        replay_status, replayed = call(scope + '/banking/upi/payments', original_body)
        status, reconciled = call(scope + '/banking/upi/payments/' + original + '/reconcile', {})
        completed = wait(lambda: payment(original), lambda value: bool(value['evidence']))
        check('Original-ID reconciliation seals the predecessor payment with its original operation', replay_status == 202 and replayed['id'] == original and status == 202 and reconciled['id'] == original and completed['operation_id'] == original_state['operation_id'] and completed['evidence']['valid'])
        check('Original-ID recovery retains both native transactions exactly', effects(original) == original_effects)
        check('The exhausted predecessor job remains retained history', any(row['id'] == failed_job['id'] and row['state'] == 'failed' and row['error_attempts'] is None for row in jobs(original)))
        automatic, _ = submit('once')
        completed = wait(lambda: payment(automatic), lambda value: bool(value['evidence']), timeout=30)
        automatic_jobs = wait(lambda: jobs(automatic), lambda rows: any(row['state'] == 'done' and row['error_attempts'] == 1 for row in rows))
        auto_effects = effects(automatic)
        check('One post-settlement read error automatically retries after productive claims', completed['evidence']['valid'] and len(read_document(fault_path)['events']) == 1 and any(row['state'] == 'done' and row['attempts'] >= 8 and row['error_attempts'] == 1 for row in automatic_jobs))
        check('Automatic retry retains exactly one transaction per original financial leg', len(auto_effects) == 2 and all(len(value) == 1 for value in auto_effects.values()))
        exhausted_id, _ = submit('always')
        wait(lambda: jobs(exhausted_id), lambda rows: any(row['error_attempts'] is not None and row['error_attempts'] >= 2 and row['state'] == 'ready' for row in rows), timeout=30)
        stop(worker);counts = jobs(exhausted_id)
        worker = start(releases.command(data, trust, 'worker'), 'candidate-worker-restarted')
        exhausted_jobs = wait(lambda: jobs(exhausted_id), lambda rows: any(row['state'] == 'failed' and row['error_attempts'] == 6 for row in rows), timeout=60)
        exhausted_payment, exhausted_effects = payment(exhausted_id), effects(exhausted_id)
        check('A real worker restart retains the budget and stops at six actual errors', any(row['error_attempts'] >= 2 for row in counts) and len(read_document(fault_path)['events']) == 6 and exhausted_payment['state'] == 'succeeded' and exhausted_payment['evidence'] is None and any(row['attempts'] >= 12 for row in exhausted_jobs))
        write_document(fault_path, {})
        status, resumed = call(scope + '/banking/upi/payments/' + exhausted_id + '/reconcile', {})
        completed = wait(lambda: payment(exhausted_id), lambda value: bool(value['evidence']))
        resumed_jobs = wait(lambda: jobs(exhausted_id), lambda rows: any(row['state'] == 'done' and row['error_attempts'] == 0 for row in rows))
        check('Exhausted work resumes through explicit original-ID reconciliation', status == 202 and resumed['id'] == exhausted_id and completed['evidence']['valid'] and any(row['state'] == 'failed' and row['error_attempts'] == 6 for row in resumed_jobs))
        check('Exhaustion and resumption preserve unique original native effects', effects(exhausted_id) == exhausted_effects and all(len(value) == 1 for value in exhausted_effects.values()))
        stop(worker)
        from avenra.app import App
        app = App(data, control_dsn=dsn)
        try:
            with app.store.transaction() as db: app.store.enqueue(db, 'tenant_avenra', fixture['environment_id'], 'qualification', 'original-lease-test', {}, uid('lease_test'))
            for _ in range(15):
                job = app.store.claim('progress')
                if job['kind'] != 'qualification': raise AssertionError('Unexpected pending domain work in lease qualification')
                app.store.defer(job, 0)
            stale_job = app.store.claim('expired', lease_seconds=-1)
            current = app.store.claim('current')
            check('PostgreSQL refuses stale completion, deferral and error accounting', not app.store.finish(stale_job) and not app.store.defer(stale_job, 0) and not app.store.finish(stale_job, 'StaleError'))
            check('PostgreSQL deferrals and reclaimed claims preserve the error budget', current['attempts'] == 17 and current['error_attempts'] == 0 and app.store.finish(current, 'EngineError'))
            with app.store.reader() as db: row = db.execute('SELECT state,attempts,error_attempts FROM outbox WHERE id=?', (current['id'],)).fetchone()
            check('PostgreSQL records one error after all productive claims', (row[0], row[1], row[2]) == ('ready', 17, 1))
            final_job = wait(lambda: app.store.claim('qualification-completion'))
            check('The original nonfinancial lease fixture drains after its natural error backoff', final_job['id'] == current['id'] and final_job['error_attempts'] == 1 and app.store.finish(final_job))
        finally: app.close()
        check('Qualification source and case pack stayed fixed', report['application_fingerprint'] == application_fingerprint() and report['case_pack_sha256'] == case_pack_hash())
        report.update(passed=True, predecessor_artifact=old['artifact_id'], candidate_artifact=new['artifact_id'], payments={'predecessor': original, 'automatic': automatic, 'exhausted': exhausted_id}, evidence={'predecessor_original_effects': original_effects, 'automatic_effects': auto_effects, 'exhausted_original_effects': exhausted_effects})
    except Exception as error: report.update(error=str(error), traceback=traceback.format_exc())
    finally:
        for child in reversed(children): stop(child)
        report['completed_at'], report['storage_admission'] = time.time(), storage.snapshot()
        full_path = folder / 'verification.json'
        write_document(full_path, report)
        summary = {key: value for key, value in report.items() if key != 'evidence'}
        summary['details'] = {'path': str(full_path), 'sha256': hashlib.sha256(full_path.read_bytes()).hexdigest()}
        write_document(ROOT / '.local/work-queue-verification.json', summary)
        print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'report': str(folder / 'verification.json'), 'error': report.get('error')}), flush=True)
        if not report['passed']: print(report.get('traceback', ''), flush=True)
    return 0 if report['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway', nargs=3, metavar=('DATA', 'CONFIG', 'FAULT'))
    parser.add_argument('--crash')
    args = parser.parse_args()
    if args.gateway: gateway_child(*args.gateway);return 0
    if args.crash: return crash_child(args.crash)
    try:
        with QualificationStorage(ROOT, 'release_upgrade') as storage: return run(storage)
    except StorageRefused as error: return record_refusal(ROOT, 'work-queue-verification.json', error, application_fingerprint(), case_pack_hash())


if __name__ == '__main__': raise SystemExit(main())
