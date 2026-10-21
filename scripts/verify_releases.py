#!/usr/bin/env python3
"""Qualify signed application upgrades/rollback with real API, workers and money.

Only trusted synthetic code is executed. A and B are copies of this application;
B adds a harmless version marker so a stale-source refusal is actually tested.
Failed attempts and isolated deployment data remain private and retained.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import traceback
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.app import App
from avenra import release_bundle as bundle
from avenra import release_runtime as releases
from avenra.database_deployment import provision
from avenra.deployment_backup import snapshot as snapshot_settings, restore as restore_settings
from avenra.errors import DomainError
from avenra.gateways import AccountingGateway
from avenra.lab_identity import private_read, private_write
from avenra.local_database import admin_dsn
from avenra.private_config import deployment, write_document
from avenra.synthetic_lab import fixture
from avenra.util import application_fingerprint, canonical, uid
from scripts.qualification_storage import QualificationStorage, StorageRefused, record_refusal, harness_case_pack_hash as guarded_case_pack_hash


def case_pack_hash():
    return guarded_case_pack_hash(ROOT, 'verify_releases.py')


def crash_child(instructions):
    value = json.loads(private_read(instructions));stage = value.pop('stage')
    def crash(observed):
        if observed == stage: os._exit(73 if stage == 'prepared' else 74)
    releases.transition(**value, checkpoint=crash)
    return 9


def run(storage):
    import psycopg
    directory = ROOT / '.local/qa' / uid('releases');directory.mkdir(parents=True, mode=0o700)
    report = {'passed': False, 'checks': [], 'directory': str(directory), 'created_at': time.time(), 'case_pack_sha256': case_pack_hash(),
              'application_fingerprint': application_fingerprint(), 'production_ready': False, 'official_conformance': False,
              'scope': 'Local exact-platform signed application bundle, offline wheel installation, database-fenced schema-preserving upgrade/rollback, real operator crashes and native payment continuity. No native service/database upgrade, distributed failover, Linux/KVM or independent certification.'}
    processes = [];app = None

    def check(name, condition):
        report['checks'].append({'name': name, 'passed': bool(condition)})
        print(json.dumps({'check': name, 'passed': bool(condition)}), flush=True)
        if not condition: raise AssertionError(name)

    def denied(name, callback, code):
        try: callback()
        except DomainError as error: return check(name, error.code == code)
        check(name, False)

    def start(command, name, cwd=ROOT):
        log = directory / (name + '.log')
        with log.open('ab') as output:
            log.chmod(0o600);p = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        processes.append(p);return p

    def stop(child):
        if child.poll() is None:
            child.terminate()
            try: child.wait(timeout=20)
            except subprocess.TimeoutExpired: child.kill();child.wait(timeout=5)

    def wait(callback, predicate=lambda x: bool(x), timeout=25):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                value = callback()
                if predicate(value): return value
            except (OSError, URLError): pass
            time.sleep(.05)
        raise TimeoutError('Release qualification did not reach its required observed state; inspect private process logs.')

    def request_id(): return str(uuid.uuid4())

    try:
        wheels = ROOT / '.local/release-inputs/wheels'
        key, trust, wrong, wrong_trust = [directory / x for x in ['release-key.raw', 'release-public.raw', 'other-key.raw', 'other-public.raw']]
        bundle.new_key(key, trust);bundle.new_key(wrong, wrong_trust)
        a = bundle.build(ROOT, wheels, key, directory / 'a.tar.gz', 'qualification-a')
        a_again = bundle.build(ROOT, wheels, key, directory / 'a-again.tar.gz', 'qualification-a')
        check('Identical pinned inputs produce byte-identical signed compressed archives', a['archive_sha256'] == a_again['archive_sha256'] and a['artifact_id'] == a_again['artifact_id'])
        inspected = bundle.inspect(directory / 'a.tar.gz', trust)
        inventory = inspected['manifest']['files']
        check('The signed release includes the API and operator guides needed outside the original checkout',
              {'API_REFERENCE.md','OPERATIONS.md','docs/COORDINATED_RECOVERY.md'} <= {item['path'] for item in inventory})
        check('Signed application inventory contains licence notices and all direct and transitive wheel hashes',
              any(x['path'].startswith('LICENSES/') for x in inventory) and len(inspected['manifest']['dependencies']) >= 18 and all(len(x['sha256']) == 64 for x in inspected['manifest']['dependencies']))
        check('Private keys, database identities and installation state are excluded from the application payload',
              not any(x['path'].startswith('.local/') or x['path'].endswith(('.dsn', '.pem', '.raw', '.db', '.sqlite', '.env')) for x in inventory))
        denied('A different externally trusted signing key refuses the archive', lambda: bundle.inspect(directory / 'a.tar.gz', wrong_trust), 'RELEASE_SIGNATURE')

        def variant(label, operation):
            target = directory / (label + '.tar.gz')
            with tarfile.open(directory / 'a.tar.gz', 'r:gz') as source, tarfile.open(target, 'w:gz') as output:
                for member in source:
                    data = source.extractfile(member).read()
                    if member.name == 'avenra/static/index.html':
                        if operation == 'missing': continue
                        if operation == 'changed': data += b'changed'
                    copy = tarfile.TarInfo(member.name);copy.size = len(data);copy.mode = 0o444
                    output.addfile(copy, io.BytesIO(data))
                if operation in {'extra', 'duplicate', 'escape', 'link'}:
                    entry = tarfile.TarInfo({'extra': 'extra.py', 'duplicate': 'requirements.txt', 'escape': '../escape', 'link': 'unexpected-link'}[operation])
                    if operation == 'link': entry.type = tarfile.SYMTYPE;entry.linkname = '/tmp/outside'
                    output.addfile(entry, io.BytesIO(b''))
            return target

        for label, error in [('changed', 'RELEASE_INTEGRITY'), ('missing', 'RELEASE_ARCHIVE'), ('extra', 'RELEASE_ARCHIVE'), ('duplicate', 'RELEASE_ARCHIVE'), ('escape', 'RELEASE_PATH'), ('link', 'RELEASE_ARCHIVE')]:
            altered = variant(label, label)
            denied('Archive refuses ' + label + ' payload without materializing an installation', lambda altered=altered: bundle.install(altered, trust, directory / 'rejected'), error)
        check('Rejected archives create no installation store and no escaped file', not (directory / 'rejected').exists() and not (directory / 'escape').exists())
        installed_a = bundle.install(directory / 'a.tar.gz', trust, directory / 'artifacts')
        artifact_a = Path(installed_a['directory'])
        check('Verified release payload installs read-only and revalidates against the external trust root',
              bundle.verify_installed(artifact_a, trust)['artifact_id'] == a['artifact_id'] and all(not p.stat().st_mode & 0o222 for p in artifact_a.rglob('*') if p.is_file()))
        source = directory / 'source-b';source.mkdir(mode=0o700)
        for relative, original in bundle.source_inventory(ROOT).items():
            target = source / relative;target.parent.mkdir(parents=True, exist_ok=True);shutil.copyfile(original, target)
        (source / 'avenra/release_qualification_marker.py').write_text('RELEASE_MARKER = "qualification-b"\n')
        (source / '.local/bin').mkdir(parents=True)
        for role in ['accounting', 'fault']: os.link(ROOT / '.local/bin' / role, source / '.local/bin' / role)
        b = bundle.build(source, wheels, key, directory / 'b.tar.gz', 'qualification-b')
        installed_b = bundle.install(directory / 'b.tar.gz', trust, directory / 'artifacts');artifact_b = Path(installed_b['directory'])
        binding_policy=directory/'binding-policy';binding_policy.mkdir(mode=0o700)
        write_document(binding_policy/'engine-endpoints.json',{'format':'avenra-loopback-engines/1','accounting':'http://127.0.0.1:4830','fault':'http://127.0.0.1:4831'})
        check('A signed current artifact declares support for an explicit engine binding before any database connection',releases.require_deployment_capabilities(binding_policy,installed_a,{})=={'engine_bindings':'avenra-loopback-engines/1'})
        capability_source=source/'avenra/deployment_capabilities.py';saved_capabilities=capability_source.read_bytes()
        canary=directory/'candidate-capability-code-executed'
        variants=[('unsupported-capabilities',b"CAPABILITIES = {'format':'avenra-deployment-capabilities/1','engine_bindings':[],'bank_routes':[]}\n"),
                  ('executable-capabilities',('CAPABILITIES = __import__(\'pathlib\').Path('+repr(str(canary))+').write_text(\'executed\')\n').encode())]
        for variant_name,payload in variants:
            capability_source.write_bytes(payload)
            archive=directory/(variant_name+'.tar.gz');bundle.build(source,wheels,key,archive,variant_name)
            candidate=bundle.install(archive,trust,directory/'artifacts')
            denied('Verified signed candidate refuses '+variant_name+' before selection',lambda candidate=candidate:releases.require_deployment_capabilities(binding_policy,candidate,{}),'RELEASE_CAPABILITIES')
        capability_source.write_bytes(saved_capabilities)
        check('Candidate capability inspection never executes its Python expression',not canary.exists())
        check('The next release is a distinct signed source fingerprint with unchanged financial engine files',
              a['application_fingerprint'] != b['application_fingerprint'] and all(bundle.sha(artifact_a / ('avenra/' + f)) == bundle.sha(artifact_b / ('avenra/' + f)) for f in ['upi.py', 'banking.py', 'simulation.py', 'gateways.py']))
        damaged = artifact_b / 'avenra/release_qualification_marker.py';original = damaged.read_bytes();damaged.chmod(0o644);damaged.write_bytes(b'changed\n');damaged.chmod(0o444)
        denied('Changed installed source refuses verification before runtime launch', lambda: bundle.verify_installed(artifact_b, trust), 'RELEASE_INTEGRITY')
        damaged.chmod(0o644);damaged.write_bytes(original);damaged.chmod(0o444)
        runtime_a, runtime_b = directory / 'runtime-a', directory / 'runtime-b'
        storage.checkpoint('offline_application_runtimes')
        for artifact, runtime in [(artifact_a, runtime_a), (artifact_b, runtime_b)]: bundle.prepare_runtime(artifact, trust, runtime)
        check('Both fresh application runtimes install entirely from pinned local wheels and pass dependency consistency',
              all((p / 'release-runtime.json').exists() and 'Successfully installed' in (p / 'installation.log').read_text() and 'No broken requirements' in (p / 'installation.log').read_text() for p in [runtime_a, runtime_b]))
        runtime_file = next((runtime_b / 'lib').rglob('uvicorn/__init__.py'));saved = runtime_file.read_bytes();runtime_file.write_bytes(saved + b'\n# changed\n')
        denied('Changed installed dependency refuses selection before application launch', lambda: releases.runtime_receipt(installed_b, runtime_b), 'RELEASE_RUNTIME')
        runtime_file.write_bytes(saved)

        data = directory / 'deployment';receipt = provision(data, admin_dsn(ROOT));report['database'] = receipt['database']
        dsn = private_read(data / 'control.dsn').decode().strip();migrator = private_read(data / 'migration.dsn').decode().strip()
        app = App(data, control_dsn=dsn);value = fixture(app);tenant, env = value['tenant_id'], value['environment_id']
        baseline = {x['id']: x['balance_paise'] for x in app.accounts(tenant, env)}
        first_id = request_id()
        denied('Initial managed activation requires even an unpinned runtime pool to drain', lambda: releases.transition(data, artifact_a, trust, runtime_a, None, first_id, 'release-verifier'), 'RELEASE_NODES_ACTIVE')
        app.close();app = None
        first = releases.transition(data, artifact_a, trust, runtime_a, None, first_id, 'release-verifier')
        check('First activation commits the schema-compatible artifact and publishes matching selection',
              first['generation'] == 1 and not first['schema_change'] and releases.status(data)['active']['artifact_id'] == a['artifact_id'] and not releases.status(data)['pending_recovery'])
        check('An identical activation retry returns its original generation', releases.transition(data, artifact_a, trust, runtime_a, None, first_id, 'release-verifier')['replayed'])
        denied('An altered activation payload cannot reuse its transition identity', lambda: releases.transition(data, artifact_a, trust, runtime_a, None, first_id, 'different-actor'), 'RELEASE_CONFLICT')
        with socket.socket() as listener: listener.bind(('127.0.0.1', 0));port = listener.getsockname()[1]
        origin = 'http://127.0.0.1:' + str(port);opener = build_opener(ProxyHandler({}));credential = json.loads(private_read(data / 'credentials.json'))
        scope = '/api/tenants/' + tenant + '/environments/' + env
        def api(path, body=None):
            request = Request(origin + path, data=canonical(body).encode() if body is not None else None,
                              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + credential['api_key']})
            with opener.open(request, timeout=10) as response: return json.load(response)
        evil = directory / 'untrusted-cwd';(evil / 'avenra').mkdir(parents=True)
        (evil / 'avenra/__init__.py').write_text('from pathlib import Path;Path("caller-import-executed").write_text("bad")\n')
        api_a = start(releases.command(data, trust, 'api', port), 'api-a', evil)
        wait(lambda: api('/health/live'))
        worker_a = start(releases.command(data, trust, 'worker'), 'worker-a')
        wait(lambda: api('/health/ready'), lambda x: x['ready'])
        check('Verified packaged API and separate worker start with isolated imports and shared release selection',
              api_a.poll() is None and worker_a.poll() is None and not (evil / 'caller-import-executed').exists())
        html = opener.open(origin + '/', timeout=10).read()
        check('The packaged application serves the existing Avenra console unchanged', hashlib.sha256(html).hexdigest() == bundle.sha(ROOT / 'avenra/static/index.html'))
        def payment(amount):
            body = {'payer_id': value['accounts'][0], 'payee_id': value['accounts'][1], 'device_id': value['devices'][0], 'amount_paise': amount, 'request_key': uid('release_payment')}
            accepted = api(scope + '/banking/upi/payments', body)
            api(scope + '/banking/upi/payments/' + accepted['id'] + '/authorize', {'device_id': value['devices'][0], 'device_proof': value['proofs'][0], 'approved': True})
            return accepted
        p1 = payment(4500);finished = wait(lambda: api(scope + '/banking/upi/payments/' + p1['id']), lambda x: x['state'] == 'succeeded' and x['evidence'])
        check('Release A completes an actual participant payment backed by native double-entry accounting', finished['evidence']['valid'])
        denied('An active API and worker prevent an upgrade instead of allowing mixed release nodes', lambda: releases.transition(data, artifact_b, trust, runtime_b, a['artifact_id'], request_id(), 'release-verifier'), 'RELEASE_NODES_ACTIVE')
        stop(worker_a)
        p2 = payment(6250)
        with psycopg.connect(dsn, autocommit=True) as db:
            retained = db.execute('SELECT id,operation_id FROM upi_payments WHERE id=%s', (p2['id'],)).fetchone()
            audit_before = db.execute('SELECT count(*) FROM audit_events').fetchone()[0]
        stop(api_a)
        denied('A stale expected release cannot activate another build', lambda: releases.transition(data, artifact_b, trust, runtime_b, '0' * 64, request_id(), 'release-verifier'), 'RELEASE_STALE')
        with psycopg.connect(migrator, autocommit=True) as db: db.execute('UPDATE fidelity_schema SET version=99')
        denied('Incompatible schemas refuse activation without implicit downgrade or financial rewind', lambda: releases.transition(data, artifact_b, trust, runtime_b, a['artifact_id'], request_id(), 'release-verifier'), 'RELEASE_SCHEMA')
        with psycopg.connect(migrator, autocommit=True) as db: db.execute('UPDATE fidelity_schema SET version=2')
        upgraded = releases.transition(data, artifact_b, trust, runtime_b, a['artifact_id'], request_id(), 'release-verifier')
        check('A drained compatible code upgrade commits exactly the next generation', upgraded['generation'] == 2 and upgraded['artifact_id'] == b['artifact_id'])
        stale_code = 'import sys;sys.path.insert(0,sys.argv.pop(1));from avenra.asgi import main;main()'
        stale = start([str(runtime_a / 'bin/python'), '-I', '-B', '-c', stale_code, str(artifact_a), '--data', str(data), '--port', str(port)], 'stale-a')
        wait(lambda: stale.poll() is not None, timeout=20)
        check('An old packaged API cannot restart against the newly selected database release', stale.returncode != 0 and 'does not match the selected application source' in (directory / 'stale-a.log').read_text())
        api_b = start(releases.command(data, trust, 'api', port), 'api-b');wait(lambda: api('/health/live'))
        worker_b = start(releases.command(data, trust, 'worker'), 'worker-b')
        done2 = wait(lambda: api(scope + '/banking/upi/payments/' + p2['id']), lambda x: x['state'] == 'succeeded' and x['evidence'])
        with psycopg.connect(dsn, autocommit=True) as db:
            same = db.execute('SELECT id,operation_id FROM upi_payments WHERE id=%s', (p2['id'],)).fetchone() == retained
            counts_ok = db.execute('SELECT count(*) FROM audit_events').fetchone()[0] >= audit_before
        check('Release B resumes original accepted participant identities and preserves prior audit history', same and done2['evidence']['valid'] and counts_ok)
        account_values = api(scope + '/accounts')['accounts'];observed = {x['id']: x['balance_paise'] for x in account_values}
        check('Native participant balances after upgrade reflect exactly both declared transfers',
              observed[value['accounts'][0]] == baseline[value['accounts'][0]] - 10750 and observed[value['accounts'][1]] == baseline[value['accounts'][1]] + 10750)
        # A real committed-but-unacknowledged legacy payment is carried across
        # rollback. It uses the same native authority as the participant engine.
        legacy_env = api('/api/tenants/' + tenant + '/environments', {'name': 'Rollback uncertainty', 'fixture_profile': 'legacy'})['id']
        legacy_scope = '/api/tenants/' + tenant + '/environments/' + legacy_env
        ov = wait(lambda: api(legacy_scope + '/overview'), lambda x: x['environment']['status'] == 'active')
        ac = api(legacy_scope + '/accounts')['accounts'];payer = next(x for x in ac if x['role'] == 'payer');payee = next(x for x in ac if x['role'] == 'payee')
        uncertain = api(legacy_scope + '/payments', {'payer_id': payer['id'], 'payee_id': payee['id'], 'amount_paise': 1000, 'request_key': uid('uncertain'), 'fault': 'lost_response'})
        wait(lambda: api(legacy_scope + '/payments/' + uncertain['id']), lambda x: x['state'] == 'unknown')
        gateway = AccountingGateway('http://127.0.0.1:4830');original_effect = gateway.transactions(ov['environment']['ledger_name'], 'payment:' + uncertain['id'])
        check('Before rollback, a physical lost response leaves an uncertain payment with one real committed effect', len(original_effect) == 1)
        stop(worker_b);stop(api_b)
        rolled = releases.transition(data, artifact_a, trust, runtime_a, b['artifact_id'], request_id(), 'release-verifier', 'rollback')
        check('Rollback reselects a previously committed schema-compatible code artifact without rewinding financial history', rolled['generation'] == 3 and not rolled['financial_history_rewound'])
        api_a2 = start(releases.command(data, trust, 'api', port), 'api-a-rollback');wait(lambda: api('/health/live'))
        worker_a2 = start(releases.command(data, trust, 'worker'), 'worker-a-rollback')
        api(legacy_scope + '/payments/' + uncertain['id'] + '/reconcile', {})
        done = wait(lambda: api(legacy_scope + '/payments/' + uncertain['id']), lambda x: x['state'] == 'succeeded')
        effects = gateway.transactions(ov['environment']['ledger_name'], 'payment:' + uncertain['id'])
        check('Rollback recovery confirms the original uncertain effect once using its original native identity', len(effects) == 1 and str(effects[0]['id']) == str(original_effect[0]['id']) and str(done['ledger_transaction_id']) == str(effects[0]['id']))
        check('Earlier participant payments and their financial evidence remain valid after rollback',
              api(scope + '/banking/upi/payments/' + p1['id'])['evidence']['valid'] and api(scope + '/banking/upi/payments/' + p2['id'])['evidence']['valid'])
        stop(worker_a2);stop(api_a2)
        with psycopg.connect(dsn, autocommit=True) as db:
            for statement in ["UPDATE release_active SET generation=99", "INSERT INTO release_events(request_id,payload_hash,operation,artifact_id,application_fingerprint,actor,generation,schemas_json) VALUES('bad','bad','activate','bad','bad','bad',99,'{}')", 'DELETE FROM release_events']:
                refused = False
                try: db.execute(statement)
                except psycopg.errors.InsufficientPrivilege: refused = True
                check('Runtime role refuses release-catalog mutation: ' + statement.split()[0], refused)
        with psycopg.connect(migrator, autocommit=True) as db:
            refused = False
            try: db.execute("UPDATE release_events SET actor='changed'")
            except psycopg.errors.RaiseException: refused = True
            check('Committed release history remains immutable even for ordinary migrator UPDATE', refused)

        def crashed(stage):
            instructions = {'directory': str(data), 'artifact_directory': str(artifact_b), 'trusted_public_file': str(trust), 'runtime_directory': str(runtime_b),
                            'expected': a['artifact_id'], 'request_id': request_id(), 'actor': 'crash-verifier', 'stage': stage}
            path = directory / ('crash-' + stage + '.json');private_write(path, canonical(instructions))
            child = start([sys.executable, '-B', str(Path(__file__).resolve()), '--transition-crash', str(path)], 'operator-' + stage)
            wait(lambda: child.poll() is not None)
            return child
        before = releases.status(data)['active']
        prepared = crashed('prepared')
        check('Actual operator death after durable preparation leaves a recoverable uncommitted transition', prepared.returncode == 73 and releases.status(data)['pending_recovery'] and releases.status(data)['active'] == before)
        denied('Pending transition prevents startup through the managed launcher', lambda: releases.command(data, trust, 'api', port), 'RELEASE_RECOVERY_REQUIRED')
        aborted = releases.recover(data, trust)
        check('Precommit crash recovery preserves the prior release and retains the aborted attempt', not aborted['committed'] and releases.status(data)['active'] == before and list(data.glob('release-aborted-*.json')))
        committed = crashed('committed')
        check('Actual operator death after database commit leaves the old filesystem selection visibly pending',
              committed.returncode == 74 and releases.status(data)['active']['artifact_id'] == b['artifact_id'] and releases.status(data)['selection']['artifact_id'] == a['artifact_id'])
        settings_snapshot = directory / 'interrupted-release-snapshot'
        captured = snapshot_settings(data, settings_snapshot)
        inactive_settings = directory / 'inactive-release-settings'
        inactive = restore_settings(settings_snapshot, inactive_settings)
        check('Inactive private deployment recovery preserves original release selection and pending transition',
              captured['files'] == inactive['files'] and inactive['valid'] and (inactive_settings / 'release-selection.json').read_bytes() == (data / 'release-selection.json').read_bytes() and (inactive_settings / 'release-pending.json').read_bytes() == (data / 'release-pending.json').read_bytes())
        denied('Restored interrupted release settings cannot contact source credentials or autoactivate an endpoint', lambda: releases.command(inactive_settings, trust, 'api', port), 'RECOVERY_INACTIVE')
        recovered = releases.recover(data, trust)
        check('Postcommit crash recovery publishes the committed artifact without rerunning a financial operation',
              recovered['committed'] and releases.status(data)['selection']['artifact_id'] == b['artifact_id'] and not releases.status(data)['pending_recovery'])
        api_final = start(releases.command(data, trust, 'api', port), 'api-b-recovered');wait(lambda: api('/health/live'))
        worker_final = start(releases.command(data, trust, 'worker'), 'worker-b-recovered');wait(lambda: api('/health/ready'), lambda x: x['ready'])
        check('Recovered release starts and retains all three settled financial identities',
              all(api(scope + '/banking/upi/payments/' + p['id'])['state'] == 'succeeded' for p in [p1, p2]) and api(legacy_scope + '/payments/' + uncertain['id'])['state'] == 'succeeded')
        stop(worker_final);stop(api_final)
        # A recovery retry of an old, superseded transition cannot rewind a
        # newer release under the guise of idempotency.
        denied('A superseded completed request cannot be replayed into an unintended downgrade', lambda: releases.transition(data, artifact_a, trust, runtime_a, None, first_id, 'release-verifier'), 'RELEASE_SUPERSEDED')
        with psycopg.connect(dsn, autocommit=True) as db:
            events = db.execute('SELECT generation,operation FROM release_events ORDER BY generation').fetchall()
            history = db.execute('SELECT count(*) FROM payments WHERE state=\'succeeded\'').fetchone()[0]
        check('Release history contains only four committed generations and no prepared-only phantom entry', events == [(1, 'activate'), (2, 'activate'), (3, 'rollback'), (4, 'activate')] and history >= 1)
        check('Qualification used one fixed source build and one fixed case pack', report['application_fingerprint'] == application_fingerprint() and report['case_pack_sha256'] == case_pack_hash())
        report.update(passed=True, artifacts={'a': a, 'b': b}, deployment_directory=str(data), participant_payments=[p1['id'], p2['id']], uncertain_payment_id=uncertain['id'])
    except Exception as error:
        report.update(error=str(error), traceback=traceback.format_exc())
    finally:
        for child in reversed(processes): stop(child)
        if app: app.close()
        report['storage_admission'] = storage.snapshot()
        report['completed_at'] = time.time();write_document(directory / 'verification.json', report)
        write_document(ROOT / '.local/release-verification.json', report)
        print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'report': str(ROOT / '.local/release-verification.json'), 'error': report.get('error')}), flush=True)
        if not report['passed']: print(report.get('traceback', ''), flush=True)
    return 0 if report['passed'] else 1


def main():
    try:
        with QualificationStorage(ROOT, 'release_upgrade') as storage:
            return run(storage)
    except StorageRefused as error:
        return record_refusal(ROOT,'release-verification.json',error,application_fingerprint(),case_pack_hash())


if __name__ == '__main__':
    raise SystemExit(crash_child(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == '--transition-crash' else main())
