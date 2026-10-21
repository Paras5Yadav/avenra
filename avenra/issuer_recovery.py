"""Explicit physical routes for the captured synthetic issuer and HTTPS API.

The logical issuer, subjects, signing key and registered client stay unchanged.
This is a private static recovery contract, not dynamic OIDC discovery at a new
issuer URL. Each use rechecks the target, immutable identity files and decision.
"""
import hashlib
import hmac
from contextlib import contextmanager
from http.client import HTTPException, HTTPSConnection
import json
import os
from pathlib import Path
import re
import ssl
import stat
import sys

from .bank_recovery import origin
from .errors import DomainError, require
from .issuer_runtime import configuration_directory
from .lab_identity import private_read
from .private_config import read_document, write_document
from .release_bundle import sha
from .util import canonical, digest

FILE = 'issuer-recovery.json'
FORMAT = 'avenra-synthetic-issuer-recovery/1'
PROCESS = 'avenra-synthetic-issuer-recovery-process/1'


def signed_write(data, path, value):
    raw = canonical(value).encode()
    require(len(raw) < 60_000, 'RECOVERY_IDENTITY_CAPACITY', 'Identity recovery exceeds its bounded document profile.', 409)
    signature = hmac.new(private_read(Path(data) / 'evidence.key'), raw, hashlib.sha256).hexdigest()
    write_document(path, {**value, 'signature': signature})


def signed_read(data, path):
    value = read_document(path)
    require(value and isinstance(value.get('signature'), str), 'RECOVERY_IDENTITY_AUTHENTICATION', 'Identity recovery requires an authenticated installation decision.', 409)
    body = {key: item for key, item in value.items() if key != 'signature'}
    expected = hmac.new(private_read(Path(data) / 'evidence.key'), canonical(body).encode(), hashlib.sha256).hexdigest()
    require(hmac.compare_digest(value['signature'], expected), 'RECOVERY_IDENTITY_AUTHENTICATION', 'Identity recovery failed installation authentication.', 409)
    return body


def _hex(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)


def _relative(value):
    path = Path(value) if isinstance(value, str) else Path('/')
    return isinstance(value, str) and bool(path.parts) and not path.is_absolute() and '..' not in path.parts and path.as_posix() == value


def private_file(data, path):
    # Share the existing owner/mode/link/size rules, with an identity-specific
    # error so operators do not mistake a failed identity pin for an AA failure.
    from .aa.routing import private_file as checked_file
    try:
        checked_file(data, path)
    except (DomainError, OSError) as error:
        raise DomainError('RECOVERY_IDENTITY_BINDING', 'Identity recovery requires private ordinary files inside this target.', 409) from error


@contextmanager
def service_log(data, path):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077 and info.st_nlink == 1,
                'RECOVERY_IDENTITY_BINDING', 'Issuer logs must be private ordinary single-link files.', 409)
        private_file(data, path)
        stream = os.fdopen(descriptor, 'ab')
        descriptor = -1
        with stream:
            yield stream
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load(data):
    data = Path(data).absolute()
    private_file(data, data / FILE)
    private_file(data, data / 'evidence.key')
    value = signed_read(data, data / FILE)
    fields = {'format', 'data_directory', 'set_id', 'artifact_id', 'request_id', 'actor', 'intent_hash', 'created_at',
              'accounting_origin', 'fault_origin', 'database_hashes', 'config', 'issuer', 'issuer_origin', 'api_origin',
              'source_origins', 'http_policy', 'identity_policy', 'issuer_certificate_sha256', 'api_certificate_sha256',
              'jwk_sha256', 'files', 'process_executable', 'process_executable_sha256', 'process_command_prefix'}
    require(set(value) == fields and value['format'] == FORMAT and value['data_directory'] == str(data),
            'RECOVERY_IDENTITY_BINDING', 'Identity routes belong to another recovery target or contract.', 409)
    require(all(_hex(value[key]) for key in ['set_id', 'artifact_id', 'intent_hash', 'issuer_certificate_sha256',
                                            'api_certificate_sha256', 'jwk_sha256', 'process_executable_sha256']),
            'RECOVERY_IDENTITY_BINDING', 'Identity recovery pins are invalid.', 409)
    require(isinstance(value['database_hashes'], dict) and set(value['database_hashes']) == {'control.dsn', 'migration.dsn', 'accounting.dsn'}
            and all(_hex(item) for item in value['database_hashes'].values()),
            'RECOVERY_IDENTITY_BINDING', 'Identity recovery must bind every newly imported database identity.', 409)
    for name in value['database_hashes']:
        private_file(data, data / name)
    require(all(sha(data / name) == expected for name, expected in value['database_hashes'].items()),
            'RECOVERY_IDENTITY_BINDING', 'Recovered identity database targets changed.', 409)
    require(_relative(value['config']) and configuration_directory(data / value['config']) == data,
            'RECOVERY_IDENTITY_BINDING', 'The original issuer must remain inside this deployment.', 409)
    for name in ['issuer', 'issuer_origin', 'api_origin']:
        origin(value[name])
    for name in ['accounting_origin', 'fault_origin']:
        origin(value[name], 'http')
    require(isinstance(value['source_origins'], list) and 2 <= len(value['source_origins']) <= 64 and
            len(set(value['source_origins'])) == len(value['source_origins']),
            'RECOVERY_IDENTITY_BINDING', 'Retain the previous physical identity endpoints.', 409)
    previous = {origin(item) for item in value['source_origins']}
    require(origin(value['issuer_origin']) != origin(value['api_origin']) and
            not {origin(value['issuer_origin']), origin(value['api_origin'])} & previous,
            'RECOVERY_IDENTITY_BINDING', 'Use distinct replacement HTTPS endpoints without source fallback.', 409)
    require(isinstance(value['http_policy'], dict) and value['http_policy'].get('tls_identity') and
            isinstance(value['identity_policy'], dict), 'RECOVERY_IDENTITY_BINDING', 'Keep the captured API TLS and authorization policy.', 409)
    config = read_document(data / 'deployment.json', {})
    require(config.get('http', {}) == value['http_policy'] and config.get('identity', {}) == value['identity_policy'],
            'RECOVERY_IDENTITY_BINDING', 'Captured ingress and institutional authorization policy changed.', 409)
    for key in ['process_executable', 'process_command_prefix']:
        require(isinstance(value[key], str) and Path(value[key]).is_absolute(), 'RECOVERY_IDENTITY_BINDING', 'Pin the verified isolated runtime executable.', 409)
    require(isinstance(value['files'], dict) and 1 <= len(value['files']) <= 256 and 'identity.json' in value['files'] and
            value['config'] in value['files'] and all(_relative(name) and _hex(hashed) and
            (name == 'identity.json' or name.startswith('deployment-secrets/')) for name, hashed in value['files'].items()),
            'RECOVERY_IDENTITY_BINDING', 'Identity recovery has an invalid immutable identity inventory.', 409)
    from .recovery_state import INACTIVE
    if not (data / INACTIVE).exists():
        policy = config.get('recovery', {})
        require(policy.get('set_id') == value['set_id'] and policy.get('issuer_routes_sha256') == sha(data / FILE),
                'RECOVERY_IDENTITY_BINDING', 'Active identity routes must match the pinned recovery decision.', 409)
    return value


def verify_files(data, routes):
    data = Path(data).absolute()
    for name, expected in routes['files'].items():
        path = data / name
        private_file(data, path)
        require(sha(path) == expected, 'RECOVERY_IDENTITY_BINDING', 'A captured identity configuration, key or certificate changed.', 409)
    private_file(data, (data / routes['config']).parent / 'issuer.db')


def resolve(data):
    data = Path(data).absolute()
    path = data / FILE
    from .recovery_state import INACTIVE
    policy = read_document(data / 'deployment.json', {}).get('recovery', {})
    if not path.exists() and not path.is_symlink():
        require(not (data / INACTIVE).exists() and not policy.get('issuer_routes_sha256') and
                not (data.parent / 'recovery-stage.json').exists() and not (data.parent / 'recovery-stage.json').is_symlink(),
                'RECOVERY_IDENTITY_REQUIRED', 'Recovered sign-in requires authenticated physical routes; original endpoints are not a fallback.', 409)
        return None
    routes = load(data)
    verify_files(data, routes)
    return routes


def configured(data):
    data = Path(data)
    return bool((data / FILE).exists() or (data / FILE).is_symlink() or
                read_document(data / 'deployment.json', {}).get('recovery', {}).get('issuer_routes_sha256'))


def api_binding(data, host, port):
    if not configured(data):
        return
    routes = resolve(data)
    if routes:
        require(host == '127.0.0.1' and port == origin(routes['api_origin']), 'RECOVERY_IDENTITY_BINDING',
                'Serve the recovered HTTPS API only at its explicitly registered callback origin.', 409)


def configuration(data, original):
    if original.get('mode', 'local') != 'institutional' and not configured(data):
        return original
    routes = resolve(data)
    if routes is None:
        return original
    require(original.get('mode') == 'institutional' and original.get('issuer') == routes['issuer'],
            'RECOVERY_IDENTITY_BINDING', 'Recovery cannot replace the logical issuer or disable institutional identity.', 409)
    issuer = routes['issuer_origin']
    return {**original, 'authorization_endpoint': issuer + '/authorize', 'token_endpoint': issuer + '/token',
            'jwks_url': issuer + '/jwks', 'external_origin': routes['api_origin'],
            'ca_file': str((Path(data) / routes['config']).parent / 'ca-cert.pem'), '_recovery': routes}


def call(data, routes, method, path, body=None, headers=None, timeout=3):
    """Validate TLS, hostname and the captured leaf before sending any secret."""
    current = load(data)
    require(current == routes, 'RECOVERY_IDENTITY_BINDING', 'The selected physical identity decision changed during this request.', 409)
    verify_files(data, routes)
    require((method, path) in {('GET', '/jwks'), ('GET', '/control/probe'), ('POST', '/token')},
            'RECOVERY_IDENTITY_BINDING', 'Unsupported recovered identity transport operation.', 409)
    context = ssl.create_default_context(cafile=str((Path(data) / routes['config']).parent / 'ca-cert.pem'))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    connection = HTTPSConnection('127.0.0.1', origin(routes['issuer_origin']), timeout=timeout, context=context)
    try:
        connection.connect()
        require(hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest() == routes['issuer_certificate_sha256'],
                'RECOVERY_IDENTITY_BINDING', 'The replacement issuer does not hold its captured TLS authority.', 409)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read(65537)
        require(len(raw) <= 65536 and response.status == 200 and response.getheader('content-type', '').split(';')[0] == 'application/json',
                'RECOVERY_IDENTITY_RESPONSE', 'The pinned synthetic issuer refused the request or exceeded its response profile.', 401 if response.status in {400, 401, 403} else 503)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise DomainError('RECOVERY_IDENTITY_RESPONSE', 'The pinned issuer returned an invalid JSON response.', 503) from error
        require(isinstance(value, dict), 'RECOVERY_IDENTITY_RESPONSE', 'The pinned issuer returned an invalid response shape.', 503)
        return value
    except (OSError, TimeoutError, HTTPException) as error:
        raise DomainError('RECOVERY_IDENTITY_UNAVAILABLE', 'The approved recovered issuer is unavailable; no source endpoint was contacted.', 503) from error
    finally:
        connection.close()


def process_command(directory, routes):
    directory = Path(directory).absolute()
    code = 'import sys;sys.path.insert(0,sys.argv.pop(1));from avenra.synthetic_identity import main;main()'
    return [str(directory / 'runtime/bin/python'), '-I', '-B', '-c', code, str(directory / 'artifacts' / routes['artifact_id']),
            '--config', str(directory / 'data' / routes['config']), '--recovery-data', str(directory / 'data')]


def expected_command(directory, routes):
    return ' '.join([routes['process_command_prefix'], *process_command(directory, routes)[1:]])


def register_process(data, config):
    from .recovery_native import process_identity, process_image
    data = Path(data).absolute()
    routes = load(data)
    verify_files(data, routes)
    require(Path(config).absolute() == data / routes['config'], 'RECOVERY_IDENTITY_BINDING', 'Start only the captured synthetic issuer.', 409)
    identity = process_identity(os.getpid())
    require(identity and identity.split(' | started=')[0] == expected_command(data.parent, routes) and
            Path(sys.prefix).resolve() == data.parent / 'runtime' and process_image(os.getpid()) == routes['process_executable'] and
            sha(routes['process_executable']) == routes['process_executable_sha256'],
            'RECOVERY_IDENTITY_PROCESS', 'Recovered issuers must run from the verified isolated application runtime.', 409)
    record = {'format': PROCESS, 'set_id': routes['set_id'], 'routes_sha256': sha(data / FILE),
              'pid': os.getpid(), 'process_identity': identity, 'origin': routes['issuer_origin']}
    signed_write(data, Path(config).parent / 'recovery-process.json', record)
    return routes
