#!/usr/bin/env python3
"""Observe committed WAL capture without transient files or source mutation."""
from contextlib import closing
import gc
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.errors import DomainError
from avenra.private_config import write_document
from avenra.sqlite_checkpoint import checkpoint
from avenra.util import application_fingerprint, uid


def case_pack_hash(): return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def main():
    directory = ROOT / '.local/qa' / uid('checkpoint');directory.mkdir(parents=True, mode=0o700)
    report = {'passed': False, 'checks': [], 'directory': str(directory), 'created_at': time.time(), 'application_fingerprint': application_fingerprint(), 'case_pack_sha256': case_pack_hash(),
              'scope': 'Actual live SQLite WAL, uncommitted writer, standalone immutable-reader verification and source preservation. Native bank/issuer/AA recovery is separately exercised by integration suites.'}
    def check(name, condition):
        report['checks'].append({'name': name, 'passed': bool(condition)})
        if not condition: raise AssertionError(name)
    try:
        source, target = directory / 'live.db', directory / 'checkpoint.db'
        source.touch(mode=0o600)
        with closing(sqlite3.connect(source)) as writer:
            writer.execute('PRAGMA journal_mode=WAL');writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute('CREATE TABLE receipts(id TEXT PRIMARY KEY,payload BLOB NOT NULL)');writer.commit()
            writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            rows = [('first', bytes(range(256)) * 8), ('second', b'confirmed original effect'), ('third', b'acknowledgement lost')]
            writer.executemany('INSERT INTO receipts VALUES(?,?)', rows);writer.commit()
            with closing(sqlite3.connect(source.as_uri() + '?mode=ro&immutable=1', uri=True)) as uncheckpointed:
                check('Committed receipt rows really exist in WAL rather than the main database file', uncheckpointed.execute('SELECT count(*) FROM receipts').fetchone()[0] == 0 and source.with_name('live.db-wal').stat().st_size > 0)
            writer.execute('BEGIN IMMEDIATE');writer.execute('INSERT INTO receipts VALUES(?,?)', ('uncommitted', b'must not escape'))
            counts = checkpoint(source, target, ['receipts'])
            check('Live backup includes all committed receipts and excludes the concurrent uncommitted receipt', counts == {'receipts': 3})
            with closing(sqlite3.connect(target.as_uri() + '?mode=ro&immutable=1', uri=True)) as observer:
                check('An immutable reader recovers exact receipt bytes without using WAL or shared memory', observer.execute('SELECT id,payload FROM receipts ORDER BY id').fetchall() == sorted(rows))
                check('The completed checkpoint has structural integrity and standalone journal mode', observer.execute('PRAGMA quick_check').fetchall() == [('ok',)] and observer.execute('PRAGMA journal_mode').fetchone()[0] == 'delete')
            before = hashlib.sha256(target.read_bytes()).hexdigest();gc.collect()
            check('Garbage collection cannot remove a required checkpoint file or change its retained bytes', before == hashlib.sha256(target.read_bytes()).hexdigest() and not any(target.with_name(target.name + suffix).exists() for suffix in ['-wal', '-shm', '-journal']))
            check('Snapshot creation leaves the original writer transaction and WAL mode intact', writer.in_transaction and writer.execute('PRAGMA journal_mode').fetchone()[0] == 'wal' and writer.execute('SELECT count(*) FROM receipts').fetchone()[0] == 4)
            writer.rollback()
            check('Rolling back the original writer does not alter the three committed snapshot receipts', writer.execute('SELECT count(*) FROM receipts').fetchone()[0] == 3 and hashlib.sha256(target.read_bytes()).hexdigest() == before)
            rejected = False
            try: checkpoint(source, target, ['receipts'])
            except FileExistsError: rejected = True
            check('An existing checkpoint cannot be overwritten', rejected and hashlib.sha256(target.read_bytes()).hexdigest() == before)
            link = directory / 'source-link.db';link.symlink_to(source)
            rejected = False
            try: checkpoint(link, directory / 'linked-output.db')
            except DomainError as error: rejected = error.code == 'CHECKPOINT_SOURCE'
            check('A linked source is rejected before creating an output', rejected and not (directory / 'linked-output.db').exists())
        check('Durable checkpoint permissions remain private after all source connections close', target.stat().st_mode & 0o077 == 0)
        check('The checkpoint qualification build and case pack remain fixed', report['application_fingerprint'] == application_fingerprint() and report['case_pack_sha256'] == case_pack_hash())
        report['passed'] = True
    except Exception as error: report.update(error=str(error), traceback=traceback.format_exc())
    report['completed_at'] = time.time();write_document(directory / 'verification.json', report);write_document(ROOT / '.local/checkpoint-verification.json', report)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'report': str(ROOT / '.local/checkpoint-verification.json'), 'error': report.get('error')}))
    if not report['passed']: print(report.get('traceback', ''))
    return 0 if report['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
