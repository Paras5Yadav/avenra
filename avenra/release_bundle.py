"""Signed, deterministic application releases; installation state is never input.

Trust is supplied by the operator, not by a public key carried in an archive.
Native engines remain separately deployed, content-pinned dependencies. A bundle
does not upgrade their databases or claim reproducible compilation of upstreams.
"""
import base64
from email.parser import BytesParser
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .errors import DomainError, require
from .lab_identity import private_read, private_write
from .util import canonical, digest

BASE_SCHEMAS = {'avenra_schema_version': 9, 'fidelity_schema': 2, 'runtime_schema': 1, 'aa_schema': 1}
SCHEMAS = {**BASE_SCHEMAS, 'ops_schema': 1}
LIMIT = 512_000_000
MAX_FILE = 128_000_000
ROOT_FILES = {'requirements.txt', 'README.md', 'VERIFICATION.md', 'BANKING_IMPLEMENTATION.md', 'AGENTS.md', 'API_REFERENCE.md', 'OPERATIONS.md'}
SOURCE_SUFFIXES = {'.py', '.sql', '.js', '.cjs', '.css', '.html', '.svg', '.json', '.go'}


def sha(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def runtime_target():
    return {'implementation': platform.python_implementation(), 'python': list(sys.version_info[:3]),
            'platform': sysconfig.get_platform()}


def parse(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: raise ValueError('Duplicate release field')
            value[key] = item
        return value
    try: return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeDecodeError): raise DomainError('RELEASE_FORMAT', 'Release metadata is not unique-field finite JSON.', 409)


def name(value):
    require(isinstance(value, str) and 0 < len(value) <= 240 and '\\' not in value and '\x00' not in value,
            'RELEASE_PATH', 'Release paths must be bounded relative POSIX paths.', 409)
    path = PurePosixPath(value)
    require(not path.is_absolute() and all(p not in {'', '.', '..'} for p in value.split('/')) and path.as_posix() == value,
            'RELEASE_PATH', 'Release paths cannot escape or alias the payload.', 409)
    return path


def regular(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_FILE, 'RELEASE_INPUT', 'Release inputs must be bounded regular files, without links.', 409)


def source_inventory(root):
    root = Path(root).resolve();paths = []
    for prefix in ['avenra', 'avenra_fidelity', 'scripts', 'docs', 'LICENSES']:
        folder = root / prefix
        require(folder.is_dir() and not folder.is_symlink(), 'RELEASE_INPUT', 'Required source and notice directories must be ordinary directories.', 409)
        for path in sorted(folder.rglob('*')):
            require(not path.is_symlink(), 'RELEASE_INPUT', 'Source inputs cannot contain links.', 409)
            if path.is_file() and '__pycache__' not in path.parts:
                if prefix in {'LICENSES', 'docs'} or path.suffix in SOURCE_SUFFIXES:
                    regular(path);paths.append(path)
    for filename in sorted(ROOT_FILES):
        path = root / filename
        if path.exists(): regular(path);paths.append(path)
    return {p.relative_to(root).as_posix(): p for p in paths}


def fingerprint(files):
    value = hashlib.sha256();value.update(b'requirements.txt\0' + files['requirements.txt'] + b'\0')
    for prefix in ['avenra', 'avenra_fidelity']:
        for path in sorted(files):
            if path.startswith(prefix + '/') and PurePosixPath(path).suffix in {'.py', '.sql', '.js', '.css', '.html'}:
                value.update(path.encode() + b'\0' + files[path] + b'\0')
    return value.hexdigest()


def wheel_lock(directory):
    files, packages = {}, {}
    for path in sorted(Path(directory).iterdir()):
        regular(path)
        require(path.suffix == '.whl', 'RELEASE_WHEELS', 'The offline dependency directory accepts wheels only.', 409)
        with zipfile.ZipFile(path) as archive:
            metadata = [x for x in archive.infolist() if x.filename.endswith('.dist-info/METADATA')]
            require(len(metadata) == 1 and metadata[0].file_size < 1_000_000, 'RELEASE_WHEELS', 'Wheel metadata is missing or ambiguous.', 409)
            value = BytesParser().parsebytes(archive.read(metadata[0]))
        package = re.sub(r'[-_.]+', '-', value.get('Name', '')).lower();version = value.get('Version', '')
        require(re.fullmatch('[a-z0-9][a-z0-9-]*', package) and re.fullmatch('[A-Za-z0-9.+!_-]+', version) and package not in packages,
                'RELEASE_WHEELS', 'Include one unambiguous pinned wheel per distribution.', 409)
        packages[package] = {'name': package, 'version': version, 'sha256': sha(path), 'wheel': path.name}
        files['wheels/' + path.name] = path
    require(0 < len(files) <= 128, 'RELEASE_WHEELS', 'A bounded complete wheel set is required.', 409)
    lock = ''.join(f"{key}=={packages[key]['version']} --hash=sha256:{packages[key]['sha256']}\n" for key in sorted(packages))
    return files, [packages[p] for p in sorted(packages)], lock.encode()


def new_key(private_path, public_path):
    key = Ed25519PrivateKey.generate()
    private_write(private_path, key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    private_write(public_path, public)
    return {'key_id': hashlib.sha256(public).hexdigest(), 'public_key_file': str(public_path)}


def build(root, wheel_directory, private_key_file, target, label):
    require(re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,79}', label or ''), 'RELEASE_LABEL', 'Use a bounded release label.', 409)
    root, target = Path(root).resolve(), Path(target)
    require(not target.exists() and not target.is_symlink(), 'RELEASE_EXISTS', 'Preserve existing release artifacts.', 409)
    files = source_inventory(root);wheels, packages, lock = wheel_lock(wheel_directory);files.update(wheels)
    # No recursive .local inclusion: only explicit public binary hashes enter the
    # dependency inventory. Credentials and live state can never be selected.
    engines = {}
    for role in ['accounting', 'fault']:
        binary = root / '.local/bin' / role
        if binary.exists(): regular(binary);engines[role] = {'sha256': sha(binary), 'deployment': 'external_service'}
    contents = {path: source.read_bytes() for path, source in files.items()}
    contents['requirements.lock'] = lock
    require(len(contents) <= 4096 and sum(map(len, contents.values())) <= LIMIT, 'RELEASE_LIMIT', 'Release exceeds the supported application bundle bounds.', 409)
    key = Ed25519PrivateKey.from_private_bytes(private_read(private_key_file))
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    manifest = {'format': 'avenra-application-release/1', 'label': label, 'target': runtime_target(),
                'application_fingerprint': fingerprint(contents), 'schemas': SCHEMAS, 'migration_policy': 'schema_preserving',
                'signing_key_id': hashlib.sha256(public).hexdigest(), 'dependencies': packages, 'native_services': engines,
                'files': [{'path': path, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'mode': 0o444} for path, data in sorted(contents.items())]}
    raw = canonical(manifest).encode();signature = base64.b64encode(key.sign(raw))
    # No wall clock, paths, host names, UID/GID or random tar metadata.
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        with gzip.GzipFile(filename='', mode='wb', fileobj=output, mtime=0) as zipped, tarfile.open(fileobj=zipped, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for path, data in [('manifest.json', raw), ('manifest.sig', signature), *sorted(contents.items())]:
                entry = tarfile.TarInfo(path);entry.size = len(data);entry.mode = 0o444;entry.mtime = 0
                archive.addfile(entry, io.BytesIO(data))
        output.flush();os.fsync(output.fileno())
    return {'artifact_id': hashlib.sha256(raw).hexdigest(), 'archive_sha256': sha(target), 'application_fingerprint': manifest['application_fingerprint'],
            'files': len(contents), 'dependencies': len(packages), 'path': str(target)}


def validate_manifest(raw, signature, trusted_public):
    require(len(raw) < 2_000_000 and len(signature) <= 256, 'RELEASE_LIMIT', 'Release manifest exceeds its bounds.', 409)
    try: Ed25519PublicKey.from_public_bytes(trusted_public).verify(base64.b64decode(signature, validate=True), raw)
    except (ValueError, InvalidSignature): raise DomainError('RELEASE_SIGNATURE', 'Release signature does not match the externally trusted key.', 409)
    value = parse(raw)
    require(isinstance(value, dict) and value.get('format') == 'avenra-application-release/1' and canonical(value).encode() == raw,
            'RELEASE_FORMAT', 'Unsupported or noncanonical release metadata.', 409)
    require(value.get('signing_key_id') == hashlib.sha256(trusted_public).hexdigest(), 'RELEASE_SIGNATURE', 'Release key identity is inconsistent.', 409)
    require(value.get('target') == runtime_target(), 'RELEASE_TARGET', 'Release requires its exact qualified interpreter and platform.', 409)
    require(value.get('schemas') in [BASE_SCHEMAS,SCHEMAS] and value.get('migration_policy') == 'schema_preserving', 'RELEASE_SCHEMA', 'This installer accepts the declared compatible schema inventories only.', 409)
    rows = value.get('files');require(isinstance(rows, list) and 0 < len(rows) <= 4096, 'RELEASE_FORMAT', 'Release inventory is missing or excessive.', 409)
    inventory = {}
    for item in rows:
        require(isinstance(item, dict) and set(item) == {'path', 'bytes', 'sha256', 'mode'}, 'RELEASE_FORMAT', 'Invalid release file declaration.', 409)
        path = name(item['path']).as_posix()
        require(path not in {'manifest.json', 'manifest.sig'} and path not in inventory and type(item['bytes']) is int and 0 <= item['bytes'] <= MAX_FILE and
                re.fullmatch('[a-f0-9]{64}', item['sha256']) and item['mode'] == 0o444, 'RELEASE_FORMAT', 'Invalid or duplicate release file.', 409)
        inventory[path] = item
    require(sum(x['bytes'] for x in rows) <= LIMIT, 'RELEASE_LIMIT', 'Release uncompressed size exceeds its bounds.', 409)
    return value, inventory


def archive_files(path):
    with Path(path).open('rb') as source, tarfile.open(fileobj=source, mode='r|gz') as archive:
        count = 0
        for member in archive:
            count += 1;name(member.name)
            require(member.isfile() and not member.pax_headers and count <= 4098 and 0 <= member.size <= MAX_FILE,
                    'RELEASE_ARCHIVE', 'Release archive contains an unsupported entry or exceeds bounds.', 409)
            stream = archive.extractfile(member);data = stream.read(member.size + 1)
            require(len(data) == member.size, 'RELEASE_ARCHIVE', 'Release member is truncated.', 409)
            yield member.name, data


def inspect(path, trusted_public_file):
    trusted = private_read(trusted_public_file);iterator = iter(archive_files(path))
    try:
        first, raw = next(iterator);second, signature = next(iterator)
    except StopIteration: raise DomainError('RELEASE_ARCHIVE', 'Release metadata is missing.', 409)
    require((first, second) == ('manifest.json', 'manifest.sig'), 'RELEASE_ARCHIVE', 'Signed metadata must precede the payload.', 409)
    value, inventory = validate_manifest(raw, signature, trusted);seen = set();sources = {}
    for relative, data in iterator:
        require(relative in inventory and relative not in seen, 'RELEASE_ARCHIVE', 'Release payload contains extra or duplicate files.', 409)
        item = inventory[relative];require(len(data) == item['bytes'] and hashlib.sha256(data).hexdigest() == item['sha256'], 'RELEASE_INTEGRITY', 'Release payload differs from the signed inventory.', 409)
        seen.add(relative)
        if relative.startswith(('avenra/', 'avenra_fidelity/')) or relative == 'requirements.txt': sources[relative] = data
    require(seen == set(inventory), 'RELEASE_ARCHIVE', 'Release payload is incomplete.', 409)
    require(fingerprint(sources) == value['application_fingerprint'], 'RELEASE_INTEGRITY', 'Application fingerprint differs from signed source files.', 409)
    return {'artifact_id': hashlib.sha256(raw).hexdigest(), 'archive_sha256': sha(path), 'manifest': value}


def install(path, trusted_public_file, store):
    verified = inspect(path, trusted_public_file);store = Path(store).resolve();store.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = store / verified['artifact_id']
    require(not destination.exists() and not destination.is_symlink(), 'RELEASE_EXISTS', 'This artifact is already installed; verify it without overwriting.', 409)
    temporary = Path(tempfile.mkdtemp(prefix='.install-', dir=store))
    try:
        # Reverify each byte during extraction. A replaced input cannot smuggle
        # changes between signature validation and filesystem materialization.
        expected = {x['path']: x for x in verified['manifest']['files']};seen = set()
        for relative, data in archive_files(path):
            if relative in {'manifest.json', 'manifest.sig'}:
                if relative == 'manifest.json': require(hashlib.sha256(data).hexdigest() == verified['artifact_id'], 'RELEASE_INTEGRITY', 'Release changed during installation.', 409)
            else:
                require(relative in expected and relative not in seen and len(data) == expected[relative]['bytes'] and hashlib.sha256(data).hexdigest() == expected[relative]['sha256'], 'RELEASE_INTEGRITY', 'Release changed during installation.', 409)
                seen.add(relative)
            target = temporary / relative;target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            private_write(target, data);target.chmod(0o444)
        require(seen == set(expected), 'RELEASE_INTEGRITY', 'Release changed during installation.', 409)
        for folder in sorted([p for p in temporary.rglob('*') if p.is_dir()], reverse=True): folder.chmod(0o555)
        # macOS requires the renamed directory itself to remain writable during
        # rename. Keep the private staging root writable until publication.
        os.rename(temporary, destination);destination.chmod(0o555)
        descriptor = os.open(store, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    except BaseException:
        cleanup = temporary if temporary.exists() else destination
        for folder in [cleanup, *cleanup.rglob('*')]:
            if folder.is_dir(): folder.chmod(0o700)
        shutil.rmtree(cleanup);raise
    return {**verified, 'directory': str(destination)}


def verify_installed(directory, trusted_public_file):
    root = Path(directory)
    require(root.is_dir() and not root.is_symlink(), 'RELEASE_INPUT', 'Installed release root cannot be a link.', 409)
    raw = (root / 'manifest.json').read_bytes();signature = (root / 'manifest.sig').read_bytes()
    value, inventory = validate_manifest(raw, signature, private_read(trusted_public_file));seen = set()
    for path in root.rglob('*'):
        require(not path.is_symlink(), 'RELEASE_INTEGRITY', 'Installed release contains a link.', 409)
        if path.is_dir(): continue
        relative = path.relative_to(root).as_posix();regular(path)
        require(not path.stat().st_mode & 0o222, 'RELEASE_INTEGRITY', 'Installed release payload must be read-only.', 409)
        if relative in {'manifest.json', 'manifest.sig'}: continue
        require(relative in inventory and sha(path) == inventory[relative]['sha256'] and path.stat().st_size == inventory[relative]['bytes'], 'RELEASE_INTEGRITY', 'Installed release changed or contains extra files.', 409)
        seen.add(relative)
    require(seen == set(inventory), 'RELEASE_INTEGRITY', 'Installed release is incomplete.', 409)
    return {'artifact_id': hashlib.sha256(raw).hexdigest(), 'manifest': value, 'directory': str(root.resolve())}


def prepare_runtime(directory, trusted_public_file, runtime_directory):
    verified = verify_installed(directory, trusted_public_file);root = Path(directory).resolve();runtime = Path(runtime_directory).resolve()
    require(not runtime.exists() and not runtime.is_symlink(), 'RELEASE_EXISTS', 'Prepare a new private runtime; preserve existing runtimes.', 409)
    # No registry/network lookup, source builds or dependency selection at deploy.
    import venv
    venv.EnvBuilder(with_pip=True, symlinks=True).create(runtime)
    log = runtime / 'installation.log'
    commands = [[str(runtime / 'bin/python'), '-I', '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'install', '--no-compile', '--no-index', '--no-cache-dir', '--only-binary=:all:', '--require-hashes', '--find-links', str(root / 'wheels'), '-r', str(root / 'requirements.lock'), '-r', str(root / 'requirements.txt')],
                [str(runtime / 'bin/python'), '-I', '-B', '-m', 'pip', '--isolated', 'check']]
    with log.open('xb') as output:
        log.chmod(0o600)
        for command in commands:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, env={k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', 'PIP_'))}, cwd=runtime)
            require(result.returncode == 0, 'RELEASE_DEPENDENCIES', 'Offline runtime installation failed; inspect its private installation log.', 409)
    # ensurepip may have compiled bytecode before -B was in force. Keep no
    # unrecorded cache that Python could prefer over the verified source files.
    for cache in runtime.rglob('__pycache__'):
        if cache.is_dir(): shutil.rmtree(cache)
    private_write(runtime / 'release-runtime.json', canonical({'artifact_id': verified['artifact_id'], 'target': runtime_target(), 'lock_sha256': sha(root / 'requirements.lock'), 'files': runtime_inventory(runtime)}))
    return {'artifact_id': verified['artifact_id'], 'runtime_directory': str(runtime.resolve()), 'offline': True}


def runtime_inventory(directory):
    directory = Path(directory);entries = []
    require(directory.is_dir() and not directory.is_symlink(), 'RELEASE_RUNTIME', 'Runtime root cannot be a link.', 409)
    for path in sorted(directory.rglob('*')):
        relative = path.relative_to(directory).as_posix()
        if relative in {'installation.log', 'release-runtime.json'}: continue
        if path.is_symlink():
            # venv can generate additional interpreter aliases (including a
            # Unicode alias on Python 3.14). Only aliases directly under bin/
            # that resolve to this exact interpreter are accepted and pinned.
            require(len(PurePosixPath(relative).parts) == 2 and relative.startswith('bin/') and path.resolve() == Path(sys.executable).resolve(), 'RELEASE_RUNTIME', 'Unexpected runtime symlink or interpreter.', 409)
            entries.append({'path': relative, 'link': os.readlink(path), 'resolved_sha256': sha(path.resolve())})
        elif path.is_dir(): continue
        else:
            regular(path);entries.append({'path': relative, 'bytes': path.stat().st_size, 'sha256': sha(path)})
    require(0 < len(entries) < 10000, 'RELEASE_RUNTIME', 'Runtime file inventory exceeds the supported bounds.', 409)
    return entries
