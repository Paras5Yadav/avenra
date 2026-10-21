"""Provision a new PostgreSQL deployment with distinct migration/runtime roles.

This never changes roles or permissions in an existing deployment database.
The migration identity stays in a separate private file and is not used by API
or durable-worker processes after installation.
"""
from pathlib import Path
import secrets

from .errors import require
from .lab_identity import private_write
from .private_config import deployment, write_document
from .util import canonical, uid


def provision(directory, administrative_dsn):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from .app import App
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not (directory / 'control.dsn').exists() and not (directory / 'migration.dsn').exists(), 'DEPLOYMENT_EXISTS', 'Preserve existing database identities; use explicit migrations.', 409)
    suffix = uid('database').split('_')[1]
    name, migrator, runtime = ('avenra_' + suffix, 'avenra_migrate_' + suffix, 'avenra_runtime_' + suffix)
    migration_password, runtime_password = secrets.token_urlsafe(36), secrets.token_urlsafe(36)
    # Generated identifiers and SQL literals are composed by the driver, never
    # interpolated into a shell command or recorded in the public receipt.
    with psycopg.connect(administrative_dsn, autocommit=True) as db:
        for role, password in [(migrator, migration_password), (runtime, runtime_password)]:
            db.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 16 PASSWORD {}').format(sql.Identifier(role), sql.Literal(password)))
        db.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name), sql.Identifier(migrator)))
        db.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
        db.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(name), sql.Identifier(runtime)))
    connection = conninfo_to_dict(administrative_dsn)
    migration_dsn = make_conninfo(**{**connection, 'dbname': name, 'user': migrator, 'password': migration_password})
    runtime_dsn = make_conninfo(**{**connection, 'dbname': name, 'user': runtime, 'password': runtime_password})
    private_write(directory / 'migration.dsn', migration_dsn)
    # Installation is an explicit one-time step using only the migration role.
    app = App(directory, control_dsn=migration_dsn)
    try: app.bootstrap()
    finally: app.close()
    with psycopg.connect(migration_dsn, autocommit=True) as db:
        db.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
        db.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(runtime)))
        rows = db.execute("""SELECT c.relname,
            EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
                WHERE t.tgrelid=c.oid AND p.proname='avenra_immutable_record'
                AND (t.tgtype & 16)=16 AND t.tgqual IS NULL AND t.tgattr=''::int2vector) AS immutable
            FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relkind='r'""").fetchall()
        versions = {'avenra_schema_version', 'fidelity_schema', 'runtime_schema', 'aa_schema', 'ops_schema', 'outbox_retry_schema'}
        for table, immutable in rows:
            permissions = ['SELECT'] if table in versions else ['SELECT', 'INSERT'] + ([] if immutable else ['UPDATE'])
            if table in {'sessions', 'identity_logins'}: permissions.append('DELETE')
            db.execute(sql.SQL('GRANT {} ON TABLE {} TO {}').format(sql.SQL(',').join(map(sql.SQL, permissions)), sql.Identifier(table), sql.Identifier(runtime)))
        db.execute(sql.SQL('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(runtime)))
        db.execute(sql.SQL('ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC').format(sql.Identifier(migrator)))
    config = deployment(directory)
    config.update(format='avenra-deployment/1', database={'mode': 'runtime', 'pool_min': 1, 'pool_max': 4, 'pool_timeout_seconds': 5})
    write_document(directory / 'deployment.json', config)
    private_write(directory / 'control.dsn', runtime_dsn)
    return {'database': name, 'migration_role': migrator, 'runtime_role': runtime, 'directory': str(directory),
            'scope': 'New deployment only. Database credentials are private files; public receipt contains no secrets.'}


def upgrade_operations(directory, artifact_directory, trusted_public_file, expected, request_id, actor):
    """One reviewed additive migration, with all runtime nodes drained.

    The signed artifact must carry this exact known DDL. No uploaded migration
    script is executed. Core versions, financial rows, grants and release pointer
    are preserved. Rollback code can ignore these additional retained tables.
    """
    from .release_bundle import verify_installed, sha, SCHEMAS
    from .release_runtime import installation_lock, migration_connection, validate_request, LOCK
    from .operations import install_schema, schema_hash, TABLES
    from .postgres_store import Connection
    from .util import digest
    from .private_config import read_document
    from psycopg import sql
    directory=Path(directory).resolve();validate_request(request_id,expected,actor,'activate')
    artifact=verify_installed(artifact_directory,trusted_public_file)
    require(artifact['manifest']['schemas']==SCHEMAS and sha(Path(artifact['directory'])/'avenra/operations_schema.sql')==schema_hash(),
            'MIGRATION_ARTIFACT','The selected signed release must carry the exact reviewed operations migration.',409)
    payload={'artifact_id':artifact['artifact_id'],'expected':expected,'actor':actor,'source_sha256':schema_hash()};hashed=digest(payload)
    with installation_lock(directory):
        require(not read_document(directory/'release-pending.json'),'RELEASE_RECOVERY_REQUIRED','Recover the original release transition before applying another migration.',409)
        db,role=migration_connection(directory)
        with db:
            require(db.execute('SELECT pg_try_advisory_lock(%s)',(LOCK,)).fetchone()[0],'RELEASE_NODES_ACTIVE','Drain all managed API/worker nodes before applying the operations migration.',409)
            with db.transaction():
                if db.execute("SELECT to_regclass('release_active')").fetchone()[0]:
                    current=db.execute('SELECT artifact_id FROM release_active WHERE singleton=1').fetchone();current=current[0] if current else None
                else:current=None
                require(current==expected,'RELEASE_STALE','Shared release selection changed before the migration.',409)
                if db.execute("SELECT to_regclass('ops_schema')").fetchone()[0]:
                    row=db.execute('SELECT version,source_sha256,installation_json FROM ops_schema').fetchone()
                    require(row[0:2]==(1,schema_hash()),'MIGRATION_CHECKSUM','Existing operations schema has a different reviewed checksum.',409)
                    import json
                    previous=json.loads(row[2])
                    if previous.get('request_id')==request_id:
                        require(previous.get('request_hash')==hashed,'IDEMPOTENCY_CONFLICT','Migration request identity changed content or actor.',409)
                    return {'installed':True,'already_installed':True,'replayed':previous.get('request_id')==request_id,'source_sha256':schema_hash(),'release_pointer_changed':False}
                installation={'request_id':request_id,'request_hash':hashed,**payload,'mode':'offline_additive_migration'}
                install_schema(Connection(db),True,installation)
                for table in TABLES:
                    permissions=['SELECT'] if table=='ops_schema' else ['SELECT','INSERT']+([] if table in {'ops_policies','ops_alert_events'} else ['UPDATE'])
                    db.execute(sql.SQL('GRANT {} ON TABLE {} TO {}').format(sql.SQL(',').join(map(sql.SQL,permissions)),sql.Identifier(table),sql.Identifier(role)))
                db.execute(sql.SQL('GRANT USAGE,SELECT ON SEQUENCE ops_alerts_sequence_seq,ops_alert_events_sequence_seq TO {}').format(sql.Identifier(role)))
    return {'installed':True,'already_installed':False,'request_id':request_id,'source_sha256':schema_hash(),'release_pointer_changed':False,'financial_history_rewound':False}


def upgrade_work_queue(directory, artifact_directory, trusted_public_file, expected, request_id, actor):
    """Install only the reviewed control 9 -> 10 retry migration, offline."""
    import json
    from psycopg import sql
    from . import outbox_retry
    from .postgres_store import Connection
    from .private_config import read_document
    from .release_bundle import verify_installed, sha, SCHEMAS
    from .release_runtime import installation_lock, migration_connection, validate_request, LOCK
    from .util import digest
    directory = Path(directory).resolve()
    validate_request(request_id, expected, actor, 'activate')
    artifact = verify_installed(artifact_directory, trusted_public_file)
    require(artifact['manifest']['schemas'] == SCHEMAS and
            sha(Path(artifact['directory']) / 'avenra/outbox_retry_schema.sql') == outbox_retry.schema_hash(),
            'MIGRATION_ARTIFACT', 'The signed release must carry the exact reviewed worker retry migration.', 409)
    payload = {'artifact_id': artifact['artifact_id'], 'expected': expected, 'actor': actor, 'source_sha256': outbox_retry.schema_hash()}
    hashed = digest(payload)
    with installation_lock(directory):
        require(not read_document(directory / 'release-pending.json'), 'RELEASE_RECOVERY_REQUIRED', 'Recover the original release transition before applying another migration.', 409)
        db, role = migration_connection(directory)
        with db:
            require(db.execute('SELECT pg_try_advisory_lock(%s)', (LOCK,)).fetchone()[0], 'RELEASE_NODES_ACTIVE', 'Drain all API/worker nodes before migrating worker retries.', 409)
            with db.transaction():
                current = None
                if db.execute("SELECT to_regclass('release_active')").fetchone()[0]:
                    row = db.execute('SELECT artifact_id FROM release_active WHERE singleton=1').fetchone()
                    current = row[0] if row else None
                require(current == expected, 'RELEASE_STALE', 'Shared release selection changed before the migration.', 409)
                versions = db.execute('SELECT version FROM avenra_schema_version').fetchall()
                require(versions in [[(9,)], [(10,)]], 'MIGRATION_VERSION', 'Only the reviewed control version 9 to 10 migration is supported.', 409)
                if versions == [(10,)]:
                    outbox_retry.validate(Connection(db), 'MIGRATION_CHECKSUM')
                    previous = json.loads(db.execute('SELECT installation_json FROM outbox_retry_schema').fetchone()[0])
                    replayed = previous.get('request_id') == request_id
                    require(not replayed or previous.get('request_hash') == hashed, 'IDEMPOTENCY_CONFLICT', 'Migration request identity changed content or actor.', 409)
                    return {'installed': True, 'already_installed': True, 'replayed': replayed, 'source_sha256': outbox_retry.schema_hash(), 'release_pointer_changed': False}
                installation = {'request_id': request_id, 'request_hash': hashed, **payload, 'mode': 'offline_control_migration'}
                outbox_retry.install(Connection(db), True, installation)
                db.execute(sql.SQL('GRANT SELECT ON outbox_retry_schema TO {}').format(sql.Identifier(role)))
                db.execute('UPDATE avenra_schema_version SET version=10')
    return {'installed': True, 'already_installed': False, 'request_id': request_id, 'source_sha256': outbox_retry.schema_hash(),
            'release_pointer_changed': False, 'financial_history_rewound': False, 'legacy_error_policy': 'Retained jobs with last_error keep the legacy claim budget; terminal jobs stay terminal.'}
