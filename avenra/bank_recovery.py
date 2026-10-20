"""Authenticated physical routes for recovered synthetic bank authorities.

Logical connection documents, financial payloads and signing identities are
unchanged. This owner-controlled file is never accepted through a tenant API.
"""
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from .errors import require
from .lab_identity import private_read
from .private_config import read_document, write_document
from .util import canonical, digest

FILE = 'bank-recovery.json'
FORMAT = 'avenra-bank-recovery-routes/1'
IDENTITY_FILES = {'ca.pem', 'ca-key.pem', 'server.pem', 'server-key.pem', 'server-signer.raw'}
AUTHORITY_FIELDS = ['worker_id', 'bank_id', 'tenant_id', 'environment_id', 'ledger_name',
                    'endpoint', 'transport_hash', 'server_signer_public', 'ca_sha256',
                    'account_addresses', 'accounting_origin']


def origin(value, scheme='https'):
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == scheme and parsed.hostname == '127.0.0.1' and
                 type(parsed.port) is int and 1024 <= parsed.port <= 65535 and
                 parsed.netloc == '127.0.0.1:' + str(parsed.port) and
                 not parsed.path and not parsed.query and not parsed.fragment)
    except (ValueError, TypeError): valid = False
    require(valid, 'RECOVERY_BANK_ROUTE', 'Recovered banks require an explicit unprivileged literal loopback origin.', 409)
    return parsed.port


def authority(configuration):
    return digest({name: configuration[name] for name in AUTHORITY_FIELDS})


def authenticate(data, value):
    return hmac.new(private_read(Path(data) / 'evidence.key'), canonical(value).encode(), hashlib.sha256).hexdigest()


def signed_read(data, path):
    value = read_document(path)
    require(value and isinstance(value.get('signature'), str), 'RECOVERY_BANK_AUTHENTICATION', 'Bank recovery state is missing its installation authentication.', 409)
    payload = {key: item for key, item in value.items() if key != 'signature'}
    require(hmac.compare_digest(value['signature'], authenticate(data, payload)), 'RECOVERY_BANK_AUTHENTICATION', 'Bank recovery state failed installation authentication.', 409)
    return payload


def signed_write(data, path, payload):
    require(len(canonical(payload).encode()) < 60_000, 'RECOVERY_BANK_CAPACITY', 'Bank recovery state exceeds its declared document bound.', 409)
    write_document(path, {**payload, 'signature': authenticate(data, payload)})


def load(data):
    data = Path(data).resolve()
    value = signed_read(data, data / FILE)
    fields = {'format','data_directory','set_id','artifact_id','request_id','actor','intent_hash',
              'accounting_origin','fault_origin','accounting_dsn_sha256','workers','authorities','created_at',
              'process_executable','process_executable_sha256','process_command_prefix'}
    require(set(value) == fields and value.get('format') == FORMAT and value.get('data_directory') == str(data) and
            value.get('accounting_dsn_sha256') == hashlib.sha256(private_read(data / 'accounting.dsn')).hexdigest() and
            isinstance(value.get('workers'), dict) and 0 < len(value['workers']) <= 32 and
            isinstance(value.get('authorities'), dict) and 0 < len(value['authorities']) <= 64,
            'RECOVERY_BANK_BINDING', 'Bank routes belong to another recovery target or database.', 409)
    origin(value['accounting_origin'], 'http')
    origin(value['fault_origin'], 'http')
    require(all(isinstance(value[key], str) and re.fullmatch('[0-9a-f]{64}',value[key]) for key in
                ['set_id','artifact_id','intent_hash','accounting_dsn_sha256','process_executable_sha256']) and
            isinstance(value['process_executable'],str) and Path(value['process_executable']).is_absolute() and
            isinstance(value['process_command_prefix'],str) and Path(value['process_command_prefix']).is_absolute(),
            'RECOVERY_BANK_BINDING', 'Bank process and recovery identity pins are invalid.', 409)
    for worker_id, worker in value['workers'].items():
        require(isinstance(worker_id,str) and re.fullmatch('labbank_[0-9a-f]{20}',worker_id) and isinstance(worker,dict) and
                set(worker)=={'origin','logical_endpoint','authority_hash','identity_file_hashes','server_certificate_sha256'} and
                isinstance(worker['identity_file_hashes'],dict) and set(worker['identity_file_hashes'])==IDENTITY_FILES and
                all(isinstance(item,str) and re.fullmatch('[0-9a-f]{64}',item) for item in
                    [worker['authority_hash'],worker['server_certificate_sha256'],*worker['identity_file_hashes'].values()]),
                'RECOVERY_BANK_BINDING', 'Recovered bank routing authority has an invalid inventory.', 409)
        origin(worker['origin']); origin(worker['logical_endpoint'])
    require(all(isinstance(key,str) and re.fullmatch('[0-9a-f]{64}',key) and worker_id in value['workers'] for key,worker_id in value['authorities'].items()) and
            set(value['authorities'].values())==set(value['workers']), 'RECOVERY_BANK_BINDING', 'Recovered role authority inventory is incomplete.', 409)
    return value


def resolve(identity):
    data = identity.installation
    policy = read_document(data / 'deployment.json', {}).get('recovery', {})
    path = data / FILE
    pinned = policy.get('bank_routes_sha256')
    inactive = (data / 'recovery-inactive.json').exists()
    if pinned and not inactive:
        require(path.exists() and hashlib.sha256(private_read(path)).hexdigest() == pinned,
                'RECOVERY_BANK_BINDING', 'The activated bank routing file is missing or changed; do not fall back to a source endpoint.', 409)
    if not path.exists() and not path.is_symlink():
        require(not pinned and not inactive,
                'RECOVERY_BANK_REQUIRED', 'Recovered bank identities require explicit authenticated routes before use.', 409)
        return None
    routes = load(data)
    document = identity.document
    require(routes['authorities'].get(identity.binding) == document['worker_id'],
            'RECOVERY_BANK_BINDING', 'No recovered route is authorized for this original bank authority and role.', 409)
    worker = routes['workers'].get(document['worker_id'])
    require(worker and worker['logical_endpoint'] == document['endpoint'],
            'RECOVERY_BANK_BINDING', 'Recovered routing cannot replace a logical participant endpoint.', 409)
    return {**worker, 'set_id': routes['set_id']}


def worker_binding(data, config):
    data, config = Path(data).resolve(), Path(config).resolve()
    configuration = read_document(config)
    routes = load(data)
    worker = routes['workers'].get(configuration['worker_id'])
    require(worker and config == data / 'synthetic-banks' / configuration['worker_id'] / 'worker.json' and
            worker['authority_hash'] == authority(configuration),
            'RECOVERY_BANK_BINDING', 'Recovered bank configuration does not match its original logical authority.', 409)
    for name in ['worker.json',*IDENTITY_FILES,'bank.sqlite']:
        path = config.parent / name
        if name != 'bank.sqlite' or path.exists() or path.is_symlink(): private_materialized(data,path)
    for name, hashed in worker['identity_file_hashes'].items():
        require(hashlib.sha256(private_read(config.parent / name)).hexdigest() == hashed,
                'RECOVERY_BANK_BINDING', 'Recovered bank server identity differs from its authenticated route.', 409)
    certificate = x509.load_pem_x509_certificate(private_read(config.parent / 'server.pem'))
    require(certificate.fingerprint(hashes.SHA256()).hex() == worker['server_certificate_sha256'],
            'RECOVERY_BANK_BINDING', 'Recovered bank TLS certificate differs from its original identity.', 409)
    return routes, worker


def private_materialized(data, path):
    data, path = Path(data), Path(path)
    require(path.is_relative_to(data), 'RECOVERY_BANK_BINDING', 'Restored private bank files cannot leave their recovery directory.', 409)
    info = data.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077,
            'RECOVERY_BANK_BINDING','Restored bank roots must be private ordinary directories.',409)
    parent = data
    for part in path.relative_to(data).parts[:-1]:
        parent = parent / part
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077,
                'RECOVERY_BANK_BINDING','Restored bank parents must be private ordinary directories.',409)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077 and
            info.st_nlink==1 and info.st_size<=32_000_000,'RECOVERY_BANK_BINDING',
            'Restored bank files must be private ordinary files without symbolic or hard links.',409)


def process_command(directory, artifact_id, worker_id):
    directory = Path(directory).resolve()
    code = 'import sys;sys.path.insert(0,sys.argv.pop(1));from avenra.lab_bank import main;main()'
    return [str(directory / 'runtime/bin/python'), '-I', '-B', '-c', code,
            str(directory / 'artifacts' / artifact_id), '--config',
            str(directory / 'data/synthetic-banks' / worker_id / 'worker.json'),
            '--recovery-data', str(directory / 'data')]


def expected_command(directory, routes, worker_id):
    return ' '.join([routes['process_command_prefix'],*process_command(directory,routes['artifact_id'],worker_id)[1:]])


def register_process(data, config):
    from .recovery_native import process_identity, process_image
    data = Path(data).resolve()
    routes, worker = worker_binding(data, config)
    worker_id = read_document(config)['worker_id']
    identity = process_identity(os.getpid())
    require(identity and identity.split(' | started=')[0] == expected_command(data.parent,routes,worker_id) and
            Path(sys.prefix).resolve() == data.parent/'runtime' and
            process_image(os.getpid())==routes['process_executable'] and
            hashlib.sha256(Path(routes['process_executable']).read_bytes()).hexdigest()==routes['process_executable_sha256'],
            'RECOVERY_BANK_PROCESS', 'A recovered bank must run from its verified offline application runtime.', 409)
    record = {'format': 'avenra-bank-recovery-process/1', 'worker_id': worker_id,
              'set_id': routes['set_id'], 'routes_sha256': hashlib.sha256(private_read(data / FILE)).hexdigest(),
              'pid': os.getpid(), 'process_identity': identity, 'origin': worker['origin']}
    signed_write(data, Path(config).parent / 'recovery-process.json', record)
    return worker['origin'], routes['accounting_origin']
