"""Reviewed control 9 -> 10 migration and the bounded worker error budget.

Claims retain their original lifetime count. Productive deferrals and reclaimed
leases do not spend the error budget. NULL identifies an inherited job whose
historical errors cannot be separated from claims: retain its legacy budget
until it finishes or an explicit domain reconciliation enqueues new work.
"""
import hashlib
from pathlib import Path

from .errors import require
from .util import canonical, now

CORE_VERSION = 10
ERROR_LIMIT = 6


def schema_hash():
    return hashlib.sha256(Path(__file__).with_name('outbox_retry_schema.sql').read_bytes()).hexdigest()


def install(db, postgres, installation=None):
    """The caller owns the DDL transaction and migration identity."""
    for statement in Path(__file__).with_name('outbox_retry_schema.sql').read_text().split(';'):
        if statement.strip():
            db.execute(statement)
    db.execute('INSERT INTO outbox_retry_schema VALUES(?,?,?,?)',
               (1, schema_hash(), now(), canonical(installation or {'mode': 'explicit_local_or_fresh_install', 'actor': 'installation'})))
    for action in ['UPDATE', 'DELETE']:
        if postgres:
            db.execute(f'CREATE TRIGGER outbox_retry_schema_{action.lower()} BEFORE {action} ON outbox_retry_schema FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record()')
        else:
            db.execute(f"CREATE TRIGGER outbox_retry_schema_{action.lower()} BEFORE {action} ON outbox_retry_schema BEGIN SELECT RAISE(ABORT,'Worker migration history is immutable'); END")


def validate(db, code='MIGRATION_REQUIRED'):
    rows = db.execute('SELECT version,source_sha256 FROM outbox_retry_schema').fetchall()
    require(len(rows) == 1 and (rows[0][0], rows[0][1]) == (1, schema_hash()), code,
            'Worker retry schema needs its matching reviewed migration.', 503 if code == 'MIGRATION_REQUIRED' else 409)


def error_number(job):
    # Legacy last_error proves an error occurred, but not how many. Do not
    # invent a count or replenish the inherited job's remaining retry budget.
    used = job['error_attempts']
    return job['attempts'] if used is None else used + 1


def exhausted(job):
    return error_number(job) >= ERROR_LIMIT
