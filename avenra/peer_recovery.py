"""Authenticated physical routes that preserve original participant identities."""
import hashlib
import hmac
import os
from pathlib import Path
import re
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from .bank_recovery import origin, private_materialized
from .errors import require
from .lab_identity import private_read
from .participant_peer_identity import binding
from .private_config import read_document, write_document
from .util import canonical

FILE = 'peer-recovery.json'
FORMAT = 'avenra-peer-recovery-routes/1'
IDENTITY_FILES = {'ca.pem', 'ca-key.pem', 'server.pem', 'server-key.pem', 'server-signer.raw'}


def signed_read(data, path):
    value = read_document(path)
    require(value and isinstance(value.get('signature'), str), 'RECOVERY_PEER_AUTHENTICATION',
            'Participant recovery state is missing installation authentication.', 409)
    payload = {key: item for key, item in value.items() if key != 'signature'}
    expected = hmac.new(private_read(Path(data) / 'evidence.key'), canonical(payload).encode(), hashlib.sha256).hexdigest()
    require(hmac.compare_digest(value['signature'], expected), 'RECOVERY_PEER_AUTHENTICATION',
            'Participant recovery state failed installation authentication.', 409)
    return payload


def signed_write(data, path, payload):
    raw = canonical(payload).encode()
    require(len(raw) < 60_000, 'RECOVERY_PEER_CAPACITY', 'Participant recovery state exceeds its declared document bound.', 409)
    signature = hmac.new(private_read(Path(data) / 'evidence.key'), raw, hashlib.sha256).hexdigest()
    write_document(path, {**payload, 'signature': signature})


def load(data):
    data = Path(data).resolve()
    value = signed_read(data, data / FILE)
    fields = {'format', 'data_directory', 'set_id', 'artifact_id', 'request_id', 'actor', 'intent_hash',
              'control_dsn_sha256', 'workers', 'authorities', 'created_at',
              'process_executable', 'process_executable_sha256', 'process_command_prefix'}
    require(set(value) == fields and value['format'] == FORMAT and value['data_directory'] == str(data) and
            value['control_dsn_sha256'] == hashlib.sha256(private_read(data / 'control.dsn')).hexdigest() and
            isinstance(value['workers'], dict) and 0 < len(value['workers']) <= 32 and
            isinstance(value['authorities'], dict) and len(value['authorities']) == len(value['workers']) and
            all(isinstance(value[key], str) and re.fullmatch('[0-9a-f]{64}', value[key]) for key in
                ['set_id', 'artifact_id', 'intent_hash', 'control_dsn_sha256', 'process_executable_sha256']) and
            all(isinstance(value[key], str) and Path(value[key]).is_absolute() for key in ['process_executable', 'process_command_prefix']),
            'RECOVERY_PEER_BINDING', 'Participant routes belong to another recovery target or runtime.', 409)
    ports = set()
    for worker_id, worker in value['workers'].items():
        require(isinstance(worker_id, str) and re.fullmatch('peer_[0-9a-f]{20}', worker_id) and isinstance(worker, dict) and
                set(worker) == {'origin', 'logical_endpoint', 'authority_hash', 'identity_file_hashes', 'server_certificate_sha256'} and
                isinstance(worker['identity_file_hashes'], dict) and set(worker['identity_file_hashes']) == IDENTITY_FILES and
                all(isinstance(item, str) and re.fullmatch('[0-9a-f]{64}', item) for item in
                    [worker['authority_hash'], worker['server_certificate_sha256'], *worker['identity_file_hashes'].values()]),
                'RECOVERY_PEER_BINDING', 'Participant route has an invalid authority inventory.', 409)
        port = origin(worker['origin']); origin(worker['logical_endpoint'])
        require(port not in ports, 'RECOVERY_PEER_BINDING', 'Participant physical routes must be distinct.', 409)
        ports.add(port)
    require(all(isinstance(key, str) and re.fullmatch('[0-9a-f]{64}', key) and worker_id in value['workers'] and
                value['workers'][worker_id]['authority_hash'] == key for key, worker_id in value['authorities'].items()) and
            set(value['authorities'].values()) == set(value['workers']),
            'RECOVERY_PEER_BINDING', 'Participant authority inventory is incomplete.', 409)
    return value


def resolve(identity):
    data = identity.installation
    policy = read_document(data / 'deployment.json', {}).get('recovery', {})
    path = data / FILE
    inactive = (data / 'recovery-inactive.json').exists()
    pinned = policy.get('peer_routes_sha256')
    if policy and not inactive:
        require(pinned and path.exists() and hashlib.sha256(private_read(path)).hexdigest() == pinned,
                'RECOVERY_PEER_BINDING', 'Activated participant routes are missing or changed; source fallback is forbidden.', 409)
    if not path.exists() and not path.is_symlink():
        require(not policy and not inactive, 'RECOVERY_PEER_REQUIRED', 'Recovered participants require authenticated physical routes.', 409)
        return None
    routes = load(data); document = identity.document
    require(routes['authorities'].get(identity.binding) == document['worker_id'],
            'RECOVERY_PEER_BINDING', 'No captured route is authorized for this original participant authority.', 409)
    worker = routes['workers'][document['worker_id']]
    require(worker['logical_endpoint'] == document['endpoint'], 'RECOVERY_PEER_BINDING', 'Recovery cannot rewrite a logical participant endpoint.', 409)
    return {**worker, 'set_id': routes['set_id']}


def worker_binding(data, config):
    data, config = Path(data).resolve(), Path(config).absolute()
    configuration = read_document(config); routes = load(data)
    worker = routes['workers'].get(configuration['worker_id'])
    require(worker and config == data / 'synthetic-participants' / configuration['worker_id'] / 'worker.json' and
            worker['authority_hash'] == binding(configuration),
            'RECOVERY_PEER_BINDING', 'Restored worker differs from its captured logical authority.', 409)
    for name in ['worker.json', *IDENTITY_FILES, 'participant.sqlite']:
        path = config.parent / name
        if name != 'participant.sqlite' or path.exists() or path.is_symlink(): private_materialized(data, path)
    for name, hashed in worker['identity_file_hashes'].items():
        require(hashlib.sha256(private_read(config.parent / name)).hexdigest() == hashed,
                'RECOVERY_PEER_BINDING', 'Participant server identity differs from its authenticated route.', 409)
    certificate = x509.load_pem_x509_certificate(private_read(config.parent / 'server.pem'))
    require(certificate.fingerprint(hashes.SHA256()).hex() == worker['server_certificate_sha256'],
            'RECOVERY_PEER_BINDING', 'Participant TLS certificate differs from the original identity.', 409)
    return routes, worker


def process_command(directory, artifact_id, worker_id):
    directory = Path(directory).resolve()
    code = 'import sys;sys.path.insert(0,sys.argv.pop(1));from avenra.participant_peer import main;main()'
    return [str(directory / 'runtime/bin/python'), '-I', '-B', '-c', code,
            str(directory / 'artifacts' / artifact_id), '--config',
            str(directory / 'data/synthetic-participants' / worker_id / 'worker.json'),
            '--recovery-data', str(directory / 'data')]


def expected_command(directory, routes, worker_id):
    return ' '.join([routes['process_command_prefix'], *process_command(directory, routes['artifact_id'], worker_id)[1:]])


def register_process(data, config):
    from .recovery_native import process_identity, process_image
    data = Path(data).resolve(); routes, worker = worker_binding(data, config)
    worker_id = read_document(config)['worker_id']; identity = process_identity(os.getpid())
    require(identity and identity.split(' | started=')[0] == expected_command(data.parent, routes, worker_id) and
            Path(sys.prefix).resolve() == data.parent / 'runtime' and process_image(os.getpid()) == routes['process_executable'] and
            hashlib.sha256(Path(routes['process_executable']).read_bytes()).hexdigest() == routes['process_executable_sha256'],
            'RECOVERY_PEER_PROCESS', 'Recovered participants must run from the verified offline application runtime.', 409)
    signed_write(data, Path(config).parent / 'recovery-process.json',
                 {'format': 'avenra-peer-recovery-process/1', 'worker_id': worker_id, 'set_id': routes['set_id'],
                  'routes_sha256': hashlib.sha256(private_read(data / FILE)).hexdigest(), 'pid': os.getpid(),
                  'process_identity': identity, 'origin': worker['origin']})
    return worker['origin']
