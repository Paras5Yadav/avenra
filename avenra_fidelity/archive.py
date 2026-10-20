"""Authenticated, streaming encryption for completed local recovery snapshots."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import struct
import tarfile
import tempfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from avenra.errors import DomainError, require
from avenra.util import canonical, digest

MAGIC = b'AVENRA-ARCHIVE-1\n'
MANIFEST = 'avenra-archive-manifest.json'
MAXIMUM_BYTES = 20 * 1024**3
MAXIMUM_FILES = 10000


def recovery_key(path, create=False):
    path = Path(path)
    if create:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try: fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError: pass
        else:
            with os.fdopen(fd, 'wb') as output: output.write(secrets.token_bytes(32))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        require(os.fstat(source.fileno()).st_mode & 0o077 == 0, 'KEY_PERMISSIONS', 'Recovery key requires owner-only permissions.', 409)
        key = source.read(33)
    require(len(key) == 32, 'RECOVERY_KEY', 'Use the original 32-byte archive recovery key.', 409)
    return key


class EncryptSink:
    def __init__(self, target, encryptor): self.target, self.encryptor = target, encryptor
    def write(self, value): self.target.write(self.encryptor.update(value));return len(value)


class HashedReader:
    def __init__(self, source): self.source, self.hasher = source, hashlib.sha256()
    def read(self, size=-1):
        value = self.source.read(size);self.hasher.update(value);return value


def encrypt_snapshot(source, destination, key):
    source, destination = Path(source).resolve(), Path(destination)
    require(source.is_dir() and len(key) == 32, 'SNAPSHOT', 'Select a completed recovery snapshot and a 32-byte key.')
    require(not destination.exists() and not destination.is_symlink(), 'ARCHIVE_EXISTS', 'Preserve the existing archive; choose a new output name.', 409)
    entries = sorted(source.rglob('*'))
    require(not any(p.is_symlink() for p in entries), 'SNAPSHOT_SYMLINK', 'Snapshots cannot contain links.', 409)
    files = [p for p in entries if p.is_file()]
    require(0 < len(files) <= MAXIMUM_FILES and sum(p.stat().st_size for p in files) <= MAXIMUM_BYTES, 'SNAPSHOT_CAPACITY', 'Snapshot exceeds the declared archive budget.', 409)
    require(not (source / MANIFEST).exists() and not destination.resolve().is_relative_to(source), 'ARCHIVE_PATH', 'The archive must be outside its source and the manifest name is reserved.')
    nonce = secrets.token_bytes(12)
    header = canonical({'format': 'avenra-encrypted-snapshot/1', 'cipher': 'AES-256-GCM', 'nonce': nonce.hex()}).encode()
    prefix = MAGIC + struct.pack('>I', len(header)) + header
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor();encryptor.authenticate_additional_data(prefix)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.' + secrets.token_hex(8) + '.partial')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    manifest = {'format': 'avenra-snapshot-manifest/1', 'files': []}
    try:
        with os.fdopen(fd, 'wb') as target:
            target.write(prefix)
            with tarfile.open(fileobj=EncryptSink(target, encryptor), mode='w|gz') as archive:
                for path in files:
                    relative = path.relative_to(source).as_posix()
                    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as file:
                        before = os.fstat(file.fileno())
                        info = tarfile.TarInfo(relative);info.size = before.st_size;info.mode = 0o600
                        reader = HashedReader(file);archive.addfile(info, reader)
                        after = os.fstat(file.fileno())
                    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), 'SNAPSHOT_CHANGED', 'Use a completed, quiesced snapshot rather than live database files.', 409)
                    manifest['files'].append({'path': relative, 'size': info.size, 'sha256': reader.hasher.hexdigest()})
                payload = canonical(manifest).encode();info = tarfile.TarInfo(MANIFEST);info.size = len(payload);info.mode = 0o600
                archive.addfile(info, io.BytesIO(payload))
            target.write(encryptor.finalize());target.write(encryptor.tag);target.flush();os.fsync(target.fileno())
        # Hard-link publication refuses a concurrent replacement of the output.
        os.link(temporary, destination);temporary.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True);raise
    return {'archive': str(destination.resolve()), 'files': len(files), 'manifest_hash': digest(manifest), 'cipher': 'AES-256-GCM', 'scope': 'Encrypted local snapshot artifact. Off-host delivery and recovery objectives are not established.'}


def restore_snapshot(source, destination, key):
    destination = Path(destination)
    require(not destination.exists() and not destination.is_symlink(), 'RESTORE_EXISTS', 'Restore into a new empty recovery directory.', 409)
    with Path(source).open('rb') as encrypted, tempfile.TemporaryFile() as clear:
        magic = encrypted.read(len(MAGIC));length = encrypted.read(4)
        require(magic == MAGIC and len(length) == 4, 'ARCHIVE_FORMAT', 'Not an Avenra encrypted snapshot.')
        size = struct.unpack('>I', length)[0]
        require(size <= 4096, 'ARCHIVE_FORMAT', 'Archive header exceeds its budget.')
        raw = encrypted.read(size)
        try: header = json.loads(raw)
        except (ValueError, UnicodeDecodeError): raise DomainError('ARCHIVE_FORMAT', 'Invalid archive header.')
        require(isinstance(header, dict) and set(header) == {'format', 'cipher', 'nonce'} and header['format'] == 'avenra-encrypted-snapshot/1' and header['cipher'] == 'AES-256-GCM' and isinstance(header['nonce'], str) and re.fullmatch('[0-9a-f]{24}', header['nonce']), 'ARCHIVE_FORMAT', 'Unsupported archive encryption profile.')
        offset = encrypted.tell();encrypted.seek(0, 2);end = encrypted.tell()
        require(16 <= end - offset <= MAXIMUM_BYTES + 64 * 1024**2, 'ARCHIVE_CAPACITY', 'Encrypted archive is truncated or exceeds its budget.')
        encrypted.seek(-16, 2);tag = encrypted.read(16);encrypted.seek(offset)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(bytes.fromhex(header['nonce']), tag)).decryptor()
        decryptor.authenticate_additional_data(magic + length + raw)
        remaining = end - offset - 16
        try:
            while remaining:
                chunk = encrypted.read(min(1024**2, remaining))
                require(chunk, 'ARCHIVE_FORMAT', 'Archive ended before its authentication tag.')
                clear.write(decryptor.update(chunk));remaining -= len(chunk)
            clear.write(decryptor.finalize())
        except InvalidTag: raise DomainError('ARCHIVE_AUTHENTICATION', 'Archive or recovery key failed authentication.', 403)
        # No target file is written before the complete GCM tag verifies.
        clear.seek(0);destination.mkdir(mode=0o700, parents=True)
        observed, manifest, total = {}, None, 0
        try:
            with tarfile.open(fileobj=clear, mode='r|gz') as archive:
                for member in archive:
                    path = PurePosixPath(member.name)
                    require(member.isfile() and not path.is_absolute() and '..' not in path.parts and path.parts and '\\' not in member.name, 'ARCHIVE_PATH', 'Archive has an unsafe or nonregular member.', 409)
                    require(member.name not in observed and (member.name != MANIFEST or manifest is None), 'ARCHIVE_PATH', 'Duplicate archive member.', 409)
                    total += member.size
                    require(len(observed) < MAXIMUM_FILES and 0 <= member.size <= MAXIMUM_BYTES and total <= MAXIMUM_BYTES, 'ARCHIVE_CAPACITY', 'Expanded archive exceeds its budget.', 409)
                    item = archive.extractfile(member)
                    if member.name == MANIFEST:
                        require(member.size <= 4 * 1024**2, 'ARCHIVE_CAPACITY', 'Snapshot manifest exceeds its budget.')
                        manifest = json.loads(item.read());continue
                    # parents=True applies mode only to the final directory;
                    # create every archive-controlled ancestor privately.
                    parent = destination
                    for part in path.parts[:-1]:
                        parent = parent / part;parent.mkdir(mode=0o700, exist_ok=True)
                    target = destination.joinpath(*path.parts)
                    hashed = hashlib.sha256()
                    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as output:
                        while chunk := item.read(1024**2): output.write(chunk);hashed.update(chunk)
                    observed[member.name] = {'path': member.name, 'size': member.size, 'sha256': hashed.hexdigest()}
            require(manifest and manifest.get('format') == 'avenra-snapshot-manifest/1' and {r['path']: r for r in manifest['files']} == observed, 'SNAPSHOT_INTEGRITY', 'Restored files differ from the authenticated snapshot manifest.', 409)
        except BaseException:
            # Preserve partial recovery for inspection, but mark it unusable.
            marker = destination / 'RESTORE_FAILED';marker.write_text('Do not launch this incomplete recovery.\n');marker.chmod(0o600)
            raise
    return {'directory': str(destination.resolve()), 'files': len(observed), 'manifest_hash': digest(manifest), 'verified': True, 'scope': 'Local authenticated file restore. Database import and off-host infrastructure qualification are separate.'}
