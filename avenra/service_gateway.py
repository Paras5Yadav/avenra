"""Private mTLS ingress for a loopback engine lacking native TLS.

The final HTTP hop is restricted to the same host. A deployment must put the
gateway and engine in one protected network namespace, with only this TLS port
reachable from API/worker nodes. This module does not claim that loopback hop
is encrypted or that an institutional network boundary has been assessed.
"""
import argparse
import asyncio
from contextlib import nullcontext
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from .deployment_identity import paths, server_context
from .errors import DomainError, require
from .gateways import NoRedirect
from .private_config import read_document


class Gateway:
    def __init__(self, config):
        self.config = config
        origin = urlsplit(config.get('upstream', ''))
        require(config.get('format') == 'avenra-service-gateway/1' and origin.scheme == 'http' and origin.hostname == '127.0.0.1' and origin.port and not (origin.path or origin.query or origin.fragment or origin.username or origin.password), 'GATEWAY_CONFIGURATION', 'Gateway upstream must be an explicit private loopback engine.', 503)
        require(config.get('service') in {'accounting', 'aa'}, 'GATEWAY_CONFIGURATION', 'Choose a supported service route profile.', 503)
        if config['service']=='aa':
            import re
            require(isinstance(config.get('prefix'),str) and re.fullmatch('/aa/[A-Za-z0-9_]+/[A-Za-z0-9_]+',config['prefix']),'GATEWAY_CONFIGURATION','Use one exact financial-data environment prefix.',503)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.capacity = asyncio.Semaphore(16)

    def forward(self, method, target, body, headers):
        allowed = {'content-type', 'accept', 'idempotency-key'}
        if self.config['service'] == 'aa': allowed |= {'x-jws-signature', 'client_api_key'}
        request = Request(self.config['upstream'] + target, data=body or None, method=method, headers={k: v for k, v in headers.items() if k in allowed})
        try: response = self.opener.open(request, timeout=8)
        except HTTPError as error: response = error
        with response:
            content = response.read(4_000_001)
            require(len(content) <= 4_000_000, 'RESPONSE_LIMIT', 'Engine response exceeds gateway bounds.', 502)
            return response.status, content, response.headers.get('Content-Type', 'application/json'), response.headers.get('x-jws-signature')

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http': return
        held = False
        try:
            require(scope.get('scheme') == 'https', 'TLS_REQUIRED', 'Engine gateway requires mutual TLS.', 403)
            require(not self.capacity.locked(), 'CAPACITY', 'Gateway request budget is occupied.', 503)
            await self.capacity.acquire();held = True
            method, path = scope['method'], scope['raw_path'].decode('ascii')
            permitted = (path == '/_/info' or path.startswith('/v2/')) if self.config['service']=='accounting' else path.startswith(self.config['prefix'] + '/')
            require(method in {'GET', 'POST'} and permitted and '//' not in path and '%' not in path and '..' not in path, 'ROUTE', 'Route is outside the private service profile.', 403)
            headers = {}
            for key, value in scope['headers']:
                name = key.decode('ascii')
                require(name not in headers, 'INVALID_HEADERS', 'Use unique gateway headers.')
                headers[name] = value.decode('latin-1')
            require('transfer-encoding' not in headers, 'INVALID_BODY', 'Use a bounded Content-Length body.')
            length = headers.get('content-length', '0')
            require(length.isdigit() and int(length) <= 65536, 'BODY_LIMIT', 'Engine body exceeds gateway bounds.', 413)
            content = bytearray();deadline = time.monotonic() + 5
            while True:
                try: event = await asyncio.wait_for(receive(), max(.001, deadline - time.monotonic()))
                except TimeoutError: raise DomainError('BODY_TIMEOUT', 'Engine body arrival deadline exceeded.', 408)
                if event['type'] == 'http.disconnect': return
                content.extend(event.get('body', b''));require(len(content) <= 65536, 'BODY_LIMIT', 'Engine body exceeds gateway bounds.', 413)
                if not event.get('more_body'): break
            require(len(content) == int(length), 'INVALID_BODY', 'Engine body differs from its framing.')
            target = path + ('?' + scope['query_string'].decode('ascii') if scope['query_string'] else '')
            task = asyncio.create_task(asyncio.to_thread(self.forward, method, target, bytes(content), headers))
            try: status, body, content_type, signature = await asyncio.shield(task)
            except asyncio.CancelledError:
                held = False
                task.add_done_callback(lambda done: (self.capacity.release(), None if done.cancelled() else done.exception()))
                raise
        except DomainError as error:
            signature = None
            status, body, content_type = error.status, json.dumps({'errorCode': error.code, 'errorMessage': error.message}).encode(), 'application/json'
        except (OSError, ValueError, UnicodeError):
            signature = None
            status, body, content_type = 502, b'{"errorCode":"OUTCOME_UNKNOWN","errorMessage":"Gateway outcome is unconfirmed; reconcile original intent."}', 'application/json'
        finally:
            if held: self.capacity.release()
        response_headers = [(b'content-type', content_type.encode()), (b'content-length', str(len(body)).encode()), (b'cache-control', b'no-store')]
        if signature: response_headers.append((b'x-jws-signature', signature.encode()))
        await send({'type': 'http.response.start', 'status': status, 'headers': response_headers})
        await send({'type': 'http.response.body', 'body': body})


def main():
    parser = argparse.ArgumentParser(description='Avenra private engine mutual-TLS gateway')
    parser.add_argument('--data', required=True);parser.add_argument('--config', required=True);parser.add_argument('--recovery-data');args = parser.parse_args()
    directory = Path(args.data).resolve();config = read_document(args.config)
    from .aa.runtime import service_lease,launch_binding
    is_aa=config.get('service')=='aa'
    require(not args.recovery_data or is_aa,'RECOVERY_AA_BINDING','Only captured AA ingress uses this recovery profile.',409)
    with service_lease(args.config) if is_aa else nullcontext():
        if is_aa:
            binding=launch_binding(args.config,args.recovery_data)
            if binding:
                from .aa.routing import register_process,origin
                routes,node=register_process(args.recovery_data,args.config)
                config={**config,'port':origin(node['origin']),'upstream':routes['api_origin']}
        app = Gateway(config);identity = paths(directory, config['identity_reference'])
        context = server_context(directory, config['identity_reference'], True)
        import uvicorn
        uvicorn.run(app, host='127.0.0.1', port=config['port'], http='h11', ws='none', lifespan='off', access_log=False, proxy_headers=False, server_header=False, limit_concurrency=32, timeout_keep_alive=3,
                    ssl_certfile=str(identity['cert']), ssl_keyfile=str(identity['key']), ssl_context_factory=lambda *_: context)


if __name__ == '__main__': main()
