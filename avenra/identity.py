"""Institutional OIDC code/PKCE login and strictly scoped token verification."""
import base64
import hashlib
import json
import math
import re
import secrets
import ssl
import threading
import time
from urllib.parse import urlencode, urlsplit

from .errors import DomainError, require
from .services import Transport
from .util import digest


class Identity:
    def __init__(self, app):
        self.app = app
        self._clients, self._lock = {}, threading.Lock()

    def configuration(self):
        from .private_config import read_document
        path = self.app.store.directory / 'identity.json'
        config = read_document(path)
        if config is None:
            return {'mode': 'local'}
        require(config.get('mode') in {'local', 'institutional'}, 'IDENTITY_CONFIGURATION', 'Unknown identity mode.', 503)
        if config['mode'] == 'institutional':
            for field in ['issuer', 'jwks_url', 'authorization_endpoint', 'token_endpoint', 'external_origin']:
                parsed = urlsplit(config.get(field, ''))
                require(parsed.hostname and not (parsed.username or parsed.password or parsed.fragment) and (parsed.scheme == 'https' or (config.get('local_identity_test') is True and parsed.scheme == 'http' and parsed.hostname == '127.0.0.1')), 'IDENTITY_CONFIGURATION', 'Institutional identity requires approved HTTPS endpoints.', 503)
            require(config.get('audience') and config.get('client_id'), 'IDENTITY_CONFIGURATION', 'Identity client and API audience are required.', 503)
        return config

    def verify(self, token, nonce=None):
        import jwt
        config = self.configuration()
        require(config['mode'] == 'institutional', 'UNAUTHENTICATED', 'Institutional identity is not configured.', 401)
        require(isinstance(token, str) and len(token) <= 16000, 'UNAUTHENTICATED', 'Token exceeds the supported identity profile.', 401)
        fingerprint = digest({k: config.get(k) for k in ['issuer', 'jwks_url', 'ca_file']})
        with self._lock:
            client = self._clients.get(fingerprint)
            if client is None:
                context = ssl.create_default_context(cafile=config.get('ca_file'))
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                client = jwt.PyJWKClient(config['jwks_url'], cache_jwk_set=True, lifespan=300, timeout=3, ssl_context=context)
                self._clients = {fingerprint: client}
        try:
            header = jwt.get_unverified_header(token)
            require(header.get('alg') == 'RS256' and isinstance(header.get('kid'), str) and not any(k in header for k in ['jku', 'jwk', 'x5u']), 'UNAUTHENTICATED', 'Unsupported token signing profile.', 401)
            key = client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=['RS256'], issuer=config['issuer'], audience=config['client_id'] if nonce else config['audience'], options={'require': ['exp', 'iat', 'iss', 'aud', 'sub']}, leeway=10)
            if nonce:
                require(claims.get('nonce') == nonce, 'UNAUTHENTICATED', 'Login nonce does not match.', 401)
                if 'azp' in claims or (isinstance(claims.get('aud'), list) and len(claims['aud']) > 1):
                    require(claims.get('azp') == config['client_id'], 'UNAUTHENTICATED', 'ID token authorized party does not match.', 401)
            tenant = claims.get(config.get('tenant_claim', 'avenra_tenant'))
            permissions = claims.get(config.get('permissions_claim', 'avenra_permissions'))
            require(isinstance(tenant, str) and re.fullmatch('[A-Za-z0-9_]{1,80}', tenant), 'FORBIDDEN', 'Token has no valid workspace binding.', 403)
            require(isinstance(permissions, list) and permissions and all(isinstance(p, str) and p in {'read', 'write', 'admin'} for p in permissions), 'FORBIDDEN', 'Token has no supported permissions.', 403)
            with self.app.store.reader() as db:
                require(db.execute('SELECT 1 FROM tenants WHERE id=?', (tenant,)).fetchone(), 'FORBIDDEN', 'Identity workspace is not provisioned.', 403)
            from .production import capabilities
            allowed = capabilities(claims.get(config.get('capabilities_claim', 'avenra_capabilities'), []))
            authenticated = claims.get('auth_time', claims['iat'])
            require(type(authenticated) in {int, float} and math.isfinite(authenticated) and 0 < authenticated <= time.time() + 10, 'UNAUTHENTICATED', 'Authentication time is invalid.', 401)
            return {'tenant_id': tenant, 'permissions': permissions, 'expires_at': claims['exp'], 'subject_hash': hashlib.sha256(claims['sub'].encode()).hexdigest(),
                    'principal': 'oidc:' + hashlib.sha256((config['issuer'] + '\0' + claims['sub']).encode()).hexdigest(), 'issuer_hash': hashlib.sha256(config['issuer'].encode()).hexdigest(),
                    'capabilities': allowed, 'authenticated_at': authenticated}
        except jwt.PyJWTError as error:
            raise DomainError('UNAUTHENTICATED', 'Identity token failed verification.', 401) from error

    def login(self):
        config = self.configuration()
        require(config['mode'] == 'institutional', 'IDENTITY_UNAVAILABLE', 'Institutional identity is not configured.', 503)
        state, verifier, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        with self.app.store.transaction() as db:
            db.execute('DELETE FROM identity_logins WHERE expires_at<?', (time.time(),))
            db.execute('INSERT INTO identity_logins VALUES(?,?,?,?)', (hashlib.sha256(state.encode()).hexdigest(), verifier, nonce, time.time() + 300))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        redirect = config['authorization_endpoint'] + '?' + urlencode({'response_type': 'code', 'client_id': config['client_id'], 'redirect_uri': config['external_origin'] + '/auth/callback', 'scope': 'openid profile', 'state': state, 'nonce': nonce, 'code_challenge': challenge, 'code_challenge_method': 'S256'})
        return state, redirect

    def callback(self, state, code, bound_state):
        require(isinstance(state, str) and isinstance(code, str) and bound_state and secrets.compare_digest(state, bound_state), 'LOGIN_STATE', 'Login state does not match this browser.', 401)
        config = self.configuration()
        with self.app.store.transaction() as db:
            login = db.execute('SELECT * FROM identity_logins WHERE state_hash=? AND expires_at>?', (hashlib.sha256(state.encode()).hexdigest(), time.time())).fetchone()
            require(login, 'LOGIN_STATE', 'Login state expired or was already used.', 401)
            db.execute('DELETE FROM identity_logins WHERE state_hash=?', (hashlib.sha256(state.encode()).hexdigest(),))
        parsed = urlsplit(config['token_endpoint'])
        origin = parsed.scheme + '://' + parsed.netloc
        form = {'grant_type': 'authorization_code', 'client_id': config['client_id'], 'code': code, 'redirect_uri': config['external_origin'] + '/auth/callback', 'code_verifier': login['verifier']}
        if config.get('client_secret'):
            form['client_secret'] = config['client_secret']
        tokens = Transport(origin, ca_file=config.get('ca_file')).call('POST', parsed.path, urlencode(form).encode(), {'Content-Type': 'application/x-www-form-urlencoded'}, timeout=8)
        identity = self.verify(tokens.get('id_token'), login['nonce'])
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        lifetime = self.app.runtime.configuration().get('identity', {}).get('max_session_seconds', 3600)
        expiry = min(time.time() + lifetime, identity['expires_at'], identity['authenticated_at'] + lifetime)
        require(expiry > time.time(), 'LOGIN_FRESHNESS', 'Reauthenticate with the issuer; the allowed session age has elapsed.', 401)
        with self.app.store.transaction() as db:
            db.execute('INSERT INTO sessions(token_hash,csrf,expires_at,tenant_id,permissions_json) VALUES(?,?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), csrf, expiry, identity['tenant_id'], json.dumps(identity['permissions'])))
            self.app.runtime.remember_session(db, hashlib.sha256(token.encode()).hexdigest(), identity, expiry)
            self.app.store.audit(db, identity['tenant_id'], None, 'identity.signed_in', identity['subject_hash'], {})
        return token, max(0, int(expiry - time.time()))
