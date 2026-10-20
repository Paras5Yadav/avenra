"""PostgreSQL control persistence with the same financial orchestration contract."""
import os
from pathlib import Path
import secrets

from .store import Store


class OrderedRecord(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if type(key) is int else super().__getitem__(key)


def record_factory(cursor):
    columns = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: OrderedRecord(zip(columns, values))


class Connection:
    def __init__(self, connection):
        self.raw = connection

    def execute(self, statement, parameters=()):
        # Application SQL is a closed set. Values are always driver parameters.
        ignore = statement.startswith('INSERT OR IGNORE INTO ')
        if ignore:
            statement = statement.replace('INSERT OR IGNORE INTO ', 'INSERT INTO ', 1).rstrip(';') + ' ON CONFLICT DO NOTHING'
        return self.raw.execute(statement.replace('?', '%s'), parameters)


class PostgresStore(Store):
    def __init__(self, directory, dsn):
        from psycopg_pool import ConnectionPool
        from .private_config import deployment
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_path = self.directory / 'evidence.key'
        try:
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, 'wb') as target:
                target.write(secrets.token_bytes(32))
        from .lab_identity import private_read
        self.signing_key = private_read(key_path)
        if len(self.signing_key) != 32:
            raise RuntimeError('Preserve the original evidence signing key.')
        policy = deployment(self.directory).get('database', {})
        runtime_only = policy.get('mode') == 'runtime'
        release = deployment(self.directory).get('release', {})
        from .release_runtime import configure_connection
        from .util import application_fingerprint
        source_fingerprint = application_fingerprint() if release.get('artifact_id') else None
        self.pool = ConnectionPool(dsn, min_size=policy.get('pool_min', 1), max_size=policy.get('pool_max', 8), timeout=policy.get('pool_timeout_seconds', 10), kwargs={'autocommit': True, 'row_factory': record_factory},
                                   configure=(lambda connection: configure_connection(connection, release, source_fingerprint)) if runtime_only else None, open=True)
        try:
            self.pool.wait(timeout=15)
            self.initialize(runtime_only)
        except BaseException:
            self.pool.close(timeout=5)
            raise

    def initialize(self, runtime_only):
        with self.pool.connection() as connection, connection.transaction():
            if runtime_only:
                present = connection.execute("SELECT to_regclass('avenra_schema_version') AS name").fetchone()['name']
                if not present or connection.execute('SELECT version FROM avenra_schema_version').fetchone()[0] != 9:
                    raise RuntimeError('The deployment migration identity must install the matching control schema before runtime startup.')
                privilege = connection.execute("SELECT current_setting('is_superuser') AS superuser,has_schema_privilege(current_user,'public','CREATE') AS can_create").fetchone()
                if privilege['superuser'] != 'off' or privilege['can_create']:
                    raise RuntimeError('Runtime database identity must be non-superuser without schema creation privileges.')
                owned = connection.execute("SELECT 1 FROM pg_class WHERE relnamespace='public'::regnamespace AND relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) LIMIT 1").fetchone()
                if owned:
                    raise RuntimeError('Runtime identity cannot own application tables, indexes or sequences.')
                return
            connection.execute('SELECT pg_advisory_xact_lock(781234911)')
            exists = connection.execute("SELECT to_regclass('avenra_schema_version') AS name").fetchone()['name']
            if not exists:
                occupied = connection.execute("SELECT to_regclass('tenants') AS name").fetchone()['name']
                if occupied:
                    raise RuntimeError('Existing control tables require an explicit migration; refusing to replace them.')
                connection.execute(Path(__file__).with_name('postgres_schema.sql').read_text())
            version = connection.execute('SELECT version FROM avenra_schema_version').fetchone()[0]
            if version == 3:
                connection.execute(Path(__file__).with_name('migrations').joinpath('004_fixture_profiles.sql').read_text())
                connection.execute('UPDATE avenra_schema_version SET version=4')
                version = 4
            if version == 4:
                connection.execute(Path(__file__).with_name('migrations').joinpath('005_identity.sql').read_text())
                connection.execute('UPDATE avenra_schema_version SET version=5')
                version = 5
            if version == 5:
                connection.execute(Path(__file__).with_name('migrations').joinpath('006_transport.sql').read_text().replace(' REAL', ' DOUBLE PRECISION'))
                connection.execute('UPDATE avenra_schema_version SET version=6')
                version = 6
            if version == 6:
                from .simulation import postgres_migration
                connection.execute(postgres_migration())
                connection.execute('UPDATE avenra_schema_version SET version=7')
                version = 7
            if version == 7:
                from .upi import postgres_migration
                connection.execute(postgres_migration())
                connection.execute('UPDATE avenra_schema_version SET version=8')
                version = 8
            if version == 8:
                from .participant_connections import postgres_migration
                connection.execute(postgres_migration())
                connection.execute('UPDATE avenra_schema_version SET version=9')
                version = 9
            if version != 9:
                raise RuntimeError('Control database requires a compatible application version.')

    def connect(self):
        from contextlib import contextmanager
        @contextmanager
        def connection():
            with self.pool.connection() as current:
                yield Connection(current)
        return connection()

    def transaction(self):
        from contextlib import contextmanager
        @contextmanager
        def transaction():
            with self.pool.connection() as current, current.transaction():
                # The audit chain and request acceptance share a short serialized
                # write boundary. External financial calls occur outside this lock.
                current.execute('SELECT pg_advisory_xact_lock(781234912)')
                yield Connection(current)
        return transaction()

    def reader(self):
        from contextlib import contextmanager
        @contextmanager
        def reader():
            with self.pool.connection() as current, current.transaction():
                current.execute('SET TRANSACTION READ ONLY')
                yield Connection(current)
        return reader()

    def close(self):
        self.pool.close(timeout=10)
