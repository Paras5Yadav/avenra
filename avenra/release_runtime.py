"""Database-fenced, recoverable application release selection.

Only schema-preserving code upgrades are supported. No financial data rollback,
automatic destructive downgrade, native engine upgrade or live connector exists.
Runtime pool connections hold a shared lock; the offline migration identity must
obtain its exclusive counterpart before a compare-and-swap activation commits.
"""
from contextlib import contextmanager
import ast
import fcntl
import hashlib
import os
from pathlib import Path
import re
import uuid

from .errors import require
from .lab_identity import private_read
from .private_config import deployment, read_document, write_document
from .release_bundle import SCHEMAS, canonical, digest, verify_installed, runtime_target

LOCK = 781234919
FORMAT = 'avenra-release-transition/1'


def configure_connection(connection, release, source_fingerprint):
    """Pool configure hook; closed/replaced connections must acquire a new fence."""
    if release.get('artifact_id'):
        require(release['application_fingerprint'] == source_fingerprint, 'RELEASE_MISMATCH', 'This runtime does not match the selected application source.', 503)
    # Nonblocking: a migration holding the exclusive fence refuses new nodes.
    require(connection.execute('SELECT pg_try_advisory_lock_shared(%s) AS acquired', (LOCK,)).fetchone()['acquired'],
            'RELEASE_DRAINING', 'A release transition is in progress; do not start another runtime.', 503)
    present = connection.execute("SELECT to_regclass('release_active') AS present").fetchone()['present']
    if not present:
        require(not release.get('artifact_id'), 'RELEASE_MISSING', 'Managed release state must be installed before runtime startup.', 503)
        return
    current = connection.execute('SELECT artifact_id,application_fingerprint FROM release_active WHERE singleton=1').fetchone()
    if not release.get('artifact_id'):
        require(not current, 'RELEASE_MISMATCH', 'This database requires its managed release selection before runtime startup.', 503)
        return
    require(current and current['artifact_id'] == release['artifact_id'] and current['application_fingerprint'] == source_fingerprint,
            'RELEASE_MISMATCH', 'The shared database selects a different application release.', 503)
    from .participant_capabilities import ensure_current
    ensure_current(connection)


@contextmanager
def installation_lock(directory):
    directory = Path(directory);directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(directory / 'release.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: require(False, 'RELEASE_BUSY', 'Another local release operation is active.', 409)
        yield


def schema_versions(db, required=None):
    from psycopg import sql
    required=required or SCHEMAS
    observed = {}
    for table in required:
        require(db.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0], 'RELEASE_SCHEMA', 'A required application schema is missing; migrate explicitly before activation.', 409)
        values = db.execute(sql.SQL('SELECT version FROM {}').format(sql.Identifier(table))).fetchall()
        require(len(values) == 1, 'RELEASE_SCHEMA', 'Schema version inventory is ambiguous.', 409)
        observed[table] = values[0][0]
    require(observed == required, 'RELEASE_SCHEMA', 'Installed schemas are incompatible with this release. No automatic downgrade is permitted.', 409)
    if db.execute("SELECT to_regclass('ops_schema')").fetchone()[0]:
        from .operations import schema_hash
        rows=db.execute('SELECT version,source_sha256 FROM ops_schema').fetchall()
        require(len(rows)==1 and rows[0]==(1,schema_hash()),'RELEASE_SCHEMA','Unknown operations migration version/checksum cannot be activated or downgraded.',409)
        observed['ops_schema']=1
    return observed


def install_catalog(db, runtime_role):
    from psycopg import sql
    db.execute('''CREATE TABLE IF NOT EXISTS release_active(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), artifact_id TEXT NOT NULL,
        application_fingerprint TEXT NOT NULL, transition_id TEXT NOT NULL, generation INTEGER NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS release_events(
        request_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, operation TEXT NOT NULL,
        previous_artifact_id TEXT, artifact_id TEXT NOT NULL, application_fingerprint TEXT NOT NULL,
        actor TEXT NOT NULL, generation INTEGER NOT NULL UNIQUE, schemas_json TEXT NOT NULL,
        committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())''')
    for action in ['UPDATE', 'DELETE']:
        trigger = 'release_events_' + action.lower()
        if not db.execute('SELECT 1 FROM pg_trigger WHERE tgrelid=\'release_events\'::regclass AND tgname=%s', (trigger,)).fetchone():
            db.execute(sql.SQL('CREATE TRIGGER {} BEFORE {} ON release_events FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record()').format(sql.Identifier(trigger), sql.SQL(action)))
    db.execute(sql.SQL('GRANT SELECT ON release_active,release_events TO {}').format(sql.Identifier(runtime_role)))


def validate_request(request_id, expected, actor, operation):
    try: valid = str(uuid.UUID(request_id)) == request_id
    except (ValueError, AttributeError): valid = False
    require(valid and (expected is None or isinstance(expected, str) and re.fullmatch('[a-f0-9]{64}', expected)), 'RELEASE_REQUEST', 'Use a UUID transition identity and the expected active artifact hash.', 409)
    require(isinstance(actor, str) and 1 <= len(actor) <= 120 and all(ord(c) >= 32 for c in actor) and operation in {'activate', 'rollback'},
            'RELEASE_REQUEST', 'An attributed activation or rollback is required.', 409)


def migration_connection(directory):
    from .recovery_state import require_active
    require_active(directory)
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    directory = Path(directory)
    migrator = conninfo_to_dict(private_read(directory / 'migration.dsn').decode().strip())
    runtime = conninfo_to_dict(private_read(directory / 'control.dsn').decode().strip())
    require(all(migrator.get(k) == runtime.get(k) for k in ['host', 'port', 'dbname']) and migrator.get('user') != runtime.get('user'),
            'RELEASE_DATABASE', 'Migration and runtime identities must be distinct roles of the same deployment database.', 409)
    return psycopg.connect(**migrator, autocommit=True, connect_timeout=5), runtime['user']


def status(directory):
    from .recovery_state import require_active
    require_active(directory)
    import psycopg
    directory = Path(directory)
    with psycopg.connect(private_read(directory / 'control.dsn').decode().strip(), autocommit=True, connect_timeout=5) as db:
        exists = db.execute("SELECT to_regclass('release_active')").fetchone()[0]
        value = db.execute('SELECT artifact_id,application_fingerprint,transition_id,generation FROM release_active WHERE singleton=1').fetchone() if exists else None
    return {'active': dict(zip(['artifact_id', 'application_fingerprint', 'transition_id', 'generation'], value)) if value else None,
            'pending_recovery': (directory / 'release-pending.json').exists(), 'selection': read_document(directory / 'release-selection.json')}


def runtime_receipt(artifact, runtime_directory):
    from .release_bundle import parse
    runtime = Path(runtime_directory).resolve();value = parse(private_read(runtime / 'release-runtime.json', 4_000_000))
    require(value and value.get('artifact_id') == artifact['artifact_id'] and value.get('target') == runtime_target(), 'RELEASE_RUNTIME', 'Prepare a separate offline runtime for the selected artifact.', 409)
    from .release_bundle import sha
    require(value.get('lock_sha256') == sha(Path(artifact['directory']) / 'requirements.lock'), 'RELEASE_RUNTIME', 'Runtime dependency lock differs from the artifact.', 409)
    from .release_bundle import runtime_inventory
    require(value.get('files') == runtime_inventory(runtime), 'RELEASE_RUNTIME', 'Prepared runtime files or interpreter differ from the retained installation receipt.', 409)
    return runtime


def recovery_configuration(directory, set_id):
    """Qualify only new target bindings, never a copied source's physical pins."""
    directory=Path(directory);configuration=read_document(directory/'deployment.json',{})
    recovery={'set_id':set_id}
    for name,field,module in [('bank-recovery.json','bank_routes_sha256','bank'),('aa-recovery.json','aa_routes_sha256','aa'),('peer-recovery.json','peer_routes_sha256','peer')]:
        path=directory/name
        if not path.exists() and not path.is_symlink():continue
        if module=='bank':from .bank_recovery import load
        elif module=='aa':from .aa.routing import load
        else:from .peer_recovery import load
        require(load(directory)['set_id']==set_id,'RECOVERY_CONFIGURATION_CHANGED','A physical binding belongs to another target recovery.',409)
        recovery[field]=hashlib.sha256(private_read(path)).hexdigest()
    configuration['recovery']=recovery if len(recovery)>1 else {}
    return configuration


def require_deployment_capabilities(directory, artifact, configuration=None):
    """Refuse code that cannot honor the deployment's selected physical bindings.

    The candidate is already signature/inventory verified. Its capability file
    is parsed as bounded literal data; no candidate module is imported/executed
    to decide whether selection or startup is permissible.
    """
    directory = Path(directory)
    configuration = deployment(directory) if configuration is None else configuration
    required = {}
    engines = directory/'engine-endpoints.json'
    if engines.exists() or engines.is_symlink():
        from .recovery_state import engine_bindings
        engine_bindings(directory,'','')
        required['engine_bindings'] = read_document(engines)['format']
    if configuration.get('recovery',{}).get('bank_routes_sha256'):
        from .bank_recovery import load
        required['bank_routes'] = load(directory)['format']
    if configuration.get('recovery',{}).get('aa_routes_sha256'):
        from .aa.routing import load
        required['aa_routes'] = load(directory)['format']
    if configuration.get('recovery',{}).get('peer_routes_sha256'):
        from .peer_recovery import load
        required['peer_routes'] = load(directory)['format']
    if not required: return {}
    from .participant_capabilities import declared
    value = declared(artifact)
    require(all(version in value.get(key,[]) for key,version in required.items()),
            'RELEASE_CAPABILITIES','The signed candidate does not support every configured recovery binding version.',409)
    return required


def publish(directory, pending):
    directory = Path(directory)
    current = deployment(directory)
    require(digest(current) in {pending['configuration_before_hash'], digest(pending['configuration_after'])},
            'RELEASE_CONFIGURATION_CHANGED', 'Deployment configuration changed during the transition; preserve the pending record for operator review.', 409)
    write_document(directory / 'deployment.json', pending['configuration_after'])
    write_document(directory / 'release-selection.json', {k: pending[k] for k in ['artifact_id', 'application_fingerprint', 'artifact_directory', 'runtime_directory', 'request_id']})
    (directory / 'release-pending.json').unlink()
    descriptor = os.open(directory, os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def transition(directory, artifact_directory, trusted_public_file, runtime_directory, expected, request_id, actor, operation='activate', checkpoint=None):
    """CAS commit then publish. An interrupted publication is recovered explicitly.

    checkpoint is for fault qualification: it can terminate the operator process
    at a real boundary, after durable preparation or after the database commit.
    """
    directory = Path(directory).resolve();validate_request(request_id, expected, actor, operation)
    with installation_lock(directory):
        require(not (directory / 'release-pending.json').exists(), 'RELEASE_RECOVERY_REQUIRED', 'Recover the existing interrupted transition before selecting another release.', 409)
        artifact = verify_installed(artifact_directory, trusted_public_file);runtime = runtime_receipt(artifact, runtime_directory)
        configuration = deployment(directory)
        require_deployment_capabilities(directory,artifact,configuration)
        require(configuration.get('database', {}).get('mode') == 'runtime', 'RELEASE_DATABASE', 'Managed releases require an installed least-privilege PostgreSQL deployment.', 409)
        after = {**configuration, 'release': {'application_fingerprint': artifact['manifest']['application_fingerprint'], 'artifact_id': artifact['artifact_id']}}
        payload = {'operation': operation, 'expected': expected, 'artifact_id': artifact['artifact_id'], 'actor': actor}
        pending = {'format': FORMAT, 'artifact_id': artifact['artifact_id'], 'application_fingerprint': artifact['manifest']['application_fingerprint'],
                   'artifact_directory': artifact['directory'], 'runtime_directory': str(runtime), 'request_id': request_id, 'payload_hash': digest(payload),
                   'configuration_before_hash': digest(configuration), 'configuration_after': after}
        db, role = migration_connection(directory)
        with db:
            require(db.execute('SELECT pg_try_advisory_lock(%s)', (LOCK,)).fetchone()[0], 'RELEASE_NODES_ACTIVE', 'Drain and stop all managed API/worker nodes before changing the release.', 409)
            with db.transaction():
                installed_schemas=schema_versions(db,artifact['manifest']['schemas'])
                from .participant_capabilities import ensure
                ensure(db,artifact)
                install_catalog(db, role)
                previous = db.execute('SELECT artifact_id,generation FROM release_active WHERE singleton=1 FOR UPDATE').fetchone()
                old = previous[0] if previous else None
                replay = db.execute('SELECT payload_hash,artifact_id,generation FROM release_events WHERE request_id=%s', (request_id,)).fetchone()
                if replay:
                    require(replay[0] == pending['payload_hash'], 'RELEASE_CONFLICT', 'Transition identity has a different payload.', 409)
                    require(old == replay[1], 'RELEASE_SUPERSEDED', 'This completed transition has since been superseded; it cannot be replayed as a new activation.', 409)
                    return {'artifact_id': old, 'generation': replay[2], 'replayed': True}
                require(old == expected, 'RELEASE_STALE', 'Active release changed; inspect its state before retrying.', 409)
                require(old != artifact['artifact_id'], 'RELEASE_ALREADY_ACTIVE', 'The selected artifact is already active.', 409)
                if operation == 'rollback':
                    require(db.execute('SELECT 1 FROM release_events WHERE artifact_id=%s', (artifact['artifact_id'],)).fetchone(), 'RELEASE_ROLLBACK_TARGET', 'Rollback selects a previously committed compatible artifact only.', 409)
                generation = previous[1] + 1 if previous else 1
                # Record before committing either the DB pointer or filesystem
                # selection. Runtime startup refuses either mismatch window.
                write_document(directory / 'release-pending.json', pending)
                if checkpoint: checkpoint('prepared')
                db.execute('INSERT INTO release_events(request_id,payload_hash,operation,previous_artifact_id,artifact_id,application_fingerprint,actor,generation,schemas_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                           (request_id, pending['payload_hash'], operation, old, artifact['artifact_id'], pending['application_fingerprint'], actor, generation, canonical(installed_schemas)))
                db.execute('INSERT INTO release_active VALUES(1,%s,%s,%s,%s) ON CONFLICT(singleton) DO UPDATE SET artifact_id=EXCLUDED.artifact_id,application_fingerprint=EXCLUDED.application_fingerprint,transition_id=EXCLUDED.transition_id,generation=EXCLUDED.generation',
                           (artifact['artifact_id'], pending['application_fingerprint'], request_id, generation))
            if checkpoint: checkpoint('committed')
            publish(directory, pending)
        return {'artifact_id': artifact['artifact_id'], 'generation': generation, 'replayed': False, 'schema_change': False, 'financial_history_rewound': False}


def recover(directory, trusted_public_file):
    directory = Path(directory).resolve()
    with installation_lock(directory):
        pending = read_document(directory / 'release-pending.json')
        require(pending and pending.get('format') == FORMAT, 'RELEASE_RECOVERY_REQUIRED', 'No supported pending transition is present.', 409)
        artifact = verify_installed(pending['artifact_directory'], trusted_public_file);runtime_receipt(artifact, pending['runtime_directory'])
        require_deployment_capabilities(directory,artifact)
        require(artifact['artifact_id'] == pending['artifact_id'], 'RELEASE_INTEGRITY', 'Pending transition no longer matches its artifact.', 409)
        db, _ = migration_connection(directory)
        with db:
            require(db.execute('SELECT pg_try_advisory_lock(%s)', (LOCK,)).fetchone()[0], 'RELEASE_NODES_ACTIVE', 'Stop managed runtime nodes before transition recovery.', 409)
            exists = db.execute("SELECT to_regclass('release_events')").fetchone()[0]
            event = db.execute('SELECT payload_hash,artifact_id FROM release_events WHERE request_id=%s', (pending['request_id'],)).fetchone() if exists else None
            if not event:
                # Preparation survived but its transaction did not commit. The
                # old selection remains authoritative and is never inferred.
                write_document(directory / ('release-aborted-' + pending['request_id'] + '.json'), pending)
                (directory / 'release-pending.json').unlink()
                return {'committed': False, 'recovered': True, 'request_id': pending['request_id']}
            current = db.execute('SELECT artifact_id,transition_id FROM release_active WHERE singleton=1').fetchone()
            require(event == (pending['payload_hash'], pending['artifact_id']) and current == (pending['artifact_id'], pending['request_id']),
                    'RELEASE_CONFLICT', 'Pending release no longer matches the authoritative database transition.', 409)
            from .participant_capabilities import ensure
            ensure(db,artifact)
            publish(directory, pending)
            return {'committed': True, 'recovered': True, 'artifact_id': pending['artifact_id'], 'request_id': pending['request_id']}


def command(directory, trusted_public_file, mode, port=4827, host='127.0.0.1'):
    directory = Path(directory).resolve();require(mode in {'api', 'combined', 'worker'}, 'RELEASE_MODE', 'Select an API or worker role.', 409)
    value = status(directory);selection = value['selection']
    require(not value['pending_recovery'] and selection and value['active'] and selection['artifact_id'] == value['active']['artifact_id'], 'RELEASE_RECOVERY_REQUIRED', 'Complete release selection/recovery before runtime startup.', 409)
    artifact = verify_installed(selection['artifact_directory'], trusted_public_file);runtime = runtime_receipt(artifact, selection['runtime_directory'])
    require_deployment_capabilities(directory,artifact)
    require(selection['artifact_id'] == artifact['artifact_id'] and deployment(directory).get('release') == {'artifact_id': artifact['artifact_id'], 'application_fingerprint': artifact['manifest']['application_fingerprint']}, 'RELEASE_MISMATCH', 'Filesystem and database release selection disagree.', 409)
    from .participant_capabilities import ensure
    connection,_ = migration_connection(directory)
    with connection:ensure(connection,artifact)
    module = 'avenra.worker' if mode == 'worker' else 'avenra.asgi'
    # Isolated Python ignores caller cwd, PYTHONPATH and user site packages. The
    # only application import path comes from the verified signed artifact.
    code = 'import sys;sys.path.insert(0,sys.argv.pop(1));from ' + module + ' import main;main()'
    arguments = [str(runtime / 'bin/python'), '-I', '-B', '-c', code, artifact['directory'], '--data', str(directory)]
    if mode != 'worker': arguments.extend(['--mode', mode, '--port', str(port), '--host', host])
    return arguments
