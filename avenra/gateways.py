"""Bounded clients for Avenra's locally built engines."""
import json
import os
import platform
import socket
from contextlib import contextmanager
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, quote
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class EngineError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HTTP:
    def __init__(self, origin):
        parsed = urlsplit(origin)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError('Local engine origin must use http://127.0.0.1:<port>.')
        parsed.port
        self.origin = origin.rstrip('/')
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def call(self, method, path, body=None, key=None, timeout=8):
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if key:
            headers['Idempotency-Key'] = key
        request = Request(self.origin + path, method=method, headers=headers,
                          data=json.dumps(body, separators=(',', ':'), allow_nan=False).encode() if body is not None else None)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    raise EngineError(0, 'INVALID_RESPONSE', 'Engine response exceeds local profile limits.')
                return json.loads(raw) if raw else {}
        except HTTPError as error:
            with error:
                raw = error.read(8192)
            try:
                detail = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                detail = {}
            raise EngineError(error.code, detail.get('errorCode', 'ENGINE_REJECTED'),
                              detail.get('errorMessage', f'Engine returned HTTP {error.code}.')) from error
        except (URLError, TimeoutError, OSError, ValueError) as error:
            raise EngineError(0, 'OUTCOME_UNKNOWN', 'Engine outcome is unconfirmed; reconcile the original intent.') from error


class AccountingGateway:
    def __init__(self, url='http://127.0.0.1:4830'):
        self.http = HTTP(url)

    def health(self):
        return self.http.call('GET', '/_/info', timeout=2)

    def create(self, name, environment):
        try:
            self.http.call('POST', '/v2/' + name, {'metadata': {'owner': 'avenra', 'environment_id': environment}})
        except EngineError as error:
            if error.code not in {'LEDGER_ALREADY_EXISTS', 'ALREADY_EXISTS'}:
                raise
            existing = self.http.call('GET', '/v2/' + name).get('data', {})
            if existing.get('metadata', {}).get('environment_id') != environment:
                raise EngineError(409, 'OWNERSHIP_CONFLICT', 'Existing ledger does not belong to this environment.')

    def post(self, name, body, key, origin=None):
        client = HTTP(origin) if origin else self.http
        return client.call('POST', '/v2/' + name + '/transactions', body, key, timeout=4)['data']

    def accounts(self, name):
        return self._pages('/v2/' + name + '/accounts?expand=volumes&pageSize=100')

    def transactions(self, name, reference=None, all_pages=False):
        body = {'$match': {'reference': reference}} if reference else None
        path = '/v2/' + name + '/transactions?pageSize=100'
        return self._pages(path, body) if all_pages else self.http.call('GET', path, body)['cursor']['data']

    def _pages(self, path, body=None):
        result, seen = [], set()
        origin = path
        for _ in range(1000):
            cursor = self.http.call('GET', path, body)['cursor']
            result.extend(cursor['data'])
            if not cursor.get('hasMore'):
                return result
            token = cursor.get('next')
            if not isinstance(token, str) or not token or token in seen:
                raise EngineError(0, 'INVALID_CURSOR', 'Accounting pagination did not produce a valid next page.')
            seen.add(token)
            path = origin + '&cursor=' + quote(token, safe='')
        raise EngineError(0, 'CAPACITY_LIMIT', 'Result exceeds this profile; use a paginated reporting adapter.')


class FaultGateway:
    def __init__(self, url='http://127.0.0.1:4831', upstream='127.0.0.1:4830'):
        self.http, self.upstream = HTTP(url), upstream

    def health(self):
        return self.http.call('GET', '/version', timeout=2)

    @contextmanager
    def lost_response(self, intent_id):
        name = 'avn_' + intent_id
        try:
            old = self.http.call('GET', '/proxies/' + name)
        except EngineError as error:
            if error.status != 404:
                raise
        else:
            if old.get('upstream') != self.upstream or not old.get('listen', '').startswith('127.0.0.1:'):
                raise EngineError(409, 'OWNERSHIP_CONFLICT', 'Fault proxy ownership does not match.')
            self.http.call('DELETE', '/proxies/' + name)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.http.call('POST', '/proxies', {'name': name, 'listen': f'127.0.0.1:{port}', 'upstream': self.upstream, 'enabled': True})
        try:
            self.http.call('POST', f'/proxies/{name}/toxics', {
                'name': 'drop_response', 'type': 'limit_data', 'stream': 'downstream',
                'toxicity': 1.0, 'attributes': {'bytes': 0}})
            yield f'http://127.0.0.1:{port}'
        finally:
            try:
                self.http.call('DELETE', '/proxies/' + name)
            except EngineError:
                pass


def execution_status():
    linux = platform.system() == 'Linux'
    kvm = linux and os.access('/dev/kvm', os.R_OK | os.W_OK)
    return {'key': 'execution', 'name': 'Isolated execution', 'state': 'unavailable',
            'source_present': True, 'host': platform.system() + '/' + platform.machine(),
            'kvm_accessible': kvm,
            'detail': ('Source imported; build and worker provisioning are still required.' if kvm else
                       'Requires a qualified Linux worker with KVM. Host execution is disabled.')}
