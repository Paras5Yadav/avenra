#!/usr/bin/env python3
"""Replace this installation's trust authentication and isolate accounting ownership."""
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.local_database import admin_dsn


def main():
    import psycopg
    from psycopg import sql
    data = ROOT / '.local/data'
    pending = data / 'database-auth.pending.json'
    if not pending.exists():
        pending.write_text(json.dumps({'admin': secrets.token_urlsafe(40), 'accounting': secrets.token_urlsafe(40)}));pending.chmod(0o600)
    credentials = json.loads(pending.read_text())
    def uri(user, password, database):
        return 'postgresql://' + user + ':' + quote(password, safe='') + '@127.0.0.1:55437/' + database
    admin = uri('avenra', credentials['admin'], 'postgres')
    ledger = uri('avenra_accounting', credentials['accounting'], 'avenra_ledger')
    subprocess.run([sys.executable, str(ROOT / 'scripts/local.py'), 'stop'], check=True, stdout=subprocess.DEVNULL)
    try:
        try:
            connection = psycopg.connect(admin_dsn(ROOT), autocommit=True, connect_timeout=3)
        except psycopg.OperationalError:
            connection = psycopg.connect(admin, autocommit=True, connect_timeout=3)
        with connection:
            database_directory = Path(connection.execute('SHOW data_directory').fetchone()[0])
            expected = Path.home() / '.local/share/avenra/components/database/data'
            if database_directory.resolve() != expected.resolve():
                raise RuntimeError('Refusing to change authentication on an unrelated database installation.')
            backup = ROOT / '.local/backups' / ('database-auth-' + str(time.time_ns()))
            backup.mkdir(parents=True, mode=0o700)
            for name in ['pg_hba.conf', 'postgresql.conf']:
                (backup / name).write_bytes((database_directory / name).read_bytes())
            connection.execute("SET password_encryption='scram-sha-256'")
            connection.execute(sql.SQL('ALTER ROLE avenra PASSWORD {}').format(sql.Literal(credentials['admin'])))
            if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname='avenra_accounting'").fetchone():
                connection.execute('CREATE ROLE avenra_accounting LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION')
            connection.execute(sql.SQL('ALTER ROLE avenra_accounting PASSWORD {}').format(sql.Literal(credentials['accounting'])))
            connection.execute('ALTER DATABASE avenra_ledger OWNER TO avenra_accounting')
        with psycopg.connect(admin.replace('/postgres', '/avenra_ledger')) as connection:
            schemas = [x[0] for x in connection.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname!='information_schema'")]
            for schema in schemas:
                connection.execute(sql.SQL('ALTER SCHEMA {} OWNER TO avenra_accounting').format(sql.Identifier(schema)))
            objects = connection.execute("SELECT n.nspname,c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname!='information_schema' AND c.relkind IN ('r','p','v','m','S','f') ORDER BY CASE WHEN c.relkind='S' THEN 1 ELSE 0 END").fetchall()
            for schema, name, kind in objects:
                object_type = {'v': 'VIEW', 'm': 'MATERIALIZED VIEW', 'S': 'SEQUENCE', 'f': 'FOREIGN TABLE'}.get(kind, 'TABLE')
                connection.execute(sql.SQL('ALTER ' + object_type + ' {} OWNER TO avenra_accounting').format(sql.Identifier(schema, name)))
        for name, value in [('database-admin.dsn', admin), ('accounting.dsn', ledger)]:
            path = data / name
            path.write_text(value + '\n');path.chmod(0o600)
        hba = database_directory / 'pg_hba.conf'
        hba.write_text('# Avenra private installation: authenticated loopback and local connections.\nlocal all all scram-sha-256\nhost all all 127.0.0.1/32 scram-sha-256\nhost all all ::1/128 scram-sha-256\n')
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute('SELECT pg_reload_conf()')
        with psycopg.connect(ledger) as connection:
            if connection.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[0]:
                raise RuntimeError('Accounting runtime still has administrative privileges.')
        pending.rename(data / 'database-auth.recovery.json')
        (ROOT / '.local/database-auth-verification.json').write_text(json.dumps({'passed': True, 'authentication': 'scram-sha-256', 'accounting_superuser': False, 'hba_backup': str(backup), 'existing_control_credentials_retained': True}, indent=2) + '\n')
        print('Private database authentication enforced; accounting runtime owns only its database.')
    finally:
        subprocess.run([sys.executable, str(ROOT / 'scripts/local.py'), 'start'], check=True, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
