"""Start, authenticate, resume and stop the captured synthetic identity service."""
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import time

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from . import issuer_recovery as routing
from .bank_recovery import origin
from .deployment_identity import paths
from .errors import require
from .issuer_runtime import configurations
from .lab_identity import private_read
from .private_config import read_document
from .recovery_banks import _runtime_image
from .recovery_native import check as native_check, owns_listener, process_identity, process_image
from .recovery_state import INACTIVE
from .release_bundle import sha, verify_installed
from .release_runtime import installation_lock, runtime_receipt, validate_request, require_deployment_capabilities, recovery_configuration
from .util import digest


def _registration(directory, routes):
    data = directory / 'data'
    path = (data / routes['config']).parent / 'recovery-process.json'
    if not path.exists() and not path.is_symlink():
        return None
    routing.private_file(data, path)
    record = routing.signed_read(data, path)
    require(set(record) == {'format', 'set_id', 'routes_sha256', 'pid', 'process_identity', 'origin'} and
            record['format'] == routing.PROCESS and record['set_id'] == routes['set_id'] and
            record['routes_sha256'] == sha(data / routing.FILE) and record['origin'] == routes['issuer_origin'] and
            type(record['pid']) is int and record['pid'] > 1 and isinstance(record['process_identity'], str) and
            record['process_identity'].split(' | started=')[0] == routing.expected_command(directory, routes),
            'RECOVERY_IDENTITY_PROCESS', 'Issuer process registration differs from the original startup intent.', 409)
    current = process_identity(record['pid'])
    require(current is None or current == record['process_identity'], 'RECOVERY_PROCESS_CHANGED', 'The issuer PID was reused; the unrelated process was neither used nor stopped.', 409)
    if current:
        require(process_image(record['pid']) == routes['process_executable'] and sha(routes['process_executable']) == routes['process_executable_sha256'],
                'RECOVERY_IDENTITY_PROCESS', 'The issuer process image differs from the verified offline runtime.', 409)
    return record if current else None


def _probe(data, routes):
    keys = routing.call(data, routes, 'GET', '/jwks')
    require(set(keys) == {'keys'} and isinstance(keys['keys'], list) and len(keys['keys']) == 1 and
            digest(keys['keys'][0]) == routes['jwk_sha256'], 'RECOVERY_IDENTITY_BINDING', 'The replacement issuer changed its original signing authority.', 409)
    challenge = secrets.token_urlsafe(32)
    result = routing.call(data, routes, 'GET', '/control/probe', headers={'X-Avenra-Recovery-Challenge': challenge})
    require(set(result) == {'proof'} and isinstance(result['proof'], str) and len(result['proof']) <= 16000,
            'RECOVERY_IDENTITY_BINDING', 'The recovered issuer did not provide its bounded challenge proof.', 409)
    try:
        header = jwt.get_unverified_header(result['proof'])
        require(header.get('alg') == 'RS256' and header.get('kid') == keys['keys'][0]['kid'] and
                not any(name in header for name in ['jku', 'jwk', 'x5u']),
                'RECOVERY_IDENTITY_BINDING', 'Unsupported issuer recovery proof signing profile.', 409)
        claims = jwt.decode(result['proof'], jwt.PyJWK(keys['keys'][0]).key, algorithms=['RS256'], issuer=routes['issuer'],
                            audience='avenra-recovery-probe', options={'require': ['iss', 'aud', 'sub', 'iat', 'exp', 'nonce', 'set_id', 'routes_sha256']})
    except jwt.PyJWTError:
        require(False, 'RECOVERY_IDENTITY_BINDING', 'The replacement issuer failed its original signing-key challenge.', 409)
    require(claims['nonce'] == challenge and claims['sub'] == 'synthetic-recovery-probe' and
            claims['set_id'] == routes['set_id'] and claims['routes_sha256'] == sha(Path(data) / routing.FILE) and
            0 < claims['exp'] - claims['iat'] <= 30,
            'RECOVERY_IDENTITY_BINDING', 'Issuer proof is stale or belongs to another recovery decision.', 409)


def check(directory):
    directory = Path(directory).absolute()
    data = directory / 'data'
    routes = routing.load(data)
    routing.verify_files(data, routes)
    native_check(directory, routes['accounting_origin'], routes['fault_origin'])
    record = routing.signed_read(data, directory / 'identity-service.json')
    process = _registration(directory, routes)
    require(set(record) == {'format', 'set_id', 'routes_sha256', 'service', 'started_at'} and
            record['format'] == 'avenra-recovery-identity/1' and record['set_id'] == routes['set_id'] and
            record['routes_sha256'] == sha(data / routing.FILE) and process and record['service'] == process and
            owns_listener(process['pid'], origin(routes['issuer_origin'])),
            'RECOVERY_IDENTITY_PROCESS', 'Start the owned, authenticated replacement issuer before activation.', 409)
    _probe(data, routes)
    return record


def _plan(directory, stage, checked, issuer_port, api_origin, request_id, actor):
    data = directory / 'data'
    require(not stage.get('private_roles', {}).get('files'), 'RECOVERY_BINDINGS_REQUIRED',
            'The captured AA ingress also needs a qualified HTTPS API upstream before combined identity recovery can activate.', 409)
    require(type(issuer_port) is int and 1024 <= issuer_port <= 65535 and 1024 <= origin(api_origin) <= 65535,
            'RECOVERY_IDENTITY_PLAN', 'Use explicit unprivileged loopback HTTPS issuer and API ports.', 409)
    native = read_document(directory / 'native-services.json')
    require(native, 'RECOVERY_NATIVE_REQUIRED', 'Start owned restored native services before the identity issuer.', 409)
    accounting = native['services']['accounting']['origin']
    fault = native['services']['fault']['origin']
    native_check(directory, accounting, fault)
    imported = read_document(directory / 'recovery-import.json')
    require(imported and imported['set_id'] == checked['set_id'] and
            all(sha(data / name) == expected for name, expected in imported['credential_hashes'].items()),
            'RECOVERY_DATABASE_TARGET', 'Identity startup requires the unchanged newly imported database identities.', 409)
    intent = {'set_id': checked['set_id'], 'artifact_id': stage['artifact_id'], 'issuer_port': issuer_port, 'api_origin': api_origin,
              'accounting_origin': accounting, 'fault_origin': fault, 'request_id': request_id, 'actor': actor}
    if (data / routing.FILE).exists() or (data / routing.FILE).is_symlink():
        routes = routing.load(data)
        require(routes['intent_hash'] == digest(intent), 'RECOVERY_CONFLICT', 'Keep the original issuer/API endpoints, actor and request on retry.', 409)
        routing.verify_files(data, routes)
        return routes
    captured = directory / 'payload/private-deployment'
    manifest = read_document(captured / 'manifest.json')
    inventory = {entry['path']: entry for entry in manifest['files']}
    source = read_document(captured / 'identity.json', {})
    deployment = read_document(captured / 'deployment.json', {})
    require(source.get('mode') == 'institutional' and deployment.get('http', {}).get('tls_identity') and not deployment.get('transport'),
            'RECOVERY_IDENTITY_PLAN', 'This owned issuer profile requires captured institutional identity, HTTPS ingress and supported native bindings.', 409)
    require(sha(data / 'identity.json') == inventory['identity.json']['sha256'] and
            all(read_document(data / 'deployment.json', {}).get(key, {}) == deployment.get(key, {}) for key in ['http', 'identity']),
            'RECOVERY_IDENTITY_BINDING', 'Preserve the captured identity and API authorization settings before planning recovery.', 409)
    issuers = configurations(data)
    require(len(issuers) == 1, 'RECOVERY_IDENTITY_PLAN', 'Select the one captured owned synthetic issuer; external providers remain unsupported.', 409)
    path = issuers[0]
    issuer = read_document(path)
    require(source.get('issuer') == issuer['issuer'] and source.get('client_id') == issuer['client_id'] and
            source.get('client_secret') == issuer['client_secret'] and source.get('audience') == issuer['api_audience'] and
            issuer['redirect_uri'] == source.get('external_origin', '') + '/auth/callback' and
            all(source.get(field) == issuer['issuer'] + endpoint for field, endpoint in
                [('authorization_endpoint', '/authorize'), ('token_endpoint', '/token'), ('jwks_url', '/jwks')]),
            'RECOVERY_IDENTITY_BINDING', 'The captured API and issuer disagree on the original logical issuer, client, audience or callback.', 409)
    ca = Path(source.get('ca_file', ''))
    require(ca.is_absolute() and '..' not in ca.parts and ca.parts[-3:] == ('deployment-secrets', path.parent.name, 'ca-cert.pem'),
            'RECOVERY_IDENTITY_BINDING', 'The captured API must trust the owned issuer’s original private TLS authority.', 409)
    issuer_keys = paths(data, issuer['identity_reference'])
    api_keys = paths(data, deployment['http']['tls_identity'])
    folders = {path.parent.relative_to(data).as_posix(), api_keys['cert'].parent.relative_to(data).as_posix()}
    files = {'identity.json': sha(data / 'identity.json')}
    for name, entry in inventory.items():
        if Path(name).parent.as_posix() not in folders:
            continue
        current = data / name
        routing.private_file(data, current)
        require(current.stat().st_size == entry['bytes'] and sha(current) == entry['sha256'],
                'RECOVERY_IDENTITY_BINDING', 'Captured issuer state or TLS authority changed before the first coordinated start.', 409)
        if Path(name).name not in {'issuer.db', 'recovery-process.json', 'recovery-service.log'}:
            files[name] = entry['sha256']
    require(all(key.relative_to(data).as_posix() in files for keys in [issuer_keys,api_keys] for key in keys.values()) and
            (path.parent/'signing-key.pem').relative_to(data).as_posix() in files,
            'RECOVERY_IDENTITY_BINDING', 'Capture every original issuer and API TLS/signing credential before recovery.', 409)
    key = serialization.load_pem_private_key(private_read(path.parent / 'signing-key.pem'), password=None)
    jwk = {**jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True), 'kid': issuer['key_id'], 'alg': 'RS256', 'use': 'sig'}
    source_origins = {source['issuer'], source['external_origin']}
    prior = captured / routing.FILE
    if prior.exists():
        old = routing.signed_read(data, prior)
        require(deployment.get('recovery', {}).get('issuer_routes_sha256') == sha(prior) and old.get('issuer') == issuer['issuer'] and old.get('format') == routing.FORMAT,
                'RECOVERY_IDENTITY_BINDING', 'The previous recovered identity decision differs from its captured deployment pin.', 409)
        source_origins.update([*old['source_origins'], old['issuer_origin'], old['api_origin']])
    reserved = {origin(accounting, 'http'), origin(fault, 'http'), *(origin(item) for item in source_origins)}
    for name, field in [('bank-services.json', 'services'), ('peer-services.json', 'services'), ('aa-services.json', 'services')]:
        record = read_document(directory / name)
        if record:
            reserved.update(origin(item['origin']) for item in record[field].values())
    require(issuer_port != origin(api_origin) and not {issuer_port, origin(api_origin)} & reserved,
            'RECOVERY_IDENTITY_PLAN', 'Use distinct new issuer/API ports separate from all original and recovered service listeners.', 409)
    image, prefix = _runtime_image(directory / 'runtime')
    routes = {'format': routing.FORMAT, 'data_directory': str(data), 'set_id': checked['set_id'], 'artifact_id': stage['artifact_id'],
              'request_id': request_id, 'actor': actor, 'intent_hash': digest(intent), 'created_at': time.time(),
              'accounting_origin': accounting, 'fault_origin': fault, 'database_hashes': imported['credential_hashes'],
              'config': path.relative_to(data).as_posix(), 'issuer': issuer['issuer'], 'issuer_origin': 'https://127.0.0.1:' + str(issuer_port),
              'api_origin': api_origin, 'source_origins': sorted(source_origins), 'http_policy': deployment['http'],
              'identity_policy': deployment.get('identity', {}), 'files': files, 'jwk_sha256': digest(jwk),
              'issuer_certificate_sha256': x509.load_pem_x509_certificate(private_read(issuer_keys['cert'])).fingerprint(hashes.SHA256()).hex(),
              'api_certificate_sha256': x509.load_pem_x509_certificate(private_read(api_keys['cert'])).fingerprint(hashes.SHA256()).hex(),
              'process_executable': image, 'process_executable_sha256': sha(image), 'process_command_prefix': prefix}
    routing.signed_write(data, data / routing.FILE, routes)
    routing.load(data)
    return routes


def start(directory, issuer_port, api_origin, recovery_trust, release_trust, request_id, actor):
    from .recovery_restore import checked_stage
    directory, stage, checked = checked_stage(directory, recovery_trust, release_trust)
    validate_request(request_id, None, actor, 'activate')
    data = directory / 'data'
    with installation_lock(directory):
        if not (data / INACTIVE).exists():
            from .private_config import deployment
            activation = read_document(directory / 'recovery-activation.json', {})
            policy = deployment(data).get('recovery', {})
            selection = read_document(data / 'release-selection.json', {})
            require(activation.get('set_id') == checked['set_id'] == policy.get('set_id') and
                    activation.get('artifact_id') == stage['artifact_id'] == selection.get('artifact_id') and
                    policy.get('issuer_routes_sha256') == sha(data / routing.FILE),
                    'RECOVERY_IDENTITY_BINDING', 'Issuer restart requires the same activated recovery and selected application.', 409)
        artifact = verify_installed(directory / 'artifacts' / stage['artifact_id'], release_trust)
        runtime_receipt(artifact, directory / 'runtime')
        reused = (data / routing.FILE).exists()
        routes = _plan(directory, stage, checked, issuer_port, api_origin, request_id, actor)
        require_deployment_capabilities(data, artifact, recovery_configuration(data, checked['set_id']))
        child = None
        try:
            process = _registration(directory, routes)
            if not process:
                with socket.socket() as listener:
                    try:
                        listener.bind(('127.0.0.1', issuer_port))
                    except OSError:
                        require(False, 'RECOVERY_PORT_BUSY', 'The replacement issuer port is occupied; no unrelated process was changed.', 409)
                log_path = (data / routes['config']).parent / 'recovery-service.log'
                with routing.service_log(data,log_path) as log:
                    child = subprocess.Popen(routing.process_command(directory, routes), cwd=artifact['directory'], stdin=subprocess.DEVNULL,
                                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                process = _registration(directory, routes)
                if process and owns_listener(process['pid'], issuer_port):
                    _probe(data, routes)
                    break
                require(child is None or child.poll() is None, 'RECOVERY_IDENTITY_START', 'The recovered issuer exited; inspect its private service log.', 409)
                time.sleep(.1)
            else:
                raise TimeoutError('The replacement issuer did not establish its authenticated owned listener.')
            routing.signed_write(data, directory / 'identity-service.json', {'format': 'avenra-recovery-identity/1', 'set_id': checked['set_id'],
                'routes_sha256': sha(data / routing.FILE), 'service': process, 'started_at': time.time()})
            check(directory)
            return {'started': True, 'reused_plan': reused, 'service': {'pid': process['pid'], 'origin': process['origin']}}
        except BaseException:
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            raise


def stop(directory):
    directory = Path(directory).absolute()
    data = directory / 'data'
    routes = routing.load(data)
    with installation_lock(directory):
        record = _registration(directory, routes)
        if record:
            os.kill(record['pid'], signal.SIGTERM)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and process_identity(record['pid']) == record['process_identity']:
                try:
                    done, _ = os.waitpid(record['pid'], os.WNOHANG)
                    if done:
                        break
                except ChildProcessError:
                    pass
                time.sleep(.1)
            require(process_identity(record['pid']) != record['process_identity'], 'RECOVERY_PROCESS_DRAIN', 'The owned issuer is still draining.', 409)
        if (directory / 'identity-service.json').exists():
            os.replace(directory / 'identity-service.json', directory / ('identity-service-stopped-' + str(time.time_ns()) + '.json'))
    return {'stopped': True}
