"""Private deployment recovery files, restored into an inactive staging folder."""
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import stat

from .errors import require
from .private_config import read_document, write_document

FILES = {'deployment.json', 'identity.json', 'gateway.json', 'control.dsn', 'migration.dsn', 'release-selection.json', 'release-pending.json', 'engine-endpoints.json'}


def regular(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not (info.st_mode & 0o077) and info.st_size <= 32_000_000, 'RECOVERY_FILE', 'Recovery inputs must be private bounded regular files.', 409)


def sha256(path):
    with path.open('rb') as stream:
        value = hashlib.file_digest(stream, 'sha256').hexdigest()
    return value


def snapshot(directory, destination):
    directory, destination = Path(directory), Path(destination)
    require(not destination.exists() and not destination.is_symlink(), 'RECOVERY_EXISTS', 'Preserve existing recovery snapshots.', 409)
    destination.mkdir(mode=0o700, parents=True)
    sources = [directory / name for name in FILES if (directory / name).exists() or (directory / name).is_symlink()]
    secrets = directory / 'deployment-secrets'
    if secrets.exists() or secrets.is_symlink():
        require(secrets.is_dir() and not secrets.is_symlink(), 'RECOVERY_FILE', 'Deployment secret root cannot be a link.', 409)
        for folder in secrets.iterdir():
            require(folder.is_dir() and not folder.is_symlink(), 'RECOVERY_FILE', 'Deployment identity folders cannot be links.', 409)
            sources.extend(p for p in folder.iterdir() if not p.name.endswith(('-wal', '-shm', '-journal')))
    require(len(sources) <= 256, 'RECOVERY_LIMIT', 'Deployment recovery file count exceeds this profile.', 409)
    entries = []
    for source in sorted(sources):
        regular(source);relative = source.relative_to(directory)
        target = destination / relative;target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if source.suffix == '.db':
            # SQLite's backup API captures a consistent issuer state, including
            # committed WAL content, rather than copying a live database file.
            from .sqlite_checkpoint import checkpoint
            checkpoint(source, target)
        else:
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'rb') as original, target.open('xb') as copied: shutil.copyfileobj(original, copied)
            target.chmod(0o600)
        entries.append({'path': relative.as_posix(), 'sha256': sha256(target), 'bytes': target.stat().st_size})
    manifest = {'format': 'avenra-deployment-recovery/1', 'activation': 'inactive_staging_only', 'files': entries}
    write_document(destination / 'manifest.json', manifest)
    return {'files': len(entries), 'sha256': sha256(destination / 'manifest.json')}


def restore(snapshot_directory, destination):
    source, destination = Path(snapshot_directory), Path(destination)
    require(not destination.exists() and not destination.is_symlink(), 'RECOVERY_EXISTS', 'Restore into a new inactive staging folder.', 409)
    manifest = read_document(source / 'manifest.json')
    require(manifest and manifest.get('format') == 'avenra-deployment-recovery/1' and manifest.get('activation') == 'inactive_staging_only' and isinstance(manifest.get('files'), list) and len(manifest['files']) <= 256, 'RECOVERY_MANIFEST', 'Unsupported deployment recovery manifest.', 409)
    seen = set()
    for entry in manifest['files']:
        relative = Path(entry['path'])
        require(not relative.is_absolute() and '..' not in relative.parts and str(relative) not in seen and (relative.as_posix() in FILES or (len(relative.parts) == 3 and relative.parts[0] == 'deployment-secrets')), 'RECOVERY_MANIFEST', 'Recovery path is outside the declared deployment profile.', 409)
        seen.add(str(relative))
        current = source / relative
        require(all(not p.is_symlink() for p in [source, *current.parents[:len(relative.parts)-1]]), 'RECOVERY_FILE', 'Recovery parents cannot be links.', 409)
        regular(current)
        require(current.stat().st_size == entry['bytes'] and sha256(current) == entry['sha256'], 'RECOVERY_INTEGRITY', 'Deployment recovery file failed integrity verification.', 409)
    destination.mkdir(mode=0o700, parents=True)
    from .recovery_state import INACTIVE
    write_document(destination / INACTIVE, {'format':'avenra-recovery-inactive/1','state':'private_files_only','manifest_sha256':sha256(source / 'manifest.json')})
    for entry in manifest['files']:
        relative = Path(entry['path']);target = destination / relative;target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target, follow_symlinks=False);target.chmod(0o600)
    return {'valid': True, 'files': len(seen), 'activation': 'inactive_staging_only'}
