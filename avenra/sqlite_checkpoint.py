"""Materialize a consistent SQLite backup as one closed, durable database file."""
from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import stat

from .errors import require


def checkpoint(source, destination, tables=()):
    source, destination = Path(source), Path(destination)
    info = source.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            'CHECKPOINT_SOURCE', 'Checkpoint source must be an owned private regular database.', 409)
    require(all(isinstance(t, str) and re.fullmatch('[a-z][a-z0-9_]{0,100}', t) for t in tables), 'CHECKPOINT_TABLE', 'Use declared receipt table names.', 409)
    # Pre-create privately, refuse existing destinations, and never copy live
    # WAL/SHM bytes. SQLite's backup API includes committed WAL contents.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    counts = {}
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as original, closing(sqlite3.connect(destination, timeout=10)) as copied:
        original.backup(copied)
        # SQLite's connection context manager does not close the connection.
        # Explicit closing plus DELETE mode makes this a standalone snapshot,
        # independent of GC timing and temporary sidecar lifetimes.
        mode = copied.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
        require(mode == 'delete' and copied.execute('PRAGMA quick_check').fetchall() == [('ok',)],
                'CHECKPOINT_INTEGRITY', 'Completed database checkpoint failed structural integrity verification.', 409)
        counts = {table: copied.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0] for table in tables}
    require(not any(destination.with_name(destination.name + suffix).exists() for suffix in ['-wal', '-shm', '-journal']),
            'CHECKPOINT_SIDECAR', 'Completed checkpoint must not depend on transient sidecar files.', 409)
    with destination.open('rb') as saved: os.fsync(saved.fileno())
    descriptor = os.open(destination.parent, os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)
    return counts
