"""Owner-controlled deployment configuration, separate from tenant input."""
import json
import os
import re
from pathlib import Path
import secrets

from .errors import DomainError, require
from .lab_identity import private_read, private_write


def write_document(path, value):
    """Replace an owned private document atomically, preserving mode and framing."""
    path = Path(path)
    if path.exists() or path.is_symlink(): private_read(path)
    temporary = path.with_name('.' + path.name + '.' + secrets.token_hex(12))
    try:
        private_write(temporary, json.dumps(value, separators=(',', ':'), sort_keys=True, allow_nan=False))
        with temporary.open('rb') as stream: os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally:
        if temporary.exists(): temporary.unlink()


def read_document(path, missing=None):
    path = Path(path)
    if not path.exists() and not path.is_symlink(): return missing
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate configuration field')
            result[key] = value
        return result
    try:
        value = json.loads(private_read(path), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeDecodeError):
        raise DomainError('DEPLOYMENT_CONFIGURATION', 'Deployment configuration must be a finite, unique-field JSON object.', 503)
    require(isinstance(value, dict), 'DEPLOYMENT_CONFIGURATION', 'Deployment configuration must be an object.', 503)
    return value


def secret_path(directory, reference, suffix):
    require(isinstance(reference, str) and re.fullmatch(r'secret://[a-zA-Z0-9_-]{1,80}', reference), 'SECRET_REFERENCE', 'Use a named private deployment identity reference.', 503)
    base = Path(directory) / 'deployment-secrets'
    require(not base.is_symlink(), 'SECRET_REFERENCE', 'Deployment identity directory cannot be a link.', 503)
    folder = base / reference[9:]
    require(folder.is_dir() and not folder.is_symlink(), 'SECRET_REFERENCE', 'Deployment identity is unavailable.', 503)
    path = folder / suffix
    private_read(path)
    return path


def deployment(directory):
    from .recovery_state import require_active
    require_active(directory)
    document = read_document(Path(directory) / 'deployment.json', {})
    require(set(document) <= {'format', 'database', 'http', 'identity', 'role', 'transport', 'release', 'recovery'}, 'DEPLOYMENT_CONFIGURATION', 'Unknown deployment configuration fields.', 503)
    if document:
        require(document.get('format') == 'avenra-deployment/1', 'DEPLOYMENT_CONFIGURATION', 'Unsupported deployment configuration version.', 503)
    db = document.get('database', {})
    require(isinstance(db, dict) and set(db) <= {'mode', 'pool_min', 'pool_max', 'pool_timeout_seconds'}, 'DEPLOYMENT_CONFIGURATION', 'Invalid database policy.', 503)
    require(db.get('mode', 'migration') in {'migration', 'runtime'}, 'DEPLOYMENT_CONFIGURATION', 'Choose migration or runtime database access.', 503)
    for name, default, lower, upper in [('pool_min', 1, 0, 8), ('pool_max', 8, 1, 32), ('pool_timeout_seconds', 10, 1, 30)]:
        value = db.get(name, default)
        require(type(value) is int and lower <= value <= upper, 'DEPLOYMENT_CONFIGURATION', 'Database pool policy is outside its bounds.', 503)
    require(db.get('pool_min', 1) <= db.get('pool_max', 8), 'DEPLOYMENT_CONFIGURATION', 'Pool minimum exceeds maximum.', 503)
    http = document.get('http', {})
    require(isinstance(http, dict) and set(http) <= {'tls_identity', 'require_client_certificate', 'dispatch_timeout_seconds', 'max_concurrency'}, 'DEPLOYMENT_CONFIGURATION', 'Invalid API serving policy.', 503)
    require(type(http.get('dispatch_timeout_seconds', 60)) is int and 1 <= http.get('dispatch_timeout_seconds', 60) <= 120, 'DEPLOYMENT_CONFIGURATION', 'HTTP execution deadline is outside its bounds.', 503)
    require(type(http.get('max_concurrency', 32)) is int and 1 <= http.get('max_concurrency', 32) <= 128, 'DEPLOYMENT_CONFIGURATION', 'HTTP concurrency is outside its bounds.', 503)
    require(type(http.get('require_client_certificate', False)) is bool, 'DEPLOYMENT_CONFIGURATION', 'Client certificate setting must be boolean.', 503)
    if 'tls_identity' in http:
        require(isinstance(http['tls_identity'], str) and re.fullmatch('secret://[a-zA-Z0-9_-]{1,80}', http['tls_identity']), 'DEPLOYMENT_CONFIGURATION', 'Use a private TLS identity reference.', 503)
    require(not http.get('require_client_certificate') or http.get('tls_identity'), 'DEPLOYMENT_CONFIGURATION', 'Required client authentication needs a TLS server identity.', 503)
    identity = document.get('identity', {})
    require(isinstance(identity, dict) and set(identity) <= {'granular', 'require_session_subject', 'max_session_seconds'}, 'DEPLOYMENT_CONFIGURATION', 'Invalid institutional identity policy.', 503)
    for name in ['granular', 'require_session_subject']:
        require(type(identity.get(name, False)) is bool, 'DEPLOYMENT_CONFIGURATION', 'Identity enforcement flags must be boolean.', 503)
    require(type(identity.get('max_session_seconds', 3600)) is int and 60 <= identity.get('max_session_seconds', 3600) <= 43200, 'DEPLOYMENT_CONFIGURATION', 'Session lifetime is outside its bounds.', 503)
    require(document.get('role', 'combined') in {'combined', 'api', 'worker'}, 'DEPLOYMENT_CONFIGURATION', 'Unsupported deployment process role.', 503)
    transport = document.get('transport', {})
    require(isinstance(transport, dict) and set(transport) <= {'accounting'}, 'DEPLOYMENT_CONFIGURATION', 'Unknown engine transport.', 503)
    for value in transport.values():
        from urllib.parse import urlsplit
        require(isinstance(value, dict) and set(value) == {'origin', 'identity_reference'}, 'DEPLOYMENT_CONFIGURATION', 'Use an approved engine origin and private identity reference.', 503)
        require(isinstance(value['origin'], str), 'DEPLOYMENT_CONFIGURATION', 'Engine origin must be a string.', 503)
        parsed = urlsplit(value['origin'])
        require(parsed.scheme == 'https' and parsed.hostname and not (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment), 'DEPLOYMENT_CONFIGURATION', 'Deployed engine connections require HTTPS origins.', 503)
        require(isinstance(value['identity_reference'], str) and re.fullmatch('secret://[a-zA-Z0-9_-]{1,80}', value['identity_reference']), 'DEPLOYMENT_CONFIGURATION', 'Use a private engine identity reference.', 503)
    release = document.get('release', {})
    require(isinstance(release, dict) and set(release) <= {'application_fingerprint', 'artifact_id'}, 'DEPLOYMENT_CONFIGURATION', 'Invalid release binding.', 503)
    if release:
        require(isinstance(release.get('application_fingerprint'), str) and re.fullmatch('[0-9a-f]{64}', release['application_fingerprint']), 'DEPLOYMENT_CONFIGURATION', 'Release fingerprint is invalid.', 503)
        if 'artifact_id' in release:
            require(isinstance(release['artifact_id'], str) and re.fullmatch('[0-9a-f]{64}', release['artifact_id']), 'DEPLOYMENT_CONFIGURATION', 'Release artifact identity is invalid.', 503)
            require(db.get('mode') == 'runtime', 'DEPLOYMENT_CONFIGURATION', 'Managed artifact selection requires a runtime database identity.', 503)
    recovery = document.get('recovery', {})
    require(isinstance(recovery, dict) and set(recovery) <= {'set_id', 'bank_routes_sha256','aa_routes_sha256','peer_routes_sha256'} and
            (not recovery or ('set_id' in recovery and len(recovery)>1)) and
            all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) for value in recovery.values()),
            'DEPLOYMENT_CONFIGURATION', 'Invalid authenticated recovery routing policy.', 503)
    if recovery.get('bank_routes_sha256'):
        import hashlib
        path = Path(directory)/'bank-recovery.json'
        require(path.exists() and hashlib.sha256(private_read(path)).hexdigest()==recovery['bank_routes_sha256'],
                'RECOVERY_BANK_BINDING','Activated bank routing is missing or changed; startup remains unavailable.',503)
        from .bank_recovery import load
        require(load(directory)['set_id']==recovery['set_id'],'RECOVERY_BANK_BINDING','Activated bank routing belongs to another recovery set.',503)
    if recovery.get('aa_routes_sha256'):
        import hashlib
        path = Path(directory)/'aa-recovery.json'
        require(path.exists() and hashlib.sha256(private_read(path)).hexdigest()==recovery['aa_routes_sha256'],
                'RECOVERY_AA_BINDING','Activated AA routing is missing or changed; startup remains unavailable.',503)
        from .aa.routing import load
        require(load(directory)['set_id']==recovery['set_id'],'RECOVERY_AA_BINDING','Activated AA routes belong to another recovery set.',503)
    if recovery.get('peer_routes_sha256'):
        import hashlib
        path = Path(directory)/'peer-recovery.json'
        require(path.exists() and hashlib.sha256(private_read(path)).hexdigest()==recovery['peer_routes_sha256'],
                'RECOVERY_PEER_BINDING','Activated participant routes are missing or changed; startup remains unavailable.',503)
        from .peer_recovery import load
        require(load(directory)['set_id']==recovery['set_id'],'RECOVERY_PEER_BINDING','Activated participant routes belong to another recovery set.',503)
    return document
