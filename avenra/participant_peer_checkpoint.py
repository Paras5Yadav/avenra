"""Cold, bounded checkpoints for independent synthetic participant authorities."""
from contextlib import ExitStack, closing, contextmanager
import hashlib
from pathlib import Path
import re
import sqlite3

from .bank_recovery import private_materialized
from .errors import require
from .lab_identity import bank_service_lease, identity_lock, private_read, private_write
from .participant_peer_identity import PeerIdentity, binding
from .private_config import read_document
from .sqlite_checkpoint import checkpoint
from .util import canonical

FORMAT = 'avenra-independent-participant-checkpoint/1'
ROOTS = {'synthetic-participants', 'participant-peer-secrets'}
SERVER = {'worker.json', 'ca.pem', 'ca-key.pem', 'server.pem', 'server-key.pem', 'server-signer.raw'}
TABLES = ('requests', 'inbox', 'payment_state', 'phases', 'events')
WORKER = re.compile(r'peer_[0-9a-f]{20}')
HASH = re.compile(r'[0-9a-f]{64}')


@contextmanager
def quiescent(directory):
    """Hold the same exclusive lifetime fence as every inbox server."""
    with ExitStack() as leases:
        for config in sorted((Path(directory) / 'synthetic-participants').glob('*/worker.json')):
            leases.enter_context(bank_service_lease(config.parent))
        yield


def _counts(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        require(db.execute('PRAGMA quick_check').fetchall() == [('ok',)],
                'CHECKPOINT_INTEGRITY', 'Participant receipt database failed integrity verification.', 409)
        return {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in TABLES}


def validate(snapshot):
    """Validate the complete inventory before a restore creates any destination."""
    snapshot = Path(snapshot)
    value = read_document(snapshot / 'manifest.json')
    require(isinstance(value, dict) and set(value) == {'format', 'activation', 'workers', 'identities', 'files'} and
            value['format'] == FORMAT and value['activation'] == 'inactive' and
            isinstance(value['workers'], list) and len(value['workers']) <= 32 and
            isinstance(value['identities'], list) and len(value['identities']) == len(value['workers']) and
            isinstance(value['files'], dict) and len(value['files']) <= 400,
            'INVALID_CHECKPOINT', 'Unsupported participant checkpoint inventory or bounds.', 409)
    workers, expected = {}, set()
    for item in value['workers']:
        require(isinstance(item, dict) and set(item) == {'worker_id', 'authority_binding', 'source_origin', 'receipt_counts'} and
                isinstance(item['worker_id'], str) and WORKER.fullmatch(item['worker_id']) and item['worker_id'] not in workers and
                isinstance(item['authority_binding'], str) and HASH.fullmatch(item['authority_binding']) and
                isinstance(item['receipt_counts'], dict) and (not item['receipt_counts'] or set(item['receipt_counts']) == set(TABLES)) and
                all(type(n) is int and n >= 0 for n in item['receipt_counts'].values()),
                'INVALID_CHECKPOINT', 'Invalid participant authority or receipt inventory.', 409)
        from .bank_recovery import origin
        origin(item['source_origin'])
        workers[item['worker_id']] = item
        prefix = 'synthetic-participants/' + item['worker_id'] + '/'
        expected.update(prefix + name for name in SERVER)
        if item['receipt_counts']: expected.add(prefix + 'participant.sqlite')
    names = set()
    for item in value['identities']:
        require(isinstance(item, dict) and set(item) == {'name', 'authority_binding'} and
                isinstance(item['name'], str) and item['name'] in workers and item['name'] not in names and
                item['authority_binding'] == workers[item['name']]['authority_binding'],
                'INVALID_CHECKPOINT', 'Participant client authority inventory is incomplete or ambiguous.', 409)
        names.add(item['name'])
        expected.update('participant-peer-secrets/' + item['name'] + '/' + name for name in ['bundle.json', 'ca.pem'])
    require(names == set(workers), 'INVALID_CHECKPOINT', 'Each participant requires its captured client authority.', 409)
    for relative, hashed in value['files'].items():
        path = Path(relative)
        require(path.as_posix() == relative and len(path.parts) == 3 and path.parts[0] in ROOTS and
                path.parts[1] in workers and isinstance(hashed, str) and HASH.fullmatch(hashed) and
                (relative in expected or (path.parts[0] == 'participant-peer-secrets' and
                 re.fullmatch(r'(client_[0-9]+\.pem|tls_[0-9]+\.pem|signer_[0-9]+\.raw)', path.name))),
                'INVALID_CHECKPOINT', 'Participant checkpoint contains an undeclared private path.', 409)
        private_materialized(snapshot, snapshot / path)
        require(hashlib.sha256(private_read(snapshot / path, 32_000_000)).hexdigest() == hashed,
                'CHECKPOINT_INTEGRITY', 'Participant checkpoint differs from its retained file inventory.', 409)
    require(expected <= set(value['files']), 'INVALID_CHECKPOINT', 'Participant checkpoint is missing required durable files.', 409)
    actual = {path.relative_to(snapshot).as_posix() for path in snapshot.rglob('*') if not path.is_dir()}
    require(actual == set(value['files']) | {'manifest.json'} and not any(path.is_symlink() for path in snapshot.rglob('*')),
            'INVALID_CHECKPOINT', 'Participant checkpoint contains extra files or symbolic links.', 409)
    for worker_id, item in workers.items():
        identity = PeerIdentity(snapshot, 'secret://' + worker_id)
        config = read_document(snapshot / 'synthetic-participants' / worker_id / 'worker.json')
        require(config['worker_id'] == worker_id and binding(config) == identity.binding == item['authority_binding'],
                'CHECKPOINT_INTEGRITY', 'Participant worker and client authority differ.', 409)
        if item['receipt_counts']:
            require(_counts(snapshot / 'synthetic-participants' / worker_id / 'participant.sqlite') == item['receipt_counts'],
                    'CHECKPOINT_INTEGRITY', 'Participant receipt counts differ from the captured inventory.', 409)
    return value


def snapshot(directory, destination):
    """Caller holds quiescent() and the deployment write fences throughout."""
    directory, destination = Path(directory), Path(destination)
    require(not destination.exists() and not destination.is_symlink(), 'CHECKPOINT_EXISTS', 'Preserve existing participant checkpoints.', 409)
    configs = sorted((directory / 'synthetic-participants').glob('*/worker.json'))
    require(len(configs) <= 32, 'CHECKPOINT_CAPACITY', 'Participant checkpoint supports at most 32 authorities.', 409)
    destination.mkdir(mode=0o700)
    value = {'format': FORMAT, 'activation': 'inactive', 'workers': [], 'identities': [], 'files': {}}
    for config in configs:
        worker_id = config.parent.name
        require(WORKER.fullmatch(worker_id), 'INVALID_CHECKPOINT', 'Invalid participant worker directory.', 409)
        with identity_lock(config.parent):
            identity = PeerIdentity(directory, 'secret://' + worker_id)
            configuration = read_document(config)
            from .peer_recovery import resolve
            route = resolve(identity)
            target = destination / 'synthetic-participants' / worker_id
            target.parent.mkdir(mode=0o700, exist_ok=True); target.mkdir(mode=0o700)
            for name in SERVER:
                private_materialized(directory, config.parent / name)
                private_write(target / name, private_read(config.parent / name))
            counts = {}
            database = config.parent / 'participant.sqlite'
            if database.exists() or database.is_symlink():
                private_materialized(directory, database)
                counts = checkpoint(database, target / 'participant.sqlite', TABLES)
            value['workers'].append({'worker_id': worker_id, 'authority_binding': identity.binding,
                                     'source_origin': route['origin'] if route else configuration['endpoint'], 'receipt_counts': counts})
            secret = destination / 'participant-peer-secrets' / worker_id
            secret.parent.mkdir(mode=0o700, exist_ok=True); secret.mkdir(mode=0o700)
            for path in identity.directory.iterdir():
                require(path.name in {'bundle.json', 'ca.pem'} or re.fullmatch(r'(client_[0-9]+\.pem|tls_[0-9]+\.pem|signer_[0-9]+\.raw)', path.name),
                        'INVALID_CHECKPOINT', 'Unexpected participant secret file; do not silently omit it.', 409)
                private_materialized(directory, path)
                private_write(secret / path.name, private_read(path))
            value['identities'].append({'name': worker_id, 'authority_binding': identity.binding})
    for path in destination.rglob('*'):
        if path.is_file(): value['files'][path.relative_to(destination).as_posix()] = hashlib.sha256(private_read(path, 32_000_000)).hexdigest()
    require(len(canonical(value).encode()) < 60_000, 'CHECKPOINT_CAPACITY', 'Participant checkpoint exceeds its bounded inventory.', 409)
    private_write(destination / 'manifest.json', canonical(value))
    validate(destination)
    return value


def restore(snapshot, directory):
    snapshot, directory = Path(snapshot), Path(directory)
    value = validate(snapshot)
    for root in ROOTS:
        require(not (directory / root).exists() and not (directory / root).is_symlink(),
                'CHECKPOINT_EXISTS', 'Restore participants only into unused inactive roots.', 409)
    for root in ROOTS: (directory / root).mkdir(mode=0o700)
    for relative in value['files']:
        target = directory / relative
        target.parent.mkdir(mode=0o700, exist_ok=True)
        private_write(target, private_read(snapshot / relative, 32_000_000))
    return {'valid': True, 'workers': len(value['workers']), 'identities': len(value['identities']),
            'activation': 'inactive', 'scope': 'Captured synthetic participant keys and durable receipts; no external acceptance.'}
