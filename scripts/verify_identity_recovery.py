#!/usr/bin/env python3
"""Rehearse two cold HTTPS identity/bank recoveries with the source unavailable.

Dedicated PostgreSQL roles, real native engines, HTTPS APIs, mTLS banks and an
owned code/PKCE issuer are used. No live rail or institutional acceptance is
implied. Evidence, credentials and failed attempts remain in private .local/.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import traceback
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPSHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra import issuer_recovery as routing, recovery_identity as identity
from avenra import recovery_set as recovery, recovery_restore as restore, recovery_native as native, recovery_banks as banks
from avenra import release_bundle as bundle, release_runtime as releases
from avenra.app import App
from avenra.database_deployment import provision
from avenra.deployment_identity import issue, paths, client_context
from avenra.errors import DomainError
from avenra.gateways import AccountingGateway, EngineError, NoRedirect
from avenra.lab_identity import private_read, private_write
from avenra.local_database import admin_dsn
from avenra.private_config import read_document, write_document, deployment
from avenra.production import POLICY_DEFAULT, ROLE_CAPABILITIES
from avenra.recovery_state import INACTIVE
from avenra.synthetic_identity import Issuer, provision as provision_issuer
from avenra.synthetic_lab import fixture, prepare, authorize, advance, provision as provision_banks, connect, start as start_bank
from avenra.upi_wire import WIRE_LOCAL, WIRE_TLS
from avenra.util import application_fingerprint, canonical, digest, uid
from scripts.qualification_storage import QualificationStorage, StorageRefused, record_refusal, harness_case_pack_hash

REPORT = 'identity-coordinated-recovery-verification.json'


def case_pack_hash():
    return harness_case_pack_hash(ROOT, 'verify_identity_recovery.py')


def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def crash_start(instructions):
    values = read_document(instructions)
    original = identity._registration
    def registration(directory, routes):
        record = original(directory, routes)
        if record and native.owns_listener(record['pid'], routing.origin(record['origin'])):
            os._exit(77)
        return record
    identity._registration = registration
    identity.start(**values)
    return 9


def call(opener, url, data=None, headers=None, form=False):
    body = urlencode(data).encode() if form else canonical(data).encode() if data is not None else None
    request = Request(url, data=body, headers={'Content-Type': 'application/x-www-form-urlencoded' if form else 'application/json', **(headers or {})})
    try:
        response = opener.open(request, timeout=12)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read(4_000_001)
        value = json.loads(raw) if raw and 'application/json' in response.headers.get('Content-Type', '') else raw.decode()
        return response.status, response.headers, value


def error_code(result):
    error = result[2].get('error') if isinstance(result[2], dict) else None
    return error.get('code') if isinstance(error, dict) else error


def browser_context(data, issuer_config, api_reference):
    context = ssl.create_default_context(cafile=str((data / issuer_config).parent / 'ca-cert.pem'))
    keys = paths(data, api_reference, client=True)
    context.load_verify_locations(str(keys['ca']))
    context.load_cert_chain(str(keys['cert']), str(keys['key']))
    return context


def run(storage):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from psycopg.rows import dict_row
    folder = ROOT / '.local/qa' / uid('identity_coordinated_recovery')
    folder.mkdir(mode=0o700, parents=True)
    report = {'passed': False, 'checks': [], 'directory': str(folder), 'created_at': time.time(),
              'application_fingerprint': application_fingerprint(), 'case_pack_sha256': case_pack_hash(),
              'qualification_profile': 'avenra-cold-identity-recovery/1', 'protocol_mode': 'xml', 'participant_mode': 'synthetic_identity',
              'production_ready': False, 'official_conformance': False,
              'scope': 'Owned static synthetic issuer/API recovery with original logical identity, signing and TLS keys, sessions/grants, real mTLS bank effects, process-death replay/restart and two cold generations. Dynamic stable-origin discovery, external IdPs, combined AA HTTPS recovery, service gateways, off-host/fleet recovery and independent/institutional qualification remain open.'}
    children, active_targets, sockets, observers, source_attempts = [], [], [], [], []
    observer_stop = threading.Event()
    app = None

    def check(name, condition):
        report['checks'].append({'name': name, 'passed': bool(condition)})
        print(json.dumps({'check': name, 'passed': bool(condition)}), flush=True)
        if not condition:
            raise AssertionError(name)

    def denied(name, operation, code):
        try:
            operation()
        except DomainError as error:
            return check(name, error.code == code)
        check(name, False)

    def start(command, name, environment=None):
        path = folder / (name + '.log')
        with path.open('ab') as output:
            path.chmod(0o600)
            child = subprocess.Popen(command, cwd=folder, env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        children.append(child)
        return child

    def stop(child):
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)

    def wait(operation, predicate=lambda value: bool(value), timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                value = operation()
                if predicate(value):
                    return value
            except (OSError, URLError, EngineError):
                pass
            time.sleep(.1)
        raise TimeoutError('Identity recovery did not reach its required observation; inspect the retained private logs.')

    def observe(number):
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', number))
        listener.listen(32)
        listener.settimeout(.2)
        sockets.append(listener)
        def accept():
            while not observer_stop.is_set():
                try:
                    channel, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                source_attempts.append(number)
                channel.close()
        worker = threading.Thread(target=accept, daemon=True)
        worker.start()
        observers.append(worker)

    def inventory(dsn):
        with psycopg.connect(dsn) as db:
            db.execute("SET LOCAL timezone='UTC'")
            return recovery.database_inventory(db)

    def rows(data, table):
        dsn = private_read(data / 'migration.dsn').decode().strip()
        with psycopg.connect(dsn, row_factory=dict_row) as db:
            return [dict(row) for row in db.execute(sql.SQL('SELECT * FROM {} ORDER BY 1').format(sql.Identifier(table)))]

    class User:
        def __init__(self, context, api_origin, issuer_origin, role, passwords, prefix, authenticate=True):
            self.jar = CookieJar()
            self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context), HTTPCookieProcessor(self.jar))
            self.origin, self.issuer, self.role = api_origin, issuer_origin, role
            if authenticate:
                _, signed = self.begin(passwords)
                callback = call(self.opener, signed[1]['Location'])
                check(prefix + ' code/PKCE establishes a secure ' + role + ' session', callback[0] == 302 and 'Secure' in callback[1].get('Set-Cookie', ''))
                self.csrf = self.api('/api/session', {})[2]['csrf']
                self.identity = self.api('/api/me')[2]

        def begin(self, passwords, finish=True):
            login = call(self.opener, self.origin + '/auth/login')
            form = call(self.opener, login[1]['Location'])
            interaction = re.search('name="interaction" value="([A-Za-z0-9_-]+)"', form[2]).group(1)
            values = {'interaction': interaction, 'username': self.role, 'password': passwords[self.role]}
            return login, call(self.opener, self.issuer + '/login', values, form=True) if finish else values

        def api(self, path, data=None):
            return call(self.opener, self.origin + path, data,
                        {'Origin': self.origin, 'X-Avenra-CSRF': self.csrf} if hasattr(self, 'csrf') else {'Origin': self.origin, 'X-Avenra-Client': 'console'})

    try:
        storage.checkpoint('signed_application_and_offline_runtime')
        release_key, release_trust, recovery_key, recovery_trust = [folder / name for name in ['release-key.raw', 'release-trust.raw', 'recovery-key.raw', 'recovery-trust.raw']]
        bundle.new_key(release_key, release_trust)
        bundle.new_key(recovery_key, recovery_trust)
        built = bundle.build(ROOT, ROOT / '.local/release-inputs/wheels', release_key, folder / 'application.tar.gz', 'identity-recovery-qualification')
        installed = bundle.install(folder / 'application.tar.gz', release_trust, folder / 'source-artifacts')
        runtime = folder / 'source-runtime'
        bundle.prepare_runtime(installed['directory'], release_trust, runtime)
        binaries = folder / 'source-native'
        binaries.mkdir(mode=0o700)
        for role in ['accounting', 'fault']:
            shutil.copyfile(ROOT / '.local/bin' / role, binaries / role)
            (binaries / role).chmod(0o700)
        storage.checkpoint('source_database_creation')
        admin = admin_dsn(ROOT)
        admin_file = folder / 'admin.dsn'
        private_write(admin_file, admin)
        suffix = uuid.uuid4().hex[:20]
        engine_user, engine_db, password = 'avn_id_engine_' + suffix, 'avn_id_source_' + suffix, secrets_token()
        with psycopg.connect(admin, autocommit=True) as db:
            db.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Identifier(engine_user), sql.Literal(password)))
            db.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(engine_db), sql.Identifier(engine_user)))
            db.execute(sql.SQL('ALTER DATABASE {} SET timezone TO {}').format(sql.Identifier(engine_db), sql.Literal('UTC')))
        engine_dsn = make_conninfo(**{**conninfo_to_dict(admin), 'dbname': engine_db, 'user': engine_user, 'password': password})
        engine_file = folder / 'source-accounting.dsn'
        private_write(engine_file, engine_dsn)
        accounting_port, fault_port, api_port, issuer_port = port(), port(), port(), port()
        accounting_origin = 'http://127.0.0.1:' + str(accounting_port)
        api_origin = 'https://127.0.0.1:' + str(api_port)
        environment = os.environ.copy()
        environment['POSTGRES_URI'] = engine_dsn
        engine = start([str(binaries / 'accounting'), 'serve', '--bind', '127.0.0.1:' + str(accounting_port), '--auto-upgrade', '--postgres-max-open-conns', '3', '--postgres-max-idle-conns', '1'], 'source-accounting', environment)
        fault = start([str(binaries / 'fault'), '-host', '127.0.0.1', '-port', str(fault_port), '-seed', '42'], 'source-fault')
        gateway = AccountingGateway(accounting_origin)
        wait(gateway.health)
        data = folder / 'source-deployment'
        provision(data, admin)
        write_document(data / 'engine-endpoints.json', {'format': 'avenra-loopback-engines/1', 'accounting': accounting_origin, 'fault': 'http://127.0.0.1:' + str(fault_port)})
        control_dsn = private_read(data / 'control.dsn').decode().strip()
        app = App(data, control_dsn=control_dsn)
        value = fixture(app)
        tenant, env = value['tenant_id'], value['environment_id']
        bank_workers = provision_banks(app, value)
        bank_processes = [start_bank(app, worker) for worker in bank_workers]
        children.extend(bank_processes)
        for participant in app.upi.overview(tenant, env)['participants']:
            connection = app.upi.connections.create(tenant, env, {'name': 'Identity recovery XML ' + participant['role'], 'participant_id': participant['id'], 'adapter_id': WIRE_LOCAL, 'request_key': uid('xml')})
            checked = app.upi.connections.validate(tenant, env, connection['id'], {})
            app.upi.connections.activate(tenant, env, connection['id'], {'check_id': checked['id']})
        connect(app, value, bank_workers, adapter_id=WIRE_TLS)
        normal = prepare(app, value, 375)
        authorize(app, value, normal)
        normal = advance(app, value, normal)
        uncertain = prepare(app, value, 825, financial_fault='lost_credit_response')
        authorize(app, value, uncertain)
        uncertain = advance(app, value, uncertain, state='unknown')
        app.fidelity.enable(tenant, env, {})
        challenge = app.fidelity.challenge(tenant, env, {'account_id': value['accounts'][0], 'device_id': value['devices'][0],
            'device_proof': value['proofs'][0], 'purpose': 'setup', 'synthetic_bank_verified': True, 'request_key': uid('credential')})
        app.fidelity.complete_credential(tenant, env, challenge['id'], {'device_proof': value['proofs'][0],
            'synthetic_challenge': challenge['synthetic_challenge'], 'new_synthetic_pin': '314159'})
        ledger = app.environment(tenant, env)['ledger_name']
        credit = uncertain['operation']['legs'][1]
        original_credit = gateway.transactions(ledger, credit['id'])
        check('Dedicated native and mTLS banks retain an actually committed unacknowledged original credit', normal['evidence']['valid'] and uncertain['state'] == 'unknown' and len(original_credit) == 1)
        other_env = app.create_environment(tenant, 'Identity environment restriction')['id']
        app.runtime.publish_policy(tenant, env, {**POLICY_DEFAULT, 'burst': 500, 'rate_per_minute': 60000, 'request_key': uid('policy')}, 'identity-verifier')
        app.close()
        app = None
        portal = issue(data, 'portal')
        issuer = provision_issuer(data, issuer_port, api_origin, tenant)
        issuer_relative = Path(issuer['config']).relative_to(data)
        policy = read_document(data / 'deployment.json')
        policy.update(http={'tls_identity': portal['reference'], 'require_client_certificate': True},
                      identity={'granular': True, 'require_session_subject': True, 'max_session_seconds': 300}, role='api')
        write_document(data / 'deployment.json', policy)
        identity_config = {'mode': 'institutional', 'issuer': issuer['issuer'], 'authorization_endpoint': issuer['issuer'] + '/authorize',
                           'token_endpoint': issuer['issuer'] + '/token', 'jwks_url': issuer['issuer'] + '/jwks', 'ca_file': issuer['ca_file'],
                           'external_origin': api_origin, 'client_id': issuer['client_id'], 'client_secret': issuer['client_secret'], 'audience': issuer['audience']}
        write_document(data / 'identity.json', identity_config)
        releases.transition(data, installed['directory'], release_trust, runtime, None, str(uuid.uuid4()), 'identity-verifier')
        issuer_code = 'import sys;sys.path.insert(0,sys.argv.pop(1));from avenra.synthetic_identity import main;main()'
        issuer_process = start([str(runtime / 'bin/python'), '-I', '-B', '-c', issuer_code, installed['directory'], '--config', issuer['config']], 'source-issuer')
        source_api = start(releases.command(data, release_trust, 'api', api_port), 'source-api')
        context = browser_context(data, issuer_relative, portal['reference'])
        plain = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))
        wait(lambda: call(plain, api_origin + '/health/live'), lambda result: result[0] == 200)
        wait(lambda: call(plain, issuer['issuer'] + '/jwks'), lambda result: result[0] == 200)
        users = {role: User(context, api_origin, issuer['issuer'], role, issuer['passwords'], 'Source') for role in ROLE_CAPABILITIES}
        scope = '/api/tenants/' + tenant + '/environments/' + env
        admin_user = users['administrator']
        restricted = {'principal': users['operator'].identity['principal'], 'capabilities': ROLE_CAPABILITIES['operator'], 'environment_ids': [env], 'expires_at': time.time() + 600}
        grant = admin_user.api(scope + '/banking/runtime/grants', restricted)
        check('Source administrator stores a bounded original subject and environment grant', grant[0] == 202)
        complaint_result = admin_user.api(scope + '/banking/fidelity/complaints', {'payment_id': normal['id'], 'reason': 'status_mismatch', 'request_key': uid('complaint')})
        check('Source lifecycle accepts the original financial complaint', complaint_result[0] == 202)
        complaint = complaint_result[2]
        check('Source lifecycle investigates the original complaint before proposing a decision', admin_user.api(scope + '/banking/fidelity/complaints/' + complaint['id'] + '/investigate', {})[0] == 200)
        proposal_result = admin_user.api(scope + '/banking/fidelity/complaints/' + complaint['id'] + '/propose', {'resolution': 'no_financial_error', 'request_key': uid('proposal')})
        check('Source maker stores an attributable original complaint proposal', proposal_result[0] == 202)
        proposal = proposal_result[2]
        approval_path = scope + '/banking/fidelity/proposals/' + proposal['id'] + '/approve'
        revoked_cookie = next(item.value for item in users['observer'].jar if item.name == 'avenra_session')
        check('Source logout records an explicit original session revocation', users['observer'].api('/api/session/logout', {})[0] == 200)
        pending_code = User(context, api_origin, issuer['issuer'], 'operator', issuer['passwords'], '', False)
        _, old_callback = pending_code.begin(issuer['passwords'])
        pending_interaction = User(context, api_origin, issuer['issuer'], 'operator', issuer['passwords'], '', False)
        _, old_interaction = pending_interaction.begin(issuer['passwords'], False)
        stop(source_api)
        source_state = {name: rows(data, name) for name in ['sessions', 'runtime_sessions', 'runtime_grants']}
        source_files = {path.relative_to(data).as_posix(): bundle.sha(path) for path in data.rglob('*') if path.is_file() and path.parts[-2] == 'synthetic-issuer'}
        stop(issuer_process)
        for child in bank_processes:
            stop(child)
        stop(engine)
        stop(fault)
        storage.checkpoint('first_cold_capture_and_restore')
        checkpoint = folder / 'checkpoint'
        captured = recovery.capture(data, checkpoint, installed['directory'], release_trust, recovery_key, engine_file, binaries,
                                    '/opt/homebrew/opt/postgresql@16/bin', str(uuid.uuid4()), 'identity-verifier')
        for number in [accounting_port, fault_port, api_port, issuer_port, *(urlsplit(worker['endpoint']).port for worker in bank_workers)]:
            observe(number)
        for original in [data, runtime, folder / 'source-artifacts', binaries]:
            original.rename(original.with_name(original.name + '-unavailable'))
        target = folder / 'recovered'
        recovery.stage(checkpoint, target, recovery_trust, release_trust)
        imported = restore.restore_databases(target, recovery_trust, release_trust, admin_file, '/opt/homebrew/opt/postgresql@16/bin', str(uuid.uuid4()), 'identity-verifier')
        check('New identity recovery databases match every signed original table and sequence', all(inventory(private_read(target / 'data' / name).decode().strip()) == recovery.verify(checkpoint, recovery_trust, release_trust)['manifest']['databases'][role] for name, role in [('migration.dsn', 'control'), ('accounting.dsn', 'accounting')]))
        new_accounting, new_fault, new_api, new_issuer = port(), port(), port(), port()
        recovered_origin = 'http://127.0.0.1:' + str(new_accounting)
        recovered_fault = 'http://127.0.0.1:' + str(new_fault)
        recovered_api = 'https://127.0.0.1:' + str(new_api)
        native.start(target, new_accounting, new_fault, recovery_trust, release_trust)
        active_targets.append(target)
        bank_ports = {worker['worker_id']: port() for worker in bank_workers}
        banks.start(target, bank_ports, recovery_trust, release_trust, str(uuid.uuid4()), 'identity-verifier')
        denied('Institutional recovery cannot activate without an owned replacement issuer', lambda: restore.activate(target, recovery_trust, release_trust, recovered_origin, recovered_fault, str(uuid.uuid4()), 'identity-verifier'), 'RECOVERY_IDENTITY_REQUIRED')
        saved_identity = private_read(target / 'data/identity.json')
        write_document(target / 'data/identity.json', {'mode': 'local'})
        denied('Changing copied institutional identity to local cannot bypass activation', lambda: restore.activate(target, recovery_trust, release_trust, recovered_origin, recovered_fault, str(uuid.uuid4()), 'identity-verifier'), 'RECOVERY_IDENTITY_BINDING')
        from avenra.lab_identity import replace_private
        replace_private(target / 'data/identity.json', saved_identity)
        identity_request = str(uuid.uuid4())
        arguments = {'directory': str(target), 'issuer_port': new_issuer, 'api_origin': recovered_api, 'recovery_trust': str(recovery_trust),
                     'release_trust': str(release_trust), 'request_id': identity_request, 'actor': 'identity-verifier'}
        instructions = folder / 'identity-start-crash.json'
        write_document(instructions, arguments)
        crashed = start([sys.executable, '-B', str(Path(__file__).resolve()), '--start-crash', str(instructions)], 'identity-operator-crash')
        wait(lambda: crashed.poll() is not None)
        routes = routing.load(target / 'data')
        first_registration = identity._registration(target, routes)
        reused = identity.start(**arguments)
        check('Actual operator death leaves an owned inactive issuer that exact retry reuses', crashed.returncode == 77 and first_registration and reused['reused_plan'] and reused['service']['pid'] == first_registration['pid'] and (target / 'data' / INACTIVE).exists())
        denied('An issuer retry cannot change its original callback intent', lambda: identity.start(**{**arguments, 'api_origin': 'https://127.0.0.1:' + str(port())}), 'RECOVERY_CONFLICT')
        recovered_context = browser_context(target / 'data', issuer_relative, portal['reference'])
        plain = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=recovered_context))
        inactive_authorize = call(plain, routes['issuer_origin'] + '/authorize')
        inactive_token = call(plain, routes['issuer_origin'] + '/token', {}, form=True)
        check('An inactive replacement issuer proves its keys but refuses authorization and tokens', error_code(inactive_authorize) == 'RECOVERY_INACTIVE' and error_code(inactive_token) == 'RECOVERY_INACTIVE' and identity.check(target)['service']['pid'] == first_registration['pid'])
        check('Replacement-origin discovery explicitly refuses a false logical issuer claim', error_code(call(plain, routes['issuer_origin'] + '/.well-known/openid-configuration')) == 'RECOVERY_IDENTITY_STATIC')
        check_wrong_leaf(folder, target / 'data', routes, check)
        check('Recovered sessions preserve every original subject, expiry and grant row', source_state == {name: rows(target / 'data', name) for name in source_state})
        restore.activate(target, recovery_trust, release_trust, recovered_origin, recovered_fault, str(uuid.uuid4()), 'identity-verifier')
        denied('Managed API startup refuses an unregistered recovered callback port', lambda: releases.command(target / 'data', release_trust, 'api', port()), 'RECOVERY_IDENTITY_BINDING')
        api_process = start(releases.command(target / 'data', release_trust, 'api', new_api), 'recovered-api')
        wait(lambda: call(plain, recovered_api + '/health/live'), lambda result: result[0] == 200)
        for user in users.values():
            user.origin = recovered_api
        old_admin = users['administrator']
        check('A retained original institutional session authenticates at the recovered API', old_admin.api('/api/me')[2]['principal'] == old_admin.identity['principal'])
        check('Original session revocation survives endpoint replacement', call(plain, recovered_api + '/api/me', headers={'Cookie': 'avenra_session=' + revoked_cookie})[0] == 401)
        recovered_users = {role: User(recovered_context, recovered_api, routes['issuer_origin'], role, issuer['passwords'], 'Recovered') for role in ROLE_CAPABILITIES}
        new_admin = recovered_users['administrator']
        check('Fresh recovered HTTPS sign-in preserves the original principal and approval identity', new_admin.identity['principal'] == old_admin.identity['principal'] and new_admin.identity['approval_principal'] == old_admin.identity['approval_principal'])
        check('Recovered sign-in cannot approve the same person’s original proposal', error_code(new_admin.api(approval_path, {})) == 'SECOND_PRINCIPAL_REQUIRED')
        check('A different recovered reviewer can approve the original proposal', recovered_users['reviewer'].api(approval_path, {})[0] == 200)
        check('Recovered observer cannot create financial work', recovered_users['observer'].api(scope + '/banking/upi/payments', {})[0] == 403)
        check('The original subject grant retains its environment restriction after fresh sign-in', error_code(recovered_users['operator'].api('/api/tenants/' + tenant + '/environments/' + other_env + '/overview')) == 'ENVIRONMENT_PERMISSION')
        pending_code.origin = recovered_api
        old_location = urlsplit(old_callback[1]['Location'])
        old_result = call(pending_code.opener, recovered_api + old_location.path + '?' + old_location.query)
        old_interaction_result = call(pending_interaction.opener, routes['issuer_origin'] + '/login', old_interaction, form=True)
        check('Old code and interaction bindings cannot be redirected to the recovered callback', old_result[0] in {401, 503} and error_code(old_interaction_result) == 'LOGIN_BINDING')
        payments_path = scope + '/banking/upi/payments'
        check('Recovered HTTPS status retains original financial uncertainty before a worker starts', new_admin.api(payments_path + '/' + uncertain['id'])[2]['state'] == 'unknown')
        worker = start(releases.command(target / 'data', release_trust, 'worker'), 'recovered-worker')
        wait(lambda: call(plain, recovered_api + '/health/ready'), lambda result: result[0] == 200)
        done = wait(lambda: new_admin.api(payments_path + '/' + uncertain['id'])[2], lambda payment: payment['state'] == 'succeeded' and payment['evidence'])
        effects = AccountingGateway(recovered_origin).transactions(ledger, credit['id'])
        check('Original uncertain bank credit is confirmed once through recovered HTTPS identity', done['operation_id'] == uncertain['operation_id'] and done['evidence']['valid'] and len(effects) == 1 and str(effects[0]['id']) == str(original_credit[0]['id']))
        first_pid = identity.check(target)['service']['pid']
        os.kill(first_pid, signal.SIGKILL)
        def reaped():
            try:
                os.waitpid(first_pid, os.WNOHANG)
            except ChildProcessError:
                pass
            return native.process_identity(first_pid) is None
        wait(reaped)
        restart = identity.start(**arguments)
        check('Actual issuer death is restarted under the same authenticated recovery decision', restart['reused_plan'] and restart['service']['pid'] != first_pid)
        restarted_user = User(recovered_context, recovered_api, routes['issuer_origin'], 'administrator', issuer['passwords'], 'Restarted')
        check('Issuer restart retains original subject authority', restarted_user.identity['principal'] == old_admin.identity['principal'])
        check('Recovered administrator revokes the original bounded subject grant', new_admin.api(scope + '/banking/runtime/grants/' + grant[2]['id'] + '/revoke', {})[0] == 200)
        stop(worker)
        stop(api_process)
        before = inventory(private_read(target / 'data/migration.dsn').decode().strip())
        baseline = read_document(ROOT / '.local/release-baselines/before-issuer-routes.json')
        if not baseline:
            raise RuntimeError('Preserve the actual signed issuer-fence predecessor before identity recovery qualification.')
        predecessor = bundle.verify_installed(baseline['artifact_directory'], baseline['trust_file'])
        from avenra.participant_capabilities import declared
        supported = declared(predecessor)
        check('The genuine predecessor supports current engine/bank/issuer fences but lacks physical issuer routes', not supported.get('issuer_routes') and all(version in supported.get(key, []) for key, version in releases.require_deployment_capabilities(target / 'data', bundle.verify_installed(target / 'artifacts' / built['artifact_id'], release_trust)).items() if key != 'issuer_routes'))
        denied('A genuine signed predecessor without issuer routing is refused before any control change', lambda: releases.transition(target / 'data', baseline['artifact_directory'], baseline['trust_file'], baseline['runtime_directory'], built['artifact_id'], str(uuid.uuid4()), 'identity-verifier'), 'RELEASE_CAPABILITIES')
        check('Issuer route capability refusal preserves every control row, sequence and release pointer', before == inventory(private_read(target / 'data/migration.dsn').decode().strip()) and releases.status(target / 'data')['selection']['artifact_id'] == built['artifact_id'])
        route_bytes = private_read(target / 'data' / routing.FILE)
        (target / 'data' / routing.FILE).unlink()
        denied('Activated identity routing loss refuses source fallback at startup', lambda: deployment(target / 'data'), 'RECOVERY_IDENTITY_BINDING')
        replace_private(target / 'data' / routing.FILE, route_bytes)
        identity.stop(target)
        banks.stop(target)
        native.stop(target)
        active_targets.remove(target)
        storage.checkpoint('second_cold_identity_generation')
        second_checkpoint = folder / 'second-checkpoint'
        second_capture = recovery.capture(target / 'data', second_checkpoint, target / 'artifacts' / built['artifact_id'], release_trust, recovery_key,
                                         target / 'data/accounting.dsn', target / 'native', '/opt/homebrew/opt/postgresql@16/bin', str(uuid.uuid4()), 'identity-verifier')
        first_rows = {name: rows(target / 'data', name) for name in source_state}
        observe(new_api)
        observe(new_issuer)
        second = folder / 'second-recovered'
        recovery.stage(second_checkpoint, second, recovery_trust, release_trust)
        restore.restore_databases(second, recovery_trust, release_trust, admin_file, '/opt/homebrew/opt/postgresql@16/bin', str(uuid.uuid4()), 'identity-verifier')
        second_accounting, second_fault, second_api, second_issuer = port(), port(), port(), port()
        native.start(second, second_accounting, second_fault, recovery_trust, release_trust)
        active_targets.append(second)
        banks.start(second, {worker['worker_id']: port() for worker in bank_workers}, recovery_trust, release_trust, str(uuid.uuid4()), 'identity-verifier')
        second_api_origin = 'https://127.0.0.1:' + str(second_api)
        target.rename(target.with_name('first-recovered-unavailable'))
        second_args = {**arguments, 'directory': str(second), 'issuer_port': second_issuer, 'api_origin': second_api_origin, 'request_id': str(uuid.uuid4())}
        denied('Second identity recovery cannot reuse its preceding physical issuer listener', lambda: identity.start(**{**second_args, 'issuer_port': new_issuer}), 'RECOVERY_IDENTITY_PLAN')
        identity.start(**second_args)
        check('Second cold generation retains all earlier sessions and grant history without extending expiry', first_rows == {name: rows(second / 'data', name) for name in source_state})
        restore.activate(second, recovery_trust, release_trust, 'http://127.0.0.1:' + str(second_accounting), 'http://127.0.0.1:' + str(second_fault), str(uuid.uuid4()), 'identity-verifier')
        second_context = browser_context(second / 'data', issuer_relative, portal['reference'])
        second_plain = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=second_context))
        api_process = start(releases.command(second / 'data', release_trust, 'api', second_api), 'second-api')
        wait(lambda: call(second_plain, second_api_origin + '/health/live'), lambda result: result[0] == 200)
        second_routes = routing.load(second / 'data')
        second_admin = User(second_context, second_api_origin, second_routes['issuer_origin'], 'administrator', issuer['passwords'], 'Second generation')
        second_operator = User(second_context, second_api_origin, second_routes['issuer_origin'], 'operator', issuer['passwords'], 'Second generation')
        check('Second-generation HTTPS sign-in retains the original subject and revoked grant', second_admin.identity['principal'] == old_admin.identity['principal'] and error_code(second_operator.api(scope + '/banking')) == 'GRANT_REVOKED')
        worker = start(releases.command(second / 'data', release_trust, 'worker'), 'second-worker')
        body = {'payer_id': value['accounts'][0], 'payee_id': value['accounts'][1], 'device_id': value['devices'][0], 'amount_paise': 145, 'request_key': uid('second_recovery')}
        fresh = second_admin.api(payments_path, body)
        check('The twice-recovered HTTPS application accepts new financial work under original device identities', fresh[0] == 202)
        credential_authorization = second_admin.api(scope + '/banking/fidelity/payments/' + fresh[2]['id'] + '/authorize',
            {'device_id': value['devices'][0], 'device_proof': value['proofs'][0], 'synthetic_pin': '314159', 'approved': True})
        check('Second-generation authorization retains the original synthetic credential and device proof', credential_authorization[0] == 202)
        fresh_payment = wait(lambda: second_admin.api(payments_path + '/' + fresh[2]['id'])[2], lambda payment: payment['state'] == 'succeeded' and payment['evidence'])
        check('New financial work completes after two cold identity recoveries with each original native leg posted once', fresh_payment['evidence']['valid'] and all(len(AccountingGateway('http://127.0.0.1:' + str(second_accounting)).transactions(ledger, leg['id'])) == 1 for leg in fresh_payment['operation']['legs']))
        source_root = data.with_name(data.name + '-unavailable')
        check('Recovered identity use preserves every original issuer state, signing key and certificate byte', source_files == {name: bundle.sha(source_root / name) for name in source_files})
        check('Neither identity recovery contacted a preceding source listener', not source_attempts)
        check('Qualification retained one fixed build and one fixed case pack', report['application_fingerprint'] == application_fingerprint() and report['case_pack_sha256'] == case_pack_hash())
        report.update(passed=True, set_id=captured['set_id'], second_recovery_set_id=second_capture['set_id'], artifact_id=built['artifact_id'],
                      restored_databases=imported['databases'], observed_end_to_end_seconds=time.time() - report['created_at'])
    except Exception as error:
        report.update(error=type(error).__name__ + ': ' + str(error), traceback=traceback.format_exc())
    finally:
        for child in reversed(children):
            stop(child)
        if app:
            app.close()
        for target in reversed(active_targets):
            for component, filename in [(identity, routing.FILE), (banks, 'bank-recovery.json'), (native, None)]:
                try:
                    if filename is None or (target / 'data' / filename).exists():
                        component.stop(target)
                except Exception as error:
                    report.setdefault('cleanup_errors', []).append(type(error).__name__ + ': ' + str(error))
                    report['passed'] = False
        observer_stop.set()
        for listener in sockets:
            listener.close()
        for observer in observers:
            observer.join(timeout=1)
        report['storage_admission'] = storage.snapshot()
        report['completed_at'] = time.time()
        write_document(folder / 'verification.json', report)
        summary = {**report, 'evidence': {'path': str(folder / 'verification.json'), 'sha256': bundle.sha(folder / 'verification.json')}}
        write_document(ROOT / '.local' / REPORT, summary)
        print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'directory': str(folder), 'error': report.get('error'), 'cleanup_errors': report.get('cleanup_errors')}), flush=True)
    return 0 if report['passed'] else 1


def secrets_token():
    import secrets
    return secrets.token_urlsafe(36)


def check_wrong_leaf(folder, data, routes, check):
    """Use a real TLS peer with a valid chain but a different server leaf."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from avenra.lab_identity import replace_private
    original_routes = private_read(data / routing.FILE)
    directory = (data / routes['config']).parent
    ca = x509.load_pem_x509_certificate(private_read(directory / 'ca-cert.pem'))
    ca_key = serialization.load_pem_private_key(private_read(directory / 'ca-key.pem'), password=None)
    original = x509.load_pem_x509_certificate(private_read(directory / 'server-cert.pem'))
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    builder = x509.CertificateBuilder().subject_name(original.subject).issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
    for extension in original.extensions:
        if not isinstance(extension.value, x509.SubjectKeyIdentifier):
            builder = builder.add_extension(extension.value, extension.critical)
    certificate = builder.sign(ca_key, hashes.SHA256())
    cert_path, key_path = folder / 'wrong-leaf.pem', folder / 'wrong-leaf-key.pem'
    private_write(cert_path, certificate.public_bytes(serialization.Encoding.PEM))
    private_write(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(5)
    received, failures, handshakes = [], [], []
    def serve():
        try:
            with listener.accept()[0] as channel:
                with context.wrap_socket(channel, server_side=True) as secure:
                    handshakes.append(True)
                    secure.settimeout(3)
                    received.append(secure.recv(65536))
        except Exception as error:
            failures.append(type(error).__name__)
    worker = threading.Thread(target=serve)
    worker.start()
    modified = {**routes, 'issuer_origin': 'https://127.0.0.1:' + str(listener.getsockname()[1])}
    routing.signed_write(data, data / routing.FILE, modified)
    refused = False
    refusal = None
    try:
        routing.call(data, modified, 'POST', '/token', b'client_secret=must-not-be-sent')
    except DomainError as error:
        refusal = error.code
        refused = error.code == 'RECOVERY_IDENTITY_BINDING'
    finally:
        worker.join(timeout=6)
        listener.close()
        replace_private(data / routing.FILE, original_routes)
    write_document(folder/'wrong-leaf-observation.json', {'refusal':refusal,'handshakes':len(handshakes),'request_bytes':sum(map(len,received)),
        'reads':len(received),'server_errors':failures,'thread_alive':worker.is_alive()})
    check('A different certificate under the original CA receives no confidential request bytes', refused and handshakes == [True] and
          sum(map(len,received)) == 0 and all(error in {'SSLEOFError','ConnectionResetError'} for error in failures) and not worker.is_alive())


def main():
    try:
        with QualificationStorage(ROOT, 'identity_recovery') as storage:
            return run(storage)
    except StorageRefused as error:
        return record_refusal(ROOT, REPORT, error, application_fingerprint(), case_pack_hash())


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--start-crash':
        raise SystemExit(crash_start(Path(sys.argv[2])))
    raise SystemExit(main())
