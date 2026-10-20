"""Synthetic OIDC counterparty for deployed authorization-code/PKCE rehearsal.

It is loopback-only and uses private fixture accounts. It never authenticates a
bank customer or represents an institutional identity-provider approval.
"""
import argparse
import asyncio
import base64
import hashlib
import hmac
import html
from http import cookies
import json
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .deployment_identity import issue, paths, server_context
from .lab_identity import private_read, private_write
from .production import ROLE_CAPABILITIES
from .errors import DomainError, require
from .util import canonical


def pin_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32).hex()


def provision(directory, port, app_origin, tenant='tenant_avenra'):
    require(type(port) is int and 1 <= port <= 65535 and isinstance(app_origin, str), 'ISSUER_CONFIGURATION', 'Use a valid private issuer port and API origin.')
    origin = urlsplit(app_origin)
    require(origin.scheme == 'https' and origin.hostname == '127.0.0.1' and not (origin.path or origin.query or origin.fragment or origin.username or origin.password), 'ISSUER_CONFIGURATION', 'Register an exact loopback HTTPS API origin.')
    identity = issue(directory, 'synthetic-issuer')
    folder = Path(identity['directory'])
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_write(folder / 'signing-key.pem', key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    users, passwords = {}, {}
    for role in ROLE_CAPABILITIES:
        password, salt = secrets.token_urlsafe(24), secrets.token_hex(16)
        passwords[role] = password
        users[role] = {'sub': 'synthetic-' + role, 'tenant': tenant, 'role': role, 'salt': salt, 'password_hash': pin_hash(password, salt)}
    config = {'format': 'avenra-synthetic-issuer/1', 'classification': 'synthetic_counterparty', 'issuer': 'https://127.0.0.1:' + str(port),
              'client_id': 'avenra-portal', 'client_secret': secrets.token_urlsafe(32), 'api_audience': 'avenra-api',
              'redirect_uri': app_origin + '/auth/callback', 'identity_reference': identity['reference'], 'key_id': 'synthetic-issuer-k1',
              'users': users, 'token_seconds': 300, 'lock_seconds': 5}
    private_write(folder / 'issuer.json', canonical(config))
    private_write(folder / 'fixture-passwords.json', canonical(passwords))
    return {'config': str(folder / 'issuer.json'), 'directory': str(folder), 'identity_reference': identity['reference'], 'issuer': config['issuer'],
            'client_id': config['client_id'], 'client_secret': config['client_secret'], 'audience': config['api_audience'], 'ca_file': str(folder / 'ca-cert.pem'), 'passwords': passwords}


class Issuer:
    def __init__(self, path):
        self.path = Path(path);self.directory = self.path.parent
        self.config = json.loads(private_read(self.path))
        require(self.config.get('format') == 'avenra-synthetic-issuer/1' and self.config.get('classification') == 'synthetic_counterparty', 'ISSUER_CONFIGURATION', 'Only the declared synthetic issuer profile can start.', 503)
        origin = urlsplit(self.config['issuer'])
        require(origin.scheme == 'https' and origin.hostname == '127.0.0.1' and origin.path == '', 'ISSUER_CONFIGURATION', 'Synthetic issuer remains on loopback HTTPS.', 503)
        self.key = serialization.load_pem_private_key(private_read(self.directory / 'signing-key.pem'), password=None)
        self.jwk = {**jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True), 'kid': self.config['key_id'], 'alg': 'RS256', 'use': 'sig'}
        self.db_path = self.directory / 'issuer.db'
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS interactions(id TEXT PRIMARY KEY,browser_hash TEXT NOT NULL,client_id TEXT NOT NULL,redirect_uri TEXT NOT NULL,state TEXT NOT NULL,nonce TEXT NOT NULL,challenge TEXT NOT NULL,expires_at REAL NOT NULL,completed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS codes(code_hash TEXT PRIMARY KEY,client_id TEXT NOT NULL,redirect_uri TEXT NOT NULL,nonce TEXT NOT NULL,challenge TEXT NOT NULL,username TEXT NOT NULL,authenticated_at REAL NOT NULL,expires_at REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS failures(username TEXT PRIMARY KEY,attempts INTEGER NOT NULL,locked_until REAL NOT NULL);''')
        self.db_path.chmod(0o600)

    def db(self):
        from contextlib import contextmanager
        @contextmanager
        def connection():
            db = sqlite3.connect(self.db_path, timeout=5, isolation_level=None);db.row_factory = sqlite3.Row
            try: yield db
            finally: db.close()
        return connection()

    @staticmethod
    def fields(raw):
        try: query = parse_qs(raw.decode('utf-8'), keep_blank_values=True, strict_parsing=True, max_num_fields=16)
        except (ValueError, UnicodeDecodeError): raise DomainError('INVALID_REQUEST', 'Use a bounded form request.')
        require(all(len(v) == 1 for v in query.values()), 'INVALID_REQUEST', 'Repeated fields are refused.')
        return {k: v[0] for k, v in query.items()}

    def authorize(self, query):
        require(set(query) == {'response_type', 'client_id', 'redirect_uri', 'scope', 'state', 'nonce', 'code_challenge', 'code_challenge_method'}, 'INVALID_REQUEST', 'Unsupported authorization request.')
        require(query['response_type'] == 'code' and query['client_id'] == self.config['client_id'] and query['redirect_uri'] == self.config['redirect_uri'], 'CLIENT_BINDING', 'Client or redirect URI is not registered.', 400)
        require(set(query['scope'].split()) <= {'openid', 'profile'} and 'openid' in query['scope'].split() and query['code_challenge_method'] == 'S256', 'INVALID_REQUEST', 'Use the registered OIDC code/S256 profile.')
        import re
        require(all(16 <= len(query[k]) <= 128 for k in ['state', 'nonce']) and re.fullmatch('[A-Za-z0-9_-]{43}', query['code_challenge']), 'INVALID_REQUEST', 'Invalid authorization binding.')
        interaction, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO interactions VALUES(?,?,?,?,?,?,?,?,0)', (interaction, hashlib.sha256(browser.encode()).hexdigest(), query['client_id'], query['redirect_uri'], query['state'], query['nonce'], query['code_challenge'], time.time() + 180))
            db.commit()
        page = ('<!doctype html><html><head><meta charset="utf-8"><title>Avenra synthetic institution sign-in</title></head><body><main><h1>Synthetic institution sign-in</h1><p>Private test identities only. This is an Avenra identity-provider counterparty.</p><form method="post" action="/login"><input type="hidden" name="interaction" value="' + html.escape(interaction) + '"><label>Test username<input name="username" autocomplete="username" required></label><label>Test password<input name="password" type="password" autocomplete="current-password" required></label><button>Sign in</button></form></main></body></html>').encode()
        return 200, page, {'content-type': 'text/html; charset=utf-8', 'set-cookie': '__Host-avenra_issuer=' + browser + '; Path=/; Secure; HttpOnly; SameSite=Lax'}

    def login(self, data, cookie):
        require(set(data) == {'interaction', 'username', 'password'} and len(data['password']) <= 256, 'INVALID_REQUEST', 'Use the synthetic sign-in form.')
        jar = cookies.SimpleCookie()
        try: jar.load(cookie)
        except cookies.CookieError: raise DomainError('LOGIN_BROWSER', 'Invalid browser binding.', 403)
        browser = jar.get('__Host-avenra_issuer')
        require(browser, 'LOGIN_BROWSER', 'This interaction is not bound to the browser.', 403)
        user = self.config['users'].get(data['username'])
        failed = False
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            interaction = db.execute('SELECT * FROM interactions WHERE id=?', (data['interaction'],)).fetchone()
            require(interaction and not interaction['completed'] and interaction['expires_at'] > time.time() and hmac.compare_digest(interaction['browser_hash'], hashlib.sha256(browser.value.encode()).hexdigest()), 'LOGIN_BROWSER', 'The authorization interaction is expired or belongs to another browser.', 403)
            previous = db.execute('SELECT * FROM failures WHERE username=?', (data['username'],)).fetchone()
            computed = pin_hash(data['password'], user['salt'] if user else '00' * 16)
            valid = user and hmac.compare_digest(computed, user['password_hash']) and (not previous or previous['locked_until'] <= time.time())
            if not valid:
                attempts = (previous['attempts'] if previous else 0) + 1
                locked = time.time() + self.config['lock_seconds'] if attempts >= 3 else 0
                db.execute('INSERT INTO failures VALUES(?,?,?) ON CONFLICT(username) DO UPDATE SET attempts=excluded.attempts,locked_until=excluded.locked_until', (data['username'], attempts, locked));db.commit();failed = True
            else:
                db.execute('DELETE FROM failures WHERE username=?', (data['username'],))
                code = secrets.token_urlsafe(32)
                db.execute('INSERT INTO codes VALUES(?,?,?,?,?,?,?,?,0)', (hashlib.sha256(code.encode()).hexdigest(), interaction['client_id'], interaction['redirect_uri'], interaction['nonce'], interaction['challenge'], data['username'], time.time(), time.time() + 60))
                db.execute('UPDATE interactions SET completed=1 WHERE id=?', (interaction['id'],));db.commit()
        require(not failed, 'LOGIN_FAILED', 'Synthetic sign-in was refused.', 401)
        return 303, b'', {'location': interaction['redirect_uri'] + '?' + urlencode({'code': code, 'state': interaction['state']})}

    def token(self, data):
        require(set(data) == {'grant_type', 'client_id', 'client_secret', 'code', 'redirect_uri', 'code_verifier'}, 'INVALID_GRANT', 'Use the registered confidential code grant.', 401)
        require(data['grant_type'] == 'authorization_code' and data['client_id'] == self.config['client_id'] and hmac.compare_digest(data['client_secret'], self.config['client_secret']), 'INVALID_CLIENT', 'Client authentication was refused.', 401)
        import re
        require(re.fullmatch('[A-Za-z0-9._~-]{43,128}', data['code_verifier']), 'INVALID_GRANT', 'Invalid PKCE verifier.', 401)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(data['code_verifier'].encode()).digest()).rstrip(b'=').decode()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM codes WHERE code_hash=?', (hashlib.sha256(data['code'].encode()).hexdigest(),)).fetchone()
            require(row and not row['used'] and row['expires_at'] > time.time() and row['client_id'] == data['client_id'] and row['redirect_uri'] == data['redirect_uri'] and hmac.compare_digest(row['challenge'], challenge), 'INVALID_GRANT', 'Authorization code is expired, used or bound to different proof.', 401)
            db.execute('UPDATE codes SET used=1 WHERE code_hash=?', (row['code_hash'],));db.commit()
        user = self.config['users'][row['username']];current = int(time.time())
        claims = {'iss': self.config['issuer'], 'sub': user['sub'], 'iat': current, 'exp': current + self.config['token_seconds'], 'auth_time': row['authenticated_at'], 'avenra_tenant': user['tenant'],
                  'avenra_permissions': ['read'] if user['role'] == 'observer' else ['read', 'write'], 'avenra_capabilities': ROLE_CAPABILITIES[user['role']]}
        def signed(extra): return jwt.encode({**claims, **extra}, self.key, algorithm='RS256', headers={'kid': self.config['key_id']})
        return {'token_type': 'Bearer', 'expires_in': self.config['token_seconds'], 'id_token': signed({'aud': self.config['client_id'], 'nonce': row['nonce']}), 'access_token': signed({'aud': self.config['api_audience']})}

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http': return
        try:
            require(scope.get('scheme') == 'https', 'TLS_REQUIRED', 'Synthetic issuer requires TLS.', 403)
            headers = {}
            for name, value in scope['headers']:
                require(name not in headers, 'INVALID_HEADERS', 'Duplicate headers are refused.')
                headers[name] = value
            path, method = scope['path'], scope['method']
            if path == '/jwks' and method == 'GET': result = 200, canonical({'keys': [self.jwk]}).encode(), {}
            elif path == '/.well-known/openid-configuration' and method == 'GET':
                c = self.config;result = 200, canonical({'issuer': c['issuer'], 'authorization_endpoint': c['issuer'] + '/authorize', 'token_endpoint': c['issuer'] + '/token', 'jwks_uri': c['issuer'] + '/jwks', 'response_types_supported': ['code'], 'subject_types_supported': ['public'], 'id_token_signing_alg_values_supported': ['RS256'], 'code_challenge_methods_supported': ['S256']}).encode(), {}
            elif path == '/authorize' and method == 'GET': result = await asyncio.to_thread(self.authorize, self.fields(scope['query_string']))
            elif path in {'/login', '/token'} and method == 'POST':
                require(headers.get(b'content-type', b'').split(b';')[0] == b'application/x-www-form-urlencoded', 'CONTENT_TYPE', 'Use a form body.', 415)
                size = headers.get(b'content-length', b'')
                require(size.isdigit() and int(size) <= 8192, 'BODY_LIMIT', 'Use a bounded form body.', 413)
                body = bytearray()
                deadline = time.monotonic() + 5
                while True:
                    try: event = await asyncio.wait_for(receive(), max(.001, deadline - time.monotonic()))
                    except TimeoutError: raise DomainError('BODY_TIMEOUT', 'Sign-in body exceeded its arrival deadline.', 408)
                    if event['type'] == 'http.disconnect': return
                    body.extend(event.get('body', b''))
                    require(len(body) <= 8192, 'BODY_LIMIT', 'Form body exceeds its budget.', 413)
                    if not event.get('more_body'): break
                require(len(body) == int(size), 'INVALID_BODY', 'Form body length differs from its framing.')
                values = self.fields(bytes(body))
                if path == '/login': result = await asyncio.to_thread(self.login, values, headers.get(b'cookie', b'').decode('latin-1'))
                else: result = 200, canonical(await asyncio.to_thread(self.token, values)).encode(), {}
            else: raise DomainError('NOT_FOUND', 'Synthetic issuer endpoint not found.', 404)
        except DomainError as error: result = error.status, canonical({'error': error.code, 'error_description': error.message}).encode(), {}
        status, body, extra = result
        headers = {'content-type': 'application/json', 'cache-control': 'no-store', 'content-length': str(len(body)), 'x-content-type-options': 'nosniff',
                   'content-security-policy': "default-src 'none'; form-action 'self'; frame-ancestors 'none'", **extra}
        await send({'type': 'http.response.start', 'status': status, 'headers': [(k.encode(), v.encode()) for k, v in headers.items()]});await send({'type': 'http.response.body', 'body': body})


def main():
    parser = argparse.ArgumentParser(description='Avenra private synthetic OIDC counterparty')
    parser.add_argument('--config', required=True);args = parser.parse_args()
    app = Issuer(args.config);directory = app.directory.parent.parent
    identity = paths(directory, app.config['identity_reference'])
    context = server_context(directory, app.config['identity_reference'])
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=urlsplit(app.config['issuer']).port, http='h11', ws='none', lifespan='off', proxy_headers=False, access_log=False,
                server_header=False, limit_concurrency=32, timeout_keep_alive=3, ssl_certfile=str(identity['cert']), ssl_keyfile=str(identity['key']), ssl_context_factory=lambda *_: context)


if __name__ == '__main__': main()
