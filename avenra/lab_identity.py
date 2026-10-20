"""Private test PKI and rotating synthetic identities; no official credentials."""
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager, closing
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import shutil
import sqlite3

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .errors import require
from .util import canonical, digest, uid

TLS_LAB = 'avenra-tls-synthetic-bank/1'
CONTRACT = {
    'id': 'avenra-synthetic-bank-leg/1', 'authority': 'synthetic', 'currency': 'INR/2',
    'roles': ['remitter', 'beneficiary'], 'transport': 'mutual_tls', 'signatures': 'ed25519',
    'operations': ['probe', 'status', 'post'], 'request_lifetime_seconds': 30,
    'rules': ['durable_financial_identity', 'exact_postings', 'bank_account_scope',
              'signed_request_response_binding', 'unknown_is_not_declined', 'credential_rotation', 'revocation'],
}
CONTRACT_HASH = digest(CONTRACT)


def private_write(path, value):
    path = Path(path)
    data = value.encode() if isinstance(value, str) else value
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def private_read(path, maximum=65536):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode) and not info.st_mode & 0o077 and info.st_uid == os.getuid(), 'PRIVATE_IDENTITY_REQUIRED', 'Synthetic identity files require private ownership and permissions.', 409)
        data = handle.read(maximum + 1)
    require(len(data) <= maximum, 'IDENTITY_TOO_LARGE', 'Identity data exceeds the supported limit.')
    return data


def replace_private(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uid('swap'))
    private_write(temporary, value)
    os.replace(temporary, path)


def _private(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _signing_public(key):
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def _certificate(name, key, issuer, issuer_key, client=False, ca=False):
    current = datetime.now(timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Avenra Synthetic Lab'), x509.NameAttribute(NameOID.COMMON_NAME, name)])
    builder = x509.CertificateBuilder().subject_name(subject).issuer_name(issuer.subject if issuer else subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(current - timedelta(minutes=1)).not_valid_after(current + timedelta(days=7))
    builder = builder.add_extension(x509.BasicConstraints(ca=ca, path_length=0 if ca else None), critical=True)
    builder = builder.add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    builder = builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key((issuer_key or key).public_key()), critical=False)
    builder = builder.add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False, key_cert_sign=ca, crl_sign=ca, encipher_only=False, decipher_only=False), critical=True)
    if not ca:
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH if client else ExtendedKeyUsageOID.SERVER_AUTH]), critical=True)
        if not client:
            builder = builder.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
    return builder.sign(issuer_key or key, hashes.SHA256())


def binding(document):
    keys = ['worker_id', 'bank_id', 'tenant_id', 'environment_id', 'participant_id', 'role', 'ledger_name', 'endpoint', 'server_signer_public', 'ca_sha256', 'transport_hash']
    return digest({k: document[k] for k in keys})


class LabIdentity:
    def __init__(self, directory, secret_reference):
        require(isinstance(secret_reference, str) and re.fullmatch(r'secret://[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', secret_reference), 'INVALID_SECRET_REFERENCE', 'Select a private synthetic identity reference.')
        self.installation = Path(directory).resolve()
        root = self.installation / 'participant-secrets'
        name = secret_reference.removeprefix('secret://')
        self.directory = root / name
        require(not root.is_symlink() and not self.directory.is_symlink(), 'PRIVATE_IDENTITY_REQUIRED', 'Identity directories cannot be symbolic links.', 409)
        for p in [root, self.directory]:
            info = p.stat()
            require(info.st_uid == os.getuid() and not info.st_mode & 0o077, 'PRIVATE_IDENTITY_REQUIRED', 'Identity directories require private ownership and permissions.', 409)
        self.document = json.loads(private_read(self.directory / 'bundle.json'))
        require(self.document['transport_hash'] == CONTRACT_HASH, 'PROFILE_UNSUPPORTED', 'Synthetic credential contract is unsupported.', 409)
        self.binding = binding(self.document)
        self.ca = self.directory / 'ca.pem'
        require(hashlib.sha256(private_read(self.ca)).hexdigest() == self.document['ca_sha256'], 'IDENTITY_CONFLICT', 'The pinned test trust root changed.', 409)
        for field in ['client_cert', 'client_key', 'signer_key']:
            require(re.fullmatch(r'[a-z]+_[0-9]+\.(pem|raw)', self.document[field]), 'INVALID_SECRET_REFERENCE', 'Invalid versioned identity filename.')
            private_read(self.directory / self.document[field])
        self.signer = ed25519.Ed25519PrivateKey.from_private_bytes(private_read(self.directory / self.document['signer_key']))
        require(_signing_public(self.signer) == self.document['client_signer_public'], 'IDENTITY_CONFLICT', 'The client signing identity does not match its public key.', 409)


def provision_bank(app, tenant, env, bank_id, port):
    """Provision one trusted-code test bank and one client identity per bank role."""
    require(type(port) is int and 1024 <= port <= 65535, 'INVALID_PORT', 'Use a nonprivileged loopback port.')
    app.environment(tenant, env, True)
    with app.store.reader() as db:
        bank = app.banking._get(db, 'bank_institutions', tenant, env, bank_id)
        actors = {x['role']: dict(x) for x in db.execute("SELECT * FROM upi_participants WHERE tenant_id=? AND environment_id=? AND bank_id=? AND role IN ('remitter','beneficiary')", (tenant, env, bank_id))}
        addresses = [x[0] for x in db.execute('SELECT a.address FROM accounts a JOIN bank_accounts b ON b.account_id=a.id WHERE b.tenant_id=? AND b.environment_id=? AND b.bank_id=?', (tenant, env, bank_id))]
    require(set(actors) == {'remitter', 'beneficiary'} and addresses, 'TOPOLOGY_INCOMPLETE', 'Install both bank actors and funded accounts first.', 409)
    identity = uid('labbank')
    workers = app.store.directory / 'synthetic-banks'
    workers.mkdir(mode=0o700, exist_ok=True)
    worker = workers / identity
    worker.mkdir(mode=0o700)
    secrets_root = app.store.directory / 'participant-secrets'
    secrets_root.mkdir(mode=0o700, exist_ok=True)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _certificate('Avenra test CA ' + identity, ca_key, None, None, ca=True)
    server_key = ec.generate_private_key(ec.SECP256R1())
    server_cert = _certificate(identity, server_key, ca, ca_key)
    server_signer = ed25519.Ed25519PrivateKey.generate()
    ca_pem = ca.public_bytes(serialization.Encoding.PEM)
    for name, data in [('ca.pem', ca_pem), ('ca-key.pem', _private(ca_key)), ('server.pem', server_cert.public_bytes(serialization.Encoding.PEM)), ('server-key.pem', _private(server_key)), ('server-signer.raw', server_signer.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))]:
        private_write(worker / name, data)
    configuration = {'worker_id': identity, 'bank_id': bank_id, 'bank_name': bank['name'], 'tenant_id': tenant, 'environment_id': env,
                     'ledger_name': app.environment(tenant, env)['ledger_name'], 'endpoint': f'https://127.0.0.1:{port}',
                     'transport_hash': CONTRACT_HASH, 'server_signer_public': _signing_public(server_signer), 'ca_sha256': hashlib.sha256(ca_pem).hexdigest(),
                     'account_addresses': addresses, 'accounting_origin': app.accounting.http.origin, 'clients': {}}
    connections = []
    for role, actor in actors.items():
        name = identity + '_' + role
        secret = secrets_root / name
        secret.mkdir(mode=0o700)
        document = {k: configuration[k] for k in ['worker_id', 'bank_id', 'tenant_id', 'environment_id', 'ledger_name', 'endpoint', 'server_signer_public', 'ca_sha256', 'transport_hash']}
        document.update(participant_id=actor['id'], role=role)
        private_write(secret / 'ca.pem', ca_pem)
        _issue_client(secret, worker, configuration, document, 1)
        private_write(secret / 'bundle.json', canonical(document))
        connections.append({'participant_id': actor['id'], 'role': role, 'secret_reference': 'secret://' + name, 'endpoint': configuration['endpoint']})
    private_write(worker / 'worker.json', canonical(configuration))
    return {'worker_id': identity, 'directory': str(worker), 'config': str(worker / 'worker.json'), 'connections': connections, 'endpoint': configuration['endpoint']}


def _issue_client(secret, worker, configuration, document, epoch):
    ca = x509.load_pem_x509_certificate(private_read(worker / 'ca.pem'))
    ca_key = serialization.load_pem_private_key(private_read(worker / 'ca-key.pem'), password=None)
    tls_key = ec.generate_private_key(ec.SECP256R1())
    cert = _certificate(document['participant_id'] + ' epoch ' + str(epoch), tls_key, ca, ca_key, client=True)
    signer = ed25519.Ed25519PrivateKey.generate()
    key_id = uid('clientkey')
    document.update(identity_epoch=epoch, client_key_id=key_id, client_signer_public=_signing_public(signer), client_cert=f'client_{epoch}.pem', client_key=f'tls_{epoch}.pem', signer_key=f'signer_{epoch}.raw')
    private_write(secret / document['client_cert'], cert.public_bytes(serialization.Encoding.PEM))
    private_write(secret / document['client_key'], _private(tls_key))
    private_write(secret / document['signer_key'], signer.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))
    configuration['clients'][key_id] = {'participant_id': document['participant_id'], 'role': document['role'], 'public_key': document['client_signer_public'], 'revoked': False}


@contextmanager
def identity_lock(worker):
    fd = os.open(worker / 'identity.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def rotate_client(directory, secret_reference):
    identity = LabIdentity(directory, secret_reference)
    worker = Path(directory).resolve() / 'synthetic-banks' / identity.document['worker_id']
    with identity_lock(worker):
        identity = LabIdentity(directory, secret_reference)
        configuration = json.loads(private_read(worker / 'worker.json'))
        document = dict(identity.document)
        _issue_client(identity.directory, worker, configuration, document, document['identity_epoch'] + 1)
        # Publish new trust before selecting the new client key. Financial intent
        # and bank authority binding remain stable across credential epochs.
        replace_private(worker / 'worker.json', canonical(configuration))
        replace_private(identity.directory / 'bundle.json', canonical(document))
    return {'previous_key_id': identity.document['client_key_id'], 'current_key_id': document['client_key_id'], 'identity_epoch': document['identity_epoch']}


def revoke_client(directory, secret_reference, key_id):
    identity = LabIdentity(directory, secret_reference)
    path = Path(directory).resolve() / 'synthetic-banks' / identity.document['worker_id'] / 'worker.json'
    with identity_lock(path.parent):
        configuration = json.loads(private_read(path))
        require(key_id in configuration['clients'] and configuration['clients'][key_id]['participant_id'] == identity.document['participant_id'], 'NOT_FOUND', 'Signing key does not belong to this participant.', 404)
        configuration['clients'][key_id]['revoked'] = True
        replace_private(path, canonical(configuration))


@contextmanager
def bank_service_lease(worker):
    """Fence a cooperating bank process against a cold recovery capture.

    The lease covers the complete process lifetime, not only a SQLite write.
    It also prevents two bank processes from sharing one receipt directory.
    """
    worker = Path(worker)
    require(worker.is_dir() and not worker.is_symlink(), 'PRIVATE_IDENTITY_REQUIRED', 'Bank service directory must be ordinary and private.', 409)
    info = worker.stat()
    require(info.st_uid == os.getuid() and not info.st_mode & 0o077, 'PRIVATE_IDENTITY_REQUIRED', 'Bank service directory requires private ownership and permissions.', 409)
    fd = os.open(worker / 'service.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077, 'PRIVATE_IDENTITY_REQUIRED', 'Bank service lease requires private ownership and permissions.', 409)
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            require(False, 'RECOVERY_BANK_ACTIVE', 'Stop the synthetic bank process before cold capture or another start.', 409)
        yield


@contextmanager
def quiescent_banks(directory):
    from contextlib import ExitStack
    with ExitStack() as leases:
        for config in sorted((Path(directory) / 'synthetic-banks').glob('*/worker.json')):
            leases.enter_context(bank_service_lease(config.parent))
        yield


def snapshot_private_banks(directory, destination):
    """Snapshot test identities and consistent local bank receipt databases.

    The caller quiesces the console. This is a local component checkpoint, not
    off-host retention or a distributed financial disaster-recovery claim.
    """
    directory, destination = Path(directory), Path(destination)
    require(not destination.exists() and not destination.is_symlink(), 'CHECKPOINT_EXISTS', 'Preserve existing bank checkpoints.', 409)
    destination.mkdir(mode=0o700, parents=True)
    manifest = {'format': 'avenra-synthetic-bank-recovery/2', 'activation': 'inactive', 'workers': [], 'identities': [], 'files': {}}
    for config in sorted((directory/'synthetic-banks').glob('*/worker.json')):
        worker = config.parent
        with identity_lock(worker):
            configuration = json.loads(private_read(config))
            target = destination/'synthetic-banks'/worker.name
            target.mkdir(mode=0o700, parents=True)
            for name in ['worker.json','ca.pem','ca-key.pem','server.pem','server-key.pem','server-signer.raw']:
                private_write(target/name, private_read(worker/name))
            counts = {}
            if (worker/'bank.sqlite').exists():
                from .sqlite_checkpoint import checkpoint
                counts = checkpoint(worker/'bank.sqlite', target/'bank.sqlite', ['requests','effects','events'])
            manifest['workers'].append({'directory':worker.name,'worker_id':configuration['worker_id'],'receipt_counts':counts})
            for bundle in sorted((directory/'participant-secrets').glob('*/bundle.json')):
                document = json.loads(private_read(bundle))
                if document['worker_id'] != configuration['worker_id']:continue
                secret = destination/'participant-secrets'/bundle.parent.name
                secret.mkdir(mode=0o700,parents=True)
                for path in bundle.parent.iterdir():
                    if path.name in {'bundle.json','ca.pem'} or re.fullmatch(r'[a-z]+_[0-9]+\.(pem|raw)',path.name):
                        private_write(secret/path.name,private_read(path))
                manifest['identities'].append({'name':bundle.parent.name,'authority_binding':binding(document)})
    # mkdir(parents=True) may inherit a permissive umask for intermediate nodes.
    for path in destination.rglob('*'):
        require(not path.is_symlink() and not path.name.endswith(('-wal', '-shm', '-journal')), 'CHECKPOINT_SIDECAR', 'Completed bank checkpoints contain durable ordinary files only.', 409)
        if path.is_dir():path.chmod(0o700)
        elif path.is_file():
            path.chmod(0o600)
            with path.open('rb') as handle:manifest['files'][str(path.relative_to(destination))]=hashlib.file_digest(handle,'sha256').hexdigest()
    private_write(destination/'manifest.json',canonical(manifest))
    return manifest


def restore_private_banks(snapshot, directory):
    snapshot, directory = Path(snapshot), Path(directory)
    manifest = json.loads(private_read(snapshot/'manifest.json'))
    require(isinstance(manifest, dict) and manifest.get('format') in {None, 'avenra-synthetic-bank-recovery/2'} and isinstance(manifest.get('files'), dict) and len(manifest['files']) <= 2000,
            'INVALID_CHECKPOINT', 'Unsupported bank checkpoint inventory.', 409)
    require(isinstance(manifest.get('workers'), list) and len(manifest['workers']) <= 100 and isinstance(manifest.get('identities'), list) and len(manifest['identities']) <= 400,
            'INVALID_CHECKPOINT', 'Bank checkpoint participant inventory exceeds its supported bounds.', 409)
    for item in manifest['workers']:
        require(isinstance(item, dict) and re.fullmatch('[A-Za-z0-9_-]{1,120}', item.get('directory', '')) and isinstance(item.get('receipt_counts'), dict) and
                set(item['receipt_counts']) <= {'requests', 'effects', 'events'} and all(type(n) is int and n >= 0 for n in item['receipt_counts'].values()),
                'INVALID_CHECKPOINT', 'Invalid bank receipt inventory.', 409)
        require(not item['receipt_counts'] or 'synthetic-banks/' + item['directory'] + '/bank.sqlite' in manifest['files'], 'INVALID_CHECKPOINT', 'Bank receipt database is missing from the inventory.', 409)
    for item in manifest['identities']:
        require(isinstance(item, dict) and re.fullmatch('[A-Za-z0-9_.-]{1,120}', item.get('name', '')) and item['name'] not in {'.', '..'} and
                isinstance(item.get('authority_binding'), str) and re.fullmatch('[0-9a-f]{64}', item['authority_binding']), 'INVALID_CHECKPOINT', 'Invalid bank identity inventory.', 409)
    for name in ['synthetic-banks','participant-secrets']:
        require(not (directory/name).exists() and not (directory/name).is_symlink(), 'CHECKPOINT_EXISTS', 'Restore private banks into an unused inactive directory.', 409)
    # Verify the complete inventory before creating any destination files.
    for relative, expected in manifest['files'].items():
        path = Path(relative)
        require(not path.is_absolute() and path.as_posix() == relative and len(path.parts) == 3 and '..' not in path.parts and path.parts[0] in {'synthetic-banks','participant-secrets'} and
                not path.name.endswith(('-wal', '-shm', '-journal')), 'INVALID_CHECKPOINT', 'Bank checkpoint path is outside its durable recovery inventory.', 409)
        source = snapshot/path
        require(all(not p.is_symlink() for p in [snapshot, *source.parents[:len(path.parts)-1]]) and not source.is_symlink(), 'INVALID_CHECKPOINT', 'Bank checkpoint inputs cannot contain links.', 409)
        info = source.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077 and info.st_size <= 32_000_000, 'INVALID_CHECKPOINT', 'Bank checkpoint files require bounded private ownership and permissions.', 409)
        with source.open('rb') as handle:
            require(hashlib.file_digest(handle, 'sha256').hexdigest() == expected, 'CHECKPOINT_INTEGRITY', 'Bank checkpoint differs from its retained inventory.', 409)
    # Create intermediate roots explicitly; pathlib's parents=True applies its
    # requested mode only to the final directory, not newly created parents.
    for name in ['synthetic-banks', 'participant-secrets']:
        (directory/name).mkdir(mode=0o700, parents=True)
    for relative in manifest['files']:
        target = directory/relative;target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_write(target, (snapshot/relative).read_bytes())
    valid = True
    for relative, expected in manifest['files'].items():
        target = directory/relative
        require(target.resolve().is_relative_to(directory.resolve()),'INVALID_CHECKPOINT','Synthetic checkpoint path is outside its destination.')
        with target.open('rb') as handle:valid &= hashlib.file_digest(handle,'sha256').hexdigest()==expected
        valid &= not bool(target.stat().st_mode&0o077)
    for identity in manifest['identities']:
        valid &= LabIdentity(directory,'secret://'+identity['name']).binding==identity['authority_binding']
    for worker in manifest['workers']:
        if worker['receipt_counts']:
            with closing(sqlite3.connect((directory/'synthetic-banks'/worker['directory']/'bank.sqlite').resolve().as_uri()+'?mode=ro&immutable=1', uri=True)) as db:
                valid &= all(db.execute('SELECT count(*) FROM '+table).fetchone()[0]==count for table,count in worker['receipt_counts'].items())
    return {'valid':bool(valid),'workers':len(manifest['workers']),'identities':len(manifest['identities']),'activation':'inactive','scope':'Restored private test identities and bank receipt files. No external bank, HSM or off-host infrastructure tested.'}
