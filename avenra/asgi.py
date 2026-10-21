"""Bounded ASGI serving for the shared request router and durable worker.

Uvicorn owns HTTP parsing, connection lifecycle, limits and graceful shutdown.
All accepted financial work is persisted before the request returns.
"""
import argparse
import asyncio
from email.message import Message
import fcntl
from io import BytesIO
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import time

from .app import App
from .server import Router


HEADERS = {
    'cache-control': 'no-store', 'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
    'content-security-policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
}


class Request(Router):
    def __init__(self, app, scope, body):
        self.server = SimpleNamespace(app=app, server_port=scope['server'][1])
        self.command = scope['method']
        self.scheme = scope.get('scheme', 'http')
        self.path = scope.get('raw_path', scope['path'].encode()).decode('ascii')
        if scope['query_string']:
            self.path += '?' + scope['query_string'].decode('ascii')
        self.headers = Message()
        for key, value in scope['headers']:
            self.headers[key.decode('ascii')] = value.decode('latin-1')
        self.rfile = BytesIO(body)
        self.response = None

    def send(self, status, body, content_type='application/json; charset=utf-8', extra=None, raw_json=False):
        encoded = body if raw_json else json.dumps(body, separators=(',', ':'), allow_nan=False).encode() if content_type.startswith('application/json') else body
        self.finish_runtime_request(status)
        headers = {**HEADERS, 'content-type': content_type, 'content-length': str(len(encoded)), **{k.lower(): v for k, v in (extra or {}).items()}}
        if self.scheme == 'https': headers['strict-transport-security'] = 'max-age=31536000'
        self.response = status, [(k.encode('ascii'), v.encode('latin-1')) for k, v in headers.items()], encoded


class Application:
    def __init__(self, app, embedded_worker=True):
        self.app, self.embedded_worker = app, embedded_worker
        self.stopping = False
        self.serving_policy = app.runtime.configuration().get('http', {})
        self.capacity = asyncio.Semaphore(self.serving_policy.get('max_concurrency', 32))
        self.lingering = set()

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                event = await receive()
                if event['type'] == 'lifespan.startup':
                    self.app.runtime.heartbeat('api')
                    if self.embedded_worker:
                        self.app.start_worker()
                    await send({'type': 'lifespan.startup.complete'})
                elif event['type'] == 'lifespan.shutdown':
                    self.stopping = True
                    if self.lingering:
                        await asyncio.wait(self.lingering, timeout=65)
                        if self.lingering:
                            await send({'type': 'lifespan.shutdown.failed', 'message': 'Requests have not drained; retain original operation identities for recovery.'})
                            return
                    await asyncio.to_thread(self.app.close)
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
        elif scope['type'] == 'http':
            if scope['method'] == 'GET' and scope['path'] == '/health/live':
                body = json.dumps({'live': True, 'draining': self.stopping}, separators=(',', ':')).encode()
                await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'application/json'), (b'cache-control', b'no-store'), (b'content-length', str(len(body)).encode())]})
                await send({'type': 'http.response.body', 'body': body})
                return
            if self.stopping or self.capacity.locked():
                return await self.error(send, 503, 'CAPACITY', 'Service is draining or at its request limit.')
            await self.capacity.acquire()
            ownership = {'held': True}
            try: await self.request(scope, receive, send, ownership)
            finally:
                if ownership['held']: self.capacity.release()
        else:
            await send({'type': 'websocket.close', 'code': 1008})

    async def error(self, send, status, code, message):
        body = json.dumps({'error': {'code': code, 'message': message}}, separators=(',', ':')).encode()
        headers = {**HEADERS, 'content-type': 'application/json; charset=utf-8', 'content-length': str(len(body))}
        await send({'type': 'http.response.start', 'status': status, 'headers': [(k.encode(), v.encode()) for k, v in headers.items()]})
        await send({'type': 'http.response.body', 'body': body})

    async def request(self, scope, receive, send, ownership=None):
        if scope['method'] not in {'GET', 'POST', 'OPTIONS'}:
            return await self.error(send, 405, 'METHOD', 'Supported methods are GET, POST and OPTIONS.')
        unique = {b'host', b'authorization', b'origin', b'content-type', b'content-length', b'cookie', b'x-avenra-csrf'}
        counts = {key: sum(k == key for k, _ in scope['headers']) for key in unique}
        if any(v > 1 for v in counts.values()) or counts[b'host'] != 1 or sum(len(k) + len(v) for k, v in scope['headers']) > 16384:
            return await self.error(send, 400, 'INVALID_HEADERS', 'Use one bounded value per authentication and framing header.')
        lengths = [v for k, v in scope['headers'] if k == b'content-length']
        if lengths and (len(lengths[0]) > 6 or not lengths[0].isdigit() or int(lengths[0]) > 65536):
            return await self.error(send, 413, 'BODY_TOO_LARGE', 'Request exceeds the supported body limit.')
        if scope['method'] == 'POST' and not lengths:
            return await self.error(send, 400, 'INVALID_BODY', 'A valid Content-Length is required.')
        if any(k == b'transfer-encoding' for k, _ in scope['headers']):
            return await self.error(send, 400, 'INVALID_BODY', 'Transfer encoding is unsupported.')
        body, deadline = bytearray(), time.monotonic() + 10
        while True:
            try:
                event = await asyncio.wait_for(receive(), max(.001, deadline - time.monotonic()))
            except TimeoutError:
                return await self.error(send, 408, 'BODY_TIMEOUT', 'Request body did not arrive within the deadline.')
            if event['type'] == 'http.disconnect':
                return
            body.extend(event.get('body', b''))
            if len(body) > 65536:
                return await self.error(send, 413, 'BODY_TOO_LARGE', 'Request exceeds the supported body limit.')
            if not event.get('more_body'):
                break
        if lengths and len(body) != int(lengths[0]):
            return await self.error(send, 400, 'INVALID_BODY', 'Request length differs from its declared framing.')
        try:
            request = Request(self.app, scope, bytes(body))
        except UnicodeError:
            return await self.error(send, 400, 'INVALID_PATH', 'Use an ASCII encoded request target.')
        def dispatch():
            if request.command == 'OPTIONS':
                request.do_OPTIONS()
            else:
                request.dispatch()
            return request.response
        task = asyncio.create_task(asyncio.to_thread(dispatch))
        def retain_until_finished():
            self.lingering.add(task)
            if ownership is not None: ownership['held'] = False
            def finished(done):
                self.lingering.discard(done)
                if ownership is not None: self.capacity.release()
                if not done.cancelled(): done.exception()
            task.add_done_callback(finished)
        try:
            response = await asyncio.wait_for(asyncio.shield(task), timeout=self.serving_policy.get('dispatch_timeout_seconds', 60))
        except TimeoutError:
            retain_until_finished()
            await self.error(send, 504, 'REQUEST_DEADLINE', 'The response deadline elapsed. The original request may still complete; retain its identity and query status.')
            try: await asyncio.to_thread(self.app.runtime.deadline, getattr(request, 'runtime_lease', None) or getattr(request, 'observed_runtime_lease', None))
            except Exception: logging.exception('Response deadline observation could not be retained')
            return
        except asyncio.CancelledError:
            # A disconnected/cancelled request cannot cancel a running Python
            # dispatch thread. Keep its resource permit until it really exits.
            retain_until_finished()
            raise
        if not response:
            return await self.error(send, 500, 'INTERNAL_ERROR', 'Request did not produce a response.')
        status, headers, content = response
        await send({'type': 'http.response.start', 'status': status, 'headers': headers})
        await send({'type': 'http.response.body', 'body': content})


def main():
    parser = argparse.ArgumentParser(description='Avenra bounded ASGI API server')
    parser.add_argument('--data', default=str(Path(__file__).resolve().parents[1] / '.local/data'))
    parser.add_argument('--port', type=int, default=4827)
    parser.add_argument('--mode', choices=['combined', 'api'])
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    data = Path(args.data).resolve();data.mkdir(parents=True, exist_ok=True, mode=0o700)
    from .issuer_recovery import api_binding
    api_binding(data,args.host,args.port)
    lock = (data / ('server.lock' if args.port == 4827 else 'server-' + str(args.port) + '.lock')).open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('This Avenra API instance is already active.')
    dsn_file = data / 'control.dsn'
    if dsn_file.exists() and dsn_file.stat().st_mode & 0o077:
        raise SystemExit('Control connection file requires private permissions.')
    app = App(data, control_dsn=dsn_file.read_text().strip() if dsn_file.exists() else None)
    configuration = app.runtime.configuration()
    if configuration.get('release') and configuration['release']['application_fingerprint'] != app.source_fingerprint:
        app.close();raise SystemExit('Installed application differs from the pinned deployment release.')
    mode = args.mode or configuration.get('role', 'combined')
    if mode == 'worker':
        app.close();raise SystemExit('Run the separate worker entry point for a worker-only deployment.')
    tls = configuration.get('http', {})
    ssl_options = {}
    if tls.get('tls_identity'):
        from .deployment_identity import server_context, paths
        identity = paths(data, tls['tls_identity'])
        context = server_context(data, tls['tls_identity'], tls.get('require_client_certificate', False))
        ssl_options = {'ssl_certfile': str(identity['cert']), 'ssl_keyfile': str(identity['key']), 'ssl_context_factory': lambda *_: context}
    if args.host not in {'127.0.0.1', 'localhost', '::1'} and (not ssl_options or app.identity.configuration()['mode'] != 'institutional' or not configuration.get('identity', {}).get('granular')):
        app.close();raise SystemExit('Non-loopback serving requires TLS, institutional identity and granular authorization.')
    app.bootstrap()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    import uvicorn
    try:
        uvicorn.run(Application(app, mode == 'combined'), host=args.host, port=args.port, http='h11',
                    ws='none', lifespan='on', proxy_headers=False, server_header=False, date_header=True,
                    limit_concurrency=64, backlog=128, timeout_keep_alive=5, timeout_graceful_shutdown=75,
                    h11_max_incomplete_event_size=16384, access_log=False, **ssl_options)
    finally:
        lock.close()


if __name__ == '__main__':
    main()
