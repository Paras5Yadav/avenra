"""Externally authenticated, cold recovery sets for managed synthetic deployments.

No source database is restored in place. Native writers and managed application
nodes must be stopped before capture. Staging never activates copied identities.
"""
from contextlib import contextmanager, ExitStack
import base64
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import release_bundle as bundle
from .errors import DomainError, require
from .lab_identity import private_read, private_write
from .private_config import read_document, write_document
from .recovery_state import INACTIVE, require_active
from .util import canonical, digest

FORMAT = 'avenra-coordinated-recovery/1'
MANIFEST, SIGNATURE = 'recovery-manifest.json', 'recovery-manifest.sig'
MAX_FILES, MAX_BYTES, MAX_FILE = 10000, 20 * 1024**3, 2 * 1024**3


def private_root(path):
    path = Path(path)
    require(path.is_dir() and not path.is_symlink(), 'RECOVERY_PATH', 'Select an ordinary private recovery directory.', 409)
    info = path.stat()
    require(info.st_uid == os.getuid() and not info.st_mode & 0o077, 'RECOVERY_PATH', 'Recovery directories require private ownership and permissions.', 409)
    return path


def regular(path, maximum=MAX_FILE):
    info = Path(path).lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077 and info.st_size <= maximum,
            'RECOVERY_FILE', 'Recovery files must be bounded private regular files, without links.', 409)
    return info


def copy_private(source, destination):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    before = source.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_FILE, 'RECOVERY_FILE', 'Recovery inputs must be bounded regular files.', 409)
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    target = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'rb') as original, os.fdopen(target, 'wb') as copied:
        opened = os.fstat(original.fileno())
        require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino), 'RECOVERY_CHANGED', 'Recovery input changed while opening.', 409)
        shutil.copyfileobj(original, copied);copied.flush();os.fsync(copied.fileno())
        after = os.fstat(original.fileno())
    require((opened.st_size, opened.st_mtime_ns) == (after.st_size, after.st_mtime_ns), 'RECOVERY_CHANGED', 'Recovery input changed during capture.', 409)


def copy_tree(source, destination):
    source, destination = Path(source), Path(destination)
    require(source.is_dir() and not source.is_symlink(), 'RECOVERY_PATH', 'Recovery trees cannot be links.', 409)
    destination.mkdir(mode=0o700, parents=True)
    for item in sorted(source.rglob('*')):
        require(not item.is_symlink(), 'RECOVERY_PATH', 'Recovery trees cannot contain links.', 409)
        target = destination / item.relative_to(source)
        if item.is_dir(): target.mkdir(mode=0o700, exist_ok=True)
        else: copy_private(item, target)


def inventory(root):
    root = private_root(root);entries = [];total = 0
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(), 'RECOVERY_PATH', 'Recovery sets cannot contain links.', 409)
        if path.is_dir(): private_root(path);continue
        relative = path.relative_to(root).as_posix()
        if relative in {MANIFEST, SIGNATURE}: continue
        info = regular(path);total += info.st_size
        entries.append({'path': relative, 'bytes': info.st_size, 'sha256': bundle.sha(path)})
        require(len(entries) <= MAX_FILES and total <= MAX_BYTES, 'RECOVERY_CAPACITY', 'Recovery set exceeds its declared file/byte budget.', 409)
    return entries


def database_inventory(db):
    """Stable full-row comparison, independent of a dump/restore success flag."""
    from psycopg import sql
    tables = db.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') AND schemaname NOT LIKE 'pg_toast%' ORDER BY schemaname,tablename").fetchall()
    require(len(tables) <= 4096, 'RECOVERY_CAPACITY', 'Database table inventory exceeds this recovery profile.', 409)
    result = []
    for schema, table in tables:
        hashed, count = hashlib.sha256(), 0
        query = sql.SQL('SELECT row_to_json(t)::text AS value FROM {}.{} t ORDER BY row_to_json(t)::text COLLATE "C"').format(sql.Identifier(schema), sql.Identifier(table))
        # A server cursor bounds client memory even for a large retained table.
        with db.cursor(name='recovery_rows') as cursor:
            cursor.execute(query)
            for value, in cursor:
                count += 1;require(count <= 2_000_000, 'RECOVERY_CAPACITY', 'A retained table exceeds this recovery profile.', 409)
                hashed.update(value.encode() + b'\n')
        result.append({'schema': schema, 'table': table, 'rows': count, 'sha256': hashed.hexdigest()})
    sequences = db.execute("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='S' AND n.nspname NOT IN ('pg_catalog','information_schema') ORDER BY n.nspname,c.relname").fetchall()
    for schema, name in sequences:
        last, called = db.execute(sql.SQL('SELECT last_value,is_called FROM {}.{}').format(sql.Identifier(schema),sql.Identifier(name))).fetchone()
        result.append({'schema':schema,'sequence':name,'last_value':last,'is_called':called})
    return result


@contextmanager
def pg_connection(dsn, directory):
    """Keep passwords out of argv, stdout and retained command diagnostics."""
    from psycopg.conninfo import conninfo_to_dict
    info = conninfo_to_dict(dsn);path = Path(directory) / ('.pgpass-' + os.urandom(12).hex())
    def escape(value): return str(value).replace('\\', '\\\\').replace(':', '\\:')
    private_write(path, ':'.join(escape(info.get(k, '')) for k in ['host', 'port', 'dbname', 'user', 'password']) + '\n')
    environment = os.environ.copy();environment['PGPASSFILE'] = str(path)
    for key in ['PGPASSWORD', 'PGSERVICE', 'PGSERVICEFILE']: environment.pop(key, None)
    arguments = ['--host=' + info.get('host', '127.0.0.1'), '--port=' + info.get('port', '5432'), '--username=' + info['user'], '--dbname=' + info['dbname'], '--no-password']
    try: yield arguments, environment
    finally: path.unlink(missing_ok=True)


def pg_command(command, dsn, directory, log_name, arguments):
    with pg_connection(dsn, directory) as (connection, environment):
        log = Path(directory) / log_name
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'ab') as output:
            child = subprocess.run([str(command), *connection, *arguments], env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, timeout=300)
    require(child.returncode == 0, 'RECOVERY_DATABASE', 'Database checkpoint command failed; inspect its private attempt log.', 409)


def capture(data, destination, artifact_directory, release_trust, signing_key, accounting_dsn_file, native_directory, pg_bin, request_id, actor):
    import psycopg
    from psycopg import sql
    from .release_runtime import installation_lock, migration_connection, schema_versions, validate_request, LOCK
    from .deployment_backup import snapshot as snapshot_deployment
    from .aa.recovery import snapshot as snapshot_roles
    from .lab_identity import snapshot_private_banks, quiescent_banks
    data = private_root(Path(data).resolve());require_active(data);destination = Path(destination)
    regular(data / 'evidence.key',32)
    validate_request(request_id, None, actor, 'activate');private_root(destination.parent)
    require(not destination.exists() and not destination.is_symlink(), 'RECOVERY_EXISTS', 'Preserve previous recovery sets; select a new destination.', 409)
    artifact = bundle.verify_installed(artifact_directory, release_trust)
    require(any(entry['path']=='avenra/recovery_state.py' for entry in artifact['manifest']['files']), 'RECOVERY_RELEASE', 'Coordinated recovery requires an application release with enforced inactive startup guards.', 409)
    accounting_dsn = private_read(accounting_dsn_file).decode().strip()
    started = time.time()
    # The directory is deliberately absent on admission refusal.
    with installation_lock(data):
        require(not read_document(data / 'release-pending.json'), 'RELEASE_RECOVERY_REQUIRED', 'Recover a pending release before a coordinated capture.', 409)
        db, _ = migration_connection(data)
        with db, ExitStack() as leases:
            require(db.execute('SELECT pg_try_advisory_lock(%s)', (LOCK,)).fetchone()[0], 'RELEASE_NODES_ACTIVE', 'Drain and stop managed API/worker nodes before capture.', 409)
            require(db.execute("SELECT to_regclass('release_active')").fetchone()[0], 'RECOVERY_RELEASE', 'Select a verified managed release before coordinated capture.', 409)
            selected = db.execute('SELECT artifact_id,application_fingerprint FROM release_active WHERE singleton=1').fetchone()
            require(selected == (artifact['artifact_id'], artifact['manifest']['application_fingerprint']), 'RECOVERY_RELEASE', 'Capture the actual selected release, not an unrelated staged artifact.', 409)
            schema_versions(db, artifact['manifest']['schemas'])
            from .participant_capabilities import ensure
            ensure(db,artifact)
            with psycopg.connect(accounting_dsn, autocommit=True, connect_timeout=5) as accounting:
                others = accounting.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND backend_type='client backend' AND pid<>pg_backend_pid()").fetchone()[0]
                require(others == 0, 'RECOVERY_ACCOUNTING_ACTIVE', 'Stop the declared native accounting service and its clients before cold capture.', 409)
                require(accounting.execute('SHOW timezone').fetchone()[0] == 'UTC', 'RECOVERY_TIMEZONE', 'Native accounting recovery requires the qualified UTC policy.', 409)
                leases.enter_context(quiescent_banks(data))
                from .aa.runtime import quiescent as quiescent_aa
                leases.enter_context(quiescent_aa(data))
                from .participant_peer_checkpoint import quiescent as quiescent_peers
                leases.enter_context(quiescent_peers(data))
                destination.mkdir(mode=0o700)
                try:
                    databases = {};settings = {}
                    with db.transaction(), accounting.transaction():
                        for role, connection, dsn in [('control', db, private_read(data / 'migration.dsn').decode().strip()), ('accounting', accounting, accounting_dsn)]:
                            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                            connection.execute("SET LOCAL timezone='UTC'");connection.execute("SET LOCAL lock_timeout='2s'")
                            tables = connection.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') AND schemaname NOT LIKE 'pg_toast%' ORDER BY schemaname,tablename").fetchall()
                            for schema, table in tables:
                                connection.execute(sql.SQL('LOCK TABLE {}.{} IN SHARE MODE NOWAIT').format(sql.Identifier(schema), sql.Identifier(table)))
                            snapshot = connection.execute('SELECT pg_export_snapshot()').fetchone()[0]
                            settings[role] = {'timezone': connection.execute('SHOW timezone').fetchone()[0]}
                            databases[role] = database_inventory(connection)
                            target = destination / 'databases' / (role + '.dump');target.parent.mkdir(mode=0o700, exist_ok=True)
                            pg_command(Path(pg_bin) / 'pg_dump', dsn, destination, role + '-capture.log', ['--format=custom', '--no-owner', '--no-privileges', '--snapshot=' + snapshot, '--file=' + str(target)])
                            target.chmod(0o600)
                        snapshot_deployment(data, destination / 'private-deployment')
                        snapshot_roles(data, destination / 'financial-data')
                        snapshot_private_banks(data, destination / 'synthetic-banks')
                        from .participant_peer_checkpoint import snapshot as snapshot_peers
                        snapshot_peers(data, destination / 'independent-participants')
                        for name in ['evidence.key', 'services.json', 'identity.json', 'product-credentials.json']:
                            if (data / name).exists():
                                regular(data / name);copy_private(data / name, destination / 'private' / name)
                    copy_tree(artifact['directory'], destination / 'application')
                    for role in ['accounting', 'fault']:
                        binary = Path(native_directory) / role
                        require(bundle.sha(binary) == artifact['manifest']['native_services'].get(role, {}).get('sha256'), 'RECOVERY_NATIVE', 'Native binary differs from the selected release dependency pin.', 409)
                        copy_private(binary, destination / 'native' / role)
                    key = Ed25519PrivateKey.from_private_bytes(private_read(signing_key))
                    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                    manifest = {'format': FORMAT, 'activation': 'inactive', 'request_id': request_id, 'actor': actor, 'created_at': time.time(),
                                'capture_seconds': time.time() - started, 'artifact_id': artifact['artifact_id'], 'application_fingerprint': artifact['manifest']['application_fingerprint'],
                                'target': bundle.runtime_target(), 'interpreter_sha256': bundle.sha(Path(sys.executable).resolve()),
                                'signing_key_id': hashlib.sha256(public).hexdigest(), 'database_settings': settings, 'databases': databases, 'files': inventory(destination),
                                'consistency': 'Cold cooperating application/native deployment; control release fence, native clients absent, table write locks and exported per-database snapshots. Other service releases, cluster globals, broker replay and off-host delivery are not included.'}
                    raw = canonical(manifest).encode();private_write(destination / MANIFEST, raw);private_write(destination / SIGNATURE, base64.b64encode(key.sign(raw)))
                    return {'set_id': hashlib.sha256(raw).hexdigest(), 'directory': str(destination.resolve()), 'files': len(manifest['files']), 'artifact_id': artifact['artifact_id'], 'capture_seconds': manifest['capture_seconds'], 'activation': 'inactive'}
                except BaseException:
                    private_write(destination / 'CAPTURE_FAILED', 'Incomplete capture; do not restore.\n');raise


def verify(directory, recovery_trust, release_trust):
    directory = private_root(directory);raw = private_read(directory / MANIFEST, 4_000_000)
    value = bundle.parse(raw);trusted = private_read(recovery_trust)
    try:
        signature = base64.b64decode(private_read(directory / SIGNATURE), validate=True)
        Ed25519PublicKey.from_public_bytes(trusted).verify(signature, raw)
    except (InvalidSignature, ValueError): raise DomainError('RECOVERY_SIGNATURE', 'Recovery set failed verification against the external trust root.', 409)
    required = {'format','activation','request_id','actor','created_at','capture_seconds','artifact_id','application_fingerprint','target','interpreter_sha256','signing_key_id','database_settings','databases','files','consistency'}
    require(isinstance(value, dict) and set(value) == required and canonical(value).encode() == raw and value['format'] == FORMAT and value['activation'] == 'inactive' and value['signing_key_id'] == hashlib.sha256(trusted).hexdigest(), 'RECOVERY_FORMAT', 'Unsupported or noncanonical recovery set.', 409)
    require(value['target'] == bundle.runtime_target() and value['interpreter_sha256'] == bundle.sha(Path(sys.executable).resolve()), 'RECOVERY_TARGET', 'Recovery requires its pinned compatible base interpreter and platform.', 409)
    require(value['files'] == inventory(directory), 'RECOVERY_INTEGRITY', 'Recovery inventory is incomplete, changed or contains extra files.', 409)
    # Application files have private archive modes until materialization. Verify
    # their independent signature/inventory without trusting a carried key.
    app_root = directory / 'application';app_raw = private_read(app_root / 'manifest.json', 4_000_000)
    app_manifest, rows = bundle.validate_manifest(app_raw, private_read(app_root / 'manifest.sig'), private_read(release_trust))
    app_files = {r['path'][12:]: r for r in value['files'] if r['path'].startswith('application/') and r['path'] not in {'application/manifest.json','application/manifest.sig'}}
    require(set(app_files) == set(rows) and all(app_files[k]['sha256'] == r['sha256'] and app_files[k]['bytes'] == r['bytes'] for k,r in rows.items()), 'RECOVERY_APPLICATION', 'Independent application inventory does not match the recovery payload.', 409)
    require(hashlib.sha256(app_raw).hexdigest() == value['artifact_id'] and app_manifest['application_fingerprint'] == value['application_fingerprint'], 'RECOVERY_APPLICATION', 'Recovery release binding differs from its signed application.', 409)
    require(all(bundle.sha(directory / 'native' / role) == app_manifest['native_services'].get(role, {}).get('sha256') for role in ['accounting','fault']), 'RECOVERY_NATIVE', 'Recovery native binary differs from its signed dependency pin.', 409)
    require(set(value['databases']) == {'control','accounting'} and set(value['database_settings']) == {'control','accounting'}, 'RECOVERY_FORMAT', 'Both authoritative databases are required.', 409)
    return {'set_id': hashlib.sha256(raw).hexdigest(), 'directory': str(directory.resolve()), 'manifest': value}


def stage(source, destination, recovery_trust, release_trust):
    from .deployment_backup import restore as restore_deployment
    from .aa.recovery import restore as restore_roles
    from .lab_identity import restore_private_banks
    started = time.time();checked = verify(source, recovery_trust, release_trust);destination = Path(destination)
    private_root(destination.parent)
    require(not destination.exists() and not destination.is_symlink(), 'RECOVERY_EXISTS', 'Stage into a new private directory.', 409)
    destination.mkdir(mode=0o700);data = destination / 'data';data.mkdir(mode=0o700)
    write_document(data / INACTIVE, {'format': 'avenra-recovery-inactive/1', 'set_id': checked['set_id'], 'state': 'staging'})
    try:
        copy_tree(source, destination / 'payload')
        artifact = destination / 'artifacts' / checked['manifest']['artifact_id'];copy_tree(destination / 'payload/application', artifact)
        for path in artifact.rglob('*'):
            if path.is_file(): path.chmod(0o444)
        bundle.verify_installed(artifact, release_trust)
        runtime = destination / 'runtime';bundle.prepare_runtime(artifact, release_trust, runtime)
        restore_deployment(destination / 'payload/private-deployment', destination / 'original-deployment')
        for path in (destination / 'original-deployment').rglob('*'):
            if path.is_file() and path.relative_to(destination / 'original-deployment').as_posix()!=INACTIVE:
                copy_private(path, data / path.relative_to(destination / 'original-deployment'))
        restored_roles = restore_roles(destination / 'payload/financial-data', destination / 'original-financial-data')
        if (destination / 'original-financial-data/aa-scopes').exists():
            shutil.move(str(destination / 'original-financial-data/aa-scopes'), data / 'aa-scopes')
        banks = restore_private_banks(destination / 'payload/synthetic-banks', data)
        peers = {'valid': True, 'workers': 0, 'identities': 0, 'activation': 'inactive'}
        if (destination / 'payload/independent-participants').exists():
            from .participant_peer_checkpoint import restore as restore_peers
            peers = restore_peers(destination / 'payload/independent-participants', data)
        for name in ['evidence.key', 'services.json', 'identity.json', 'product-credentials.json']:
            if (destination / 'payload/private' / name).exists():
                original = destination / 'payload/private' / name
                if (data / name).exists():
                    require(bundle.sha(data / name) == bundle.sha(original), 'RECOVERY_CHANGED', 'Duplicate private snapshots disagree.', 409)
                else: copy_private(original, data / name)
        for role in ['accounting','fault']:
            copy_private(destination / 'payload/native' / role, destination / 'native' / role)
            (destination / 'native' / role).chmod(0o700)
        value = {'format': 'avenra-recovery-stage/1', 'set_id': checked['set_id'], 'artifact_id': checked['manifest']['artifact_id'],
                 'application_fingerprint': checked['manifest']['application_fingerprint'], 'activation': 'inactive', 'stage_seconds': time.time() - started,
                 'private_roles': restored_roles, 'private_banks': banks, 'private_peers': peers, 'created_at': time.time()}
        write_document(destination / 'recovery-stage.json', value)
        return {**value, 'directory': str(destination.resolve())}
    except BaseException:
        write_document(data / INACTIVE, {'format': 'avenra-recovery-inactive/1', 'set_id': checked['set_id'], 'state': 'failed_staging'})
        raise
