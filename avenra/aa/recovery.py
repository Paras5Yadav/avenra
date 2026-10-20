"""Consistent private AA-role checkpoints, restored inactive by default."""
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import stat

from ..errors import require
from ..private_config import read_document, write_document


def hashed(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def private_parents(root, destination):
    current=Path(root)
    for part in Path(destination).relative_to(root).parts:
        current=current/part;current.mkdir(mode=0o700,exist_ok=True)


def regular(path):
    info=path.lstat();require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077 and info.st_size<=32_000_000,'AA_RECOVERY_FILE','Role recovery inputs must be private bounded regular files.',409)


def snapshot(directory, destination):
    root=Path(directory)/'aa-scopes';destination=Path(destination)
    require(not destination.exists(),'AA_RECOVERY_EXISTS','Preserve previous role snapshots.',409);destination.mkdir(mode=0o700,parents=True)
    entries=[]
    if root.exists():
        require(not root.is_symlink(),'AA_RECOVERY_FILE','Role scope root cannot be a link.',409)
        for source in sorted(root.rglob('*')):
            require(not source.is_symlink(),'AA_RECOVERY_FILE','Role snapshots cannot contain links.',409)
            if source.is_dir() or source.name in {'.service.lock','recovery-process.json'} or source.name.endswith(('.log','-wal','-shm','-journal')):continue
            regular(source);relative=source.relative_to(root);target=destination/'files'/relative;private_parents(destination,target.parent)
            if source.suffix=='.db':
                from ..sqlite_checkpoint import checkpoint
                checkpoint(source, target)
            else:
                fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
                with os.fdopen(fd,'rb') as original,target.open('xb') as copied:shutil.copyfileobj(original,copied)
            target.chmod(0o600);entries.append({'path':relative.as_posix(),'sha256':hashed(target),'bytes':target.stat().st_size})
            require(len(entries)<=2000,'AA_RECOVERY_LIMIT','Role snapshot file budget exceeded.',409)
    manifest={'format':'avenra-aa-recovery/1','activation':'inactive','files':entries};write_document(destination/'manifest.json',manifest)
    return {'files':len(entries),'role_databases':sum(x['path'].endswith('/role.db') for x in entries),'manifest_sha256':hashed(destination/'manifest.json')}


def restore(source, destination):
    source,destination=Path(source),Path(destination);manifest=read_document(source/'manifest.json')
    require(manifest and manifest.get('format')=='avenra-aa-recovery/1' and manifest.get('activation')=='inactive' and len(manifest.get('files',[]))<=2000,'AA_RECOVERY_MANIFEST','Unknown role recovery profile.',409)
    require(not destination.exists(),'AA_RECOVERY_EXISTS','Restore into a new inactive staging folder.',409)
    seen=set()
    for entry in manifest['files']:
        relative=Path(entry['path']);require(not relative.is_absolute() and '..' not in relative.parts and relative.as_posix() not in seen,'AA_RECOVERY_MANIFEST','Invalid role recovery path.',409);seen.add(relative.as_posix())
        path=source/'files'/relative;require(all(not p.is_symlink() for p in [source,*path.parents[:len(relative.parts)]]),'AA_RECOVERY_FILE','Role recovery parents cannot be links.',409);regular(path)
        require(path.stat().st_size==entry['bytes'] and hashed(path)==entry['sha256'],'AA_RECOVERY_INTEGRITY','Role checkpoint failed integrity verification.',409)
    destination.mkdir(mode=0o700,parents=True)
    for entry in manifest['files']:
        target=destination/'aa-scopes'/entry['path']
        private_parents(destination,target.parent)
        shutil.copyfile(source/'files'/entry['path'],target,follow_symlinks=False);target.chmod(0o600)
    write_document(destination/'aa-recovery-inactive.json',{'format':'avenra-aa-recovery/1','activation':'inactive','manifest_sha256':hashed(source/'manifest.json')})
    return {'valid':True,'files':len(seen),'activation':'inactive'}
