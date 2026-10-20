import argparse
import fcntl
import hashlib
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import threading
import time
from urllib.parse import urlsplit, parse_qs

from .app import App
from .errors import DomainError, require
from .gateways import EngineError
from .util import now

STATIC = Path(__file__).with_name('static')


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    capacity = threading.BoundedSemaphore(64)

    def process_request(self, request, client_address):
        if not self.capacity.acquire(blocking=False):
            request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n')
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.capacity.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.capacity.release()


class Router:
    server_version = 'Avenra'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt, *args):
        logging.info('%s %s', self.command, urlsplit(self.path).path)

    def send(self, status, body, content_type='application/json; charset=utf-8', extra=None, raw_json=False):
        encoded = body if raw_json else json.dumps(body, separators=(',', ':'), allow_nan=False).encode() if content_type.startswith('application/json') else body
        self.finish_runtime_request(status)
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(encoded)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def body(self):
        require(self.headers.get('Content-Type', '').split(';')[0].strip() == 'application/json', 'CONTENT_TYPE', 'Use application/json.', 415)
        lengths = self.headers.get_all('Content-Length') or []
        require(len(lengths) == 1 and lengths[0].isdigit(), 'INVALID_BODY', 'A valid Content-Length is required.')
        size = int(lengths[0])
        require(size <= 65536, 'BODY_TOO_LARGE', 'Request exceeds the local profile limit.', 413)
        require(not self.headers.get('Transfer-Encoding'), 'INVALID_BODY', 'Transfer encoding is unsupported.')
        try:
            def unique_fields(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError('Duplicate JSON field')
                    value[key] = item
                return value
            value = json.loads(self.rfile.read(size), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()), object_pairs_hook=unique_fields)
            json.dumps(value, allow_nan=False)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise DomainError('INVALID_JSON', 'Request body must contain valid JSON.')
        require(isinstance(value, dict), 'INVALID_BODY', 'Request body must be a JSON object.')
        return value

    def authenticate(self):
        app = self.server.app
        permission = 'write' if self.command == 'POST' and urlsplit(self.path).path != '/api/session/logout' else 'read'
        authorization = self.headers.get('Authorization', '')
        if authorization.startswith('Bearer '):
            configuration = app.identity.configuration()
            if configuration['mode'] == 'institutional' and not authorization[7:].startswith('avn_'):
                identity = app.identity.verify(authorization[7:])
                require(permission in identity['permissions'], 'FORBIDDEN', 'Identity does not grant this operation.', 403)
                self.identity_context = app.runtime.context(identity['principal'], identity['permissions'], approval_principal='subject:' + identity['subject_hash'], capabilities=identity['capabilities'], expires_at=identity['expires_at'], operator=False)
                return identity['tenant_id']
            token_hash = hashlib.sha256(authorization[7:].encode()).hexdigest()
            with app.store.reader() as db:
                key = db.execute('SELECT * FROM api_keys WHERE token_hash=? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)', (token_hash, time.time())).fetchone()
            require(key, 'UNAUTHENTICATED', 'API key is invalid.', 401)
            require(configuration['mode'] != 'institutional' or key['expires_at'] is not None, 'UNAUTHENTICATED', 'Institutional service keys require an explicit expiry.', 401)
            require(permission in json.loads(key['permissions_json']), 'FORBIDDEN', 'API key does not grant this operation.', 403)
            self.identity_context = app.runtime.context('service:' + hashlib.sha256(authorization.encode()).hexdigest(), json.loads(key['permissions_json']), expires_at=key['expires_at'], operator=False)
            return key['tenant_id']
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie', ''))
        except cookies.CookieError:
            raise DomainError('UNAUTHENTICATED', 'Open a local console session.', 401)
        token = jar.get('avenra_session')
        token_hash = hashlib.sha256((token.value if token else '').encode()).hexdigest()
        with app.store.reader() as db:
            session = db.execute('SELECT * FROM sessions WHERE token_hash=? AND expires_at>?', (token_hash, time.time())).fetchone()
        require(session, 'UNAUTHENTICATED', 'Open a local console session.', 401)
        require(app.identity.configuration()['mode'] != 'institutional' or session['tenant_id'] is not None, 'UNAUTHENTICATED', 'Institutional sign-in is required.', 401)
        require(permission in json.loads(session['permissions_json']), 'FORBIDDEN', 'Session does not grant this operation.', 403)
        self.identity_context = app.runtime.session_context(token_hash, session['tenant_id'], json.loads(session['permissions_json']))
        if self.command == 'POST':
            require(secrets.compare_digest(self.headers.get('X-Avenra-CSRF', ''), session['csrf']), 'CSRF', 'Console request token is missing or invalid.', 403)
            self.require_origin()
        return session['tenant_id']

    def finish_runtime_request(self, status):
        lease = getattr(self, 'runtime_lease', None)
        if lease:
            self.observed_runtime_lease = lease
            self.runtime_lease = None
            try: self.server.app.runtime.complete(lease, status)
            except Exception: logging.exception('Runtime request observation could not be retained')

    def require_origin(self):
        configuration = self.server.app.identity.configuration()
        expected = configuration['external_origin'] if configuration['mode'] == 'institutional' else getattr(self, 'scheme', 'http') + '://' + self.headers.get('Host', '')
        require(self.headers.get('Origin') == expected, 'ORIGIN', 'Request must originate from this console.', 403)

    def do_OPTIONS(self):
        self.send(403, {'error': {'code': 'ORIGIN', 'message': 'Cross-origin API access is disabled.'}})

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        try:
            port = self.server.server_port
            configuration = self.server.app.identity.configuration()
            allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
            if configuration['mode'] == 'institutional':
                allowed_hosts.add(urlsplit(configuration['external_origin']).netloc)
            require(self.headers.get('Host') in allowed_hosts, 'HOST', 'Use the configured console host.', 403)
            origin = self.headers.get('Origin')
            if origin:
                self.require_origin()
            path = urlsplit(self.path).path
            require('//' not in path and '%' not in path, 'INVALID_PATH', 'Use a canonical API path.')
            financial_data = re.fullmatch(r'/aa/([A-Za-z0-9_]+)/([A-Za-z0-9_]+)(/.*)', path)
            if financial_data:
                from .aa.api import wire_dispatch
                return wire_dispatch(self, *financial_data.groups())
            if self.command == 'GET' and path == '/auth/login':
                state, redirect = self.server.app.identity.login()
                secure = '; Secure' if configuration['external_origin'].startswith('https://') else ''
                return self.send(302, {}, extra={'Location': redirect, 'Set-Cookie': 'avenra_login=' + state + '; Path=/auth; HttpOnly; SameSite=Lax; Max-Age=300' + secure})
            if self.command == 'GET' and path == '/auth/callback':
                query = parse_qs(urlsplit(self.path).query)
                require(len(query.get('state', [])) == 1 and len(query.get('code', [])) == 1, 'LOGIN_STATE', 'Valid login response required.', 401)
                jar = cookies.SimpleCookie()
                jar.load(self.headers.get('Cookie', ''))
                bound = jar.get('avenra_login')
                token, age = self.server.app.identity.callback(query['state'][0], query['code'][0], bound.value if bound else None)
                secure = '; Secure' if configuration['external_origin'].startswith('https://') else ''
                return self.send(302, {}, extra={'Location': '/', 'Set-Cookie': 'avenra_session=' + token + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=' + str(age) + secure})
            if self.command == 'GET' and path == '/api/health':
                return self.send(200, {'name': 'Avenra', 'state': 'ready', 'mode': 'local_simulation'})
            if self.command == 'GET' and path == '/health/live':
                return self.send(200, {'live': True})
            if self.command == 'GET' and path == '/health/ready':
                result = self.server.app.runtime.readiness()
                return self.send(200 if result['ready'] else 503, result)
            if self.command == 'GET' and path in {'/', '/index.html', '/app.js', '/banking.js', '/simulation.js', '/upi.js', '/fidelity.js', '/production.js', '/aa.js', '/styles.css', '/favicon.svg'}:
                name = 'index.html' if path == '/' else path[1:]
                types = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml'}
                return self.send(200, (STATIC / name).read_bytes(), types[Path(name).suffix])
            if self.command == 'POST' and path == '/api/session':
                self.require_origin()
                require(self.headers.get('X-Avenra-Client') == 'console', 'ORIGIN', 'Console client header is required.', 403)
                self.body()
                existing_jar = cookies.SimpleCookie()
                try:
                    existing_jar.load(self.headers.get('Cookie', ''))
                except cookies.CookieError:
                    existing_jar = cookies.SimpleCookie()
                existing_token = existing_jar.get('avenra_session')
                if existing_token:
                    existing_hash = hashlib.sha256(existing_token.value.encode()).hexdigest()
                    with self.server.app.store.reader() as db:
                        existing_session = db.execute('SELECT csrf,tenant_id FROM sessions WHERE token_hash=? AND expires_at>?', (existing_hash, time.time())).fetchone()
                    if existing_session and (configuration['mode'] == 'local' or existing_session['tenant_id'] is not None):
                        self.server.app.runtime.session_context(existing_hash, existing_session['tenant_id'], [])
                        return self.send(200, {'csrf': existing_session['csrf']})
                require(configuration['mode'] == 'local', 'SSO_REQUIRED', 'Sign in with your institution to open this workspace.', 401)
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                with self.server.app.store.transaction() as db:
                    db.execute('DELETE FROM sessions WHERE expires_at<?', (time.time(),))
                    db.execute('INSERT INTO sessions(token_hash,csrf,expires_at) VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), csrf, time.time() + 43200))
                secure = '; Secure' if getattr(self, 'scheme', 'http') == 'https' else ''
                return self.send(200, {'csrf': csrf}, extra={'Set-Cookie': f'avenra_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200' + secure})
            allowed_tenant = self.authenticate()
            app = self.server.app
            if self.command == 'POST' and path == '/api/session/logout':
                require(not self.headers.get('Authorization'), 'SESSION_REQUIRED', 'Sign out using the authenticated browser session.', 400)
                self.body()
                jar = cookies.SimpleCookie();jar.load(self.headers.get('Cookie', ''))
                token_hash = hashlib.sha256(jar['avenra_session'].value.encode()).hexdigest()
                with app.store.transaction() as db:
                    db.execute('DELETE FROM sessions WHERE token_hash=?', (token_hash,))
                    db.execute('UPDATE runtime_sessions SET revoked_at=? WHERE token_hash=?', (now(), token_hash))
                    app.store.audit(db, allowed_tenant, None, 'identity.signed_out', self.identity_context['principal'], {})
                secure = '; Secure' if getattr(self, 'scheme', 'http') == 'https' else ''
                return self.send(200, {'signed_out': True}, extra={'Set-Cookie': 'avenra_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0' + secure})
            if self.command == 'GET' and path == '/api/me':
                return self.send(200, {'tenant_id': allowed_tenant, **self.identity_context})
            if self.command == 'GET' and path == '/api/catalog':
                return self.send(200, app.runtime.filter_catalog(app.catalog(allowed_tenant), allowed_tenant, self.identity_context))
            if self.command == 'GET' and path == '/api/audit/verify':
                require(allowed_tenant is None, 'FORBIDDEN', 'Operator session required.', 403)
                return self.send(200, app.store.verify_audit())
            match = re.fullmatch(r'/api/tenants/([A-Za-z0-9_]+)/environments(?:/([A-Za-z0-9_]+))?(?:/(.*))?', path)
            require(match, 'NOT_FOUND', 'Endpoint not found.', 404)
            tenant, environment, tail = match.groups()
            require(allowed_tenant is None or allowed_tenant == tenant, 'FORBIDDEN', 'API key belongs to another workspace.', 403)
            if self.command == 'POST' and environment is None:
                self.runtime_lease = app.runtime.guard(self, tenant, None, 'environment')
                data = self.body()
                return self.send(202, app.create_environment(tenant, data.get('name'), data.get('fixture_profile', 'legacy')))
            require(environment is not None, 'NOT_FOUND', 'Environment is required.', 404)
            app.environment(tenant, environment)
            self.runtime_lease = app.runtime.guard(self, tenant, environment, tail or '')
            if tail == 'banking' or (tail or '').startswith('banking/'):
                from .bank_api import dispatch
                return dispatch(self, tenant, environment, tail)
            if self.command == 'GET' and tail == 'overview':
                return self.send(200, app.overview(tenant, environment))
            if self.command == 'GET' and tail == 'accounts':
                return self.send(200, {'accounts': app.accounts(tenant, environment)})
            if self.command == 'POST' and tail == 'payments':
                return self.send(202, app.submit_payment(tenant, environment, self.body()))
            if self.command == 'POST' and tail == 'runs':
                return self.send(202, app.create_run(tenant, environment, self.body()))
            payment_match = re.fullmatch(r'payments/([A-Za-z0-9_]+)(/reconcile)?', tail or '')
            if payment_match:
                payment_id, action = payment_match.groups()
                if self.command == 'POST' and action:
                    self.body()
                    return self.send(202, app.reconcile(tenant, environment, payment_id))
                if self.command == 'GET' and not action:
                    return self.send(200, app.payment(tenant, environment, payment_id))
            run_match = re.fullmatch(r'runs/([A-Za-z0-9_]+)(/evidence)?', tail or '')
            if self.command == 'GET' and run_match:
                run_id, action = run_match.groups()
                run = app.run(tenant, run_id)
                require(run['source_environment_id'] == environment, 'NOT_FOUND', 'Run not found in this environment.', 404)
                if action:
                    require(run['evidence'], 'EVIDENCE_PENDING', 'Evidence is available after the run completes.', 409)
                    return self.send(200, run['evidence'], extra={'Content-Disposition': f'attachment; filename="avenra-{run_id}.json"'})
                return self.send(200, run)
            raise DomainError('NOT_FOUND', 'Endpoint not found.', 404)
        except DomainError as error:
            self.send(error.status, {'error': {'code': error.code, 'message': error.message}})
        except EngineError:
            self.send(503, {'error': {'code': 'ENGINE_UNAVAILABLE', 'message': 'Required local engine is unavailable. Confirm the service status.'}})
        except Exception:
            logging.exception('Avenra request failed')
            self.send(500, {'error': {'code': 'INTERNAL_ERROR', 'message': 'The operation could not complete. Inspect the local service log.'}})


class Handler(Router, BaseHTTPRequestHandler):
    """Legacy local transport retained for compatibility tests only."""


def main():
    parser = argparse.ArgumentParser(description='Avenra local financial simulation console')
    parser.add_argument('--port', type=int, default=4827)
    parser.add_argument('--data', default=str(Path(__file__).resolve().parents[1] / '.local/data'))
    args = parser.parse_args()
    data = Path(args.data).resolve()
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (data / 'server.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('This Avenra installation already has an active server.')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    dsn_file = data / 'control.dsn'
    if dsn_file.exists() and dsn_file.stat().st_mode & 0o077:
        raise SystemExit('Control connection file must be readable only by its owner.')
    app = App(data, control_dsn=dsn_file.read_text().strip() if dsn_file.exists() else None)
    app.bootstrap()
    server = Server(('127.0.0.1', args.port), Handler)
    server.app = app
    app.start_worker()
    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logging.info('Avenra console: http://127.0.0.1:%s', args.port)
    try:
        server.serve_forever(poll_interval=.25)
    finally:
        server.server_close()
        app.close()
        lock.close()


if __name__ == '__main__':
    main()
