#!/usr/bin/env python3
"""Back up and copy local control state to a dedicated PostgreSQL database."""
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.postgres_store import PostgresStore
from avenra.local_database import admin_dsn
from avenra.util import digest


def main():
    import psycopg
    from psycopg import sql
    directory = ROOT / '.local/data'
    lock = (directory / 'server.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Stop only the Avenra console before migrating its control state.')
    active, pending = directory / 'control.dsn', directory / 'control.pending.dsn'
    if active.exists():
        raise SystemExit('A control connection is already configured; existing state was preserved.')
    backup = ROOT / '.local/backups' / ('control-migration-' + str(int(time.time())))
    backup.mkdir(parents=True, mode=0o700)
    with sqlite3.connect(directory / 'avenra.db') as source, sqlite3.connect(backup / 'avenra.db') as target:
        source.backup(target)
    (backup / 'avenra.db').chmod(0o600)
    source = sqlite3.connect(backup / 'avenra.db')
    if source.execute('PRAGMA user_version').fetchone()[0] not in {6, 7, 8, 9}:
        raise SystemExit('Only verified schema versions 6, 7, 8 and 9 can migrate to the current control schema.')
    database, role = 'avenra_control', 'avenra_control'
    if not pending.exists():
        password = secrets.token_urlsafe(40)
        with psycopg.connect(admin_dsn(ROOT), autocommit=True) as admin:
            if admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone() or admin.execute('SELECT 1 FROM pg_database WHERE datname=%s', (database,)).fetchone():
                raise SystemExit('Control role/database already exists without a migration receipt; refusing to replace it.')
            with pending.open('x') as target:
                pending.chmod(0o600)
                target.write('postgresql://' + role + ':' + quote(password, safe='') + '@127.0.0.1:55437/' + database + '\n')
            admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role), sql.Literal(password)))
            admin.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(database), sql.Identifier(role)))
    store = PostgresStore(directory, pending.read_text().strip())
    tables = [x[0] for x in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    dependencies = {name: {x[2] for x in source.execute('PRAGMA foreign_key_list(' + name + ')') if x[2] != name} for name in tables}
    ordered = []
    while len(ordered) < len(tables):
        ready = [name for name in tables if name not in ordered and dependencies[name] <= set(ordered)]
        if not ready:
            raise RuntimeError('Unresolved schema dependencies require an explicit migration.')
        ordered.extend(ready)
    counts = {}
    with store.pool.connection() as target, target.transaction():
        target.execute('SELECT pg_advisory_xact_lock(781234912)')
        for name in ordered:
            if target.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(name))).fetchone()[0]:
                raise RuntimeError('Target contains existing data; copy was not attempted: ' + name)
        for name in ordered:
            columns = [x[1] for x in source.execute('PRAGMA table_info(' + name + ')')]
            rows = source.execute('SELECT * FROM ' + name + ' ORDER BY rowid').fetchall()
            statement = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(name), sql.SQL(',').join(map(sql.Identifier, columns)), sql.SQL(',').join(sql.Placeholder() for _ in columns))
            with target.cursor() as cursor:
                cursor.executemany(statement, rows)
            counts[name] = len(rows)
            if target.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(name))).fetchone()[0] != len(rows):
                raise RuntimeError('Control copy count mismatch: ' + name)
        for name, column in [('audit_events', 'sequence'), ('callback_attempts', 'id')]:
            maximum = target.execute(sql.SQL('SELECT COALESCE(MAX({}),0) FROM {}').format(sql.Identifier(column), sql.Identifier(name))).fetchone()[0]
            target.execute('SELECT setval(pg_get_serial_sequence(%s,%s),%s,%s)', (name, column, max(1, maximum), maximum > 0))
    audit = store.verify_audit()
    if not audit['valid']:
        raise RuntimeError('Copied audit chain failed verification; connection was not activated.')
    with store.reader() as connection:
        for record in connection.execute('SELECT * FROM evidence'):
            manifest = json.loads(record['manifest_json'])
            actual_hash = digest(manifest)
            signature = hmac.new(store.signing_key, actual_hash.encode(), hashlib.sha256).hexdigest()
            if actual_hash != record['manifest_hash'] or signature != record['signature']:
                raise RuntimeError('Evidence integrity mismatch; connection was not activated.')
    receipt = {'completed_at': time.time(), 'backend': 'postgresql', 'source_backup': str(backup), 'table_counts': counts, 'audit': audit, 'evidence_verified': True}
    (backup / 'migration.json').write_text(json.dumps(receipt, indent=2) + '\n')
    (ROOT / '.local/control-migration.json').write_text(json.dumps(receipt, indent=2) + '\n')
    os.replace(pending, active)
    store.close()
    print(json.dumps({'backend': 'postgresql', 'tables': len(counts), 'records': sum(counts.values()), 'audit_valid': audit['valid'], 'evidence_verified': True, 'backup': str(backup)}))


if __name__ == '__main__':
    main()
