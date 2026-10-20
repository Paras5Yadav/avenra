"""Operator-configured service adapters. Configuration never comes from tenant URLs."""
import base64
import json
import secrets
from pathlib import Path
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, ProxyHandler, HTTPSHandler, build_opener

from .errors import DomainError, require
from .gateways import EngineError, NoRedirect


class Transport:
    def __init__(self, origin, headers=None, ca_file=None, client_identity=None):
        parsed = urlsplit(origin)
        require(parsed.scheme in {'http', 'https'} and parsed.hostname and not (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment), 'SERVICE_CONFIGURATION', 'Service origin must be an approved HTTP origin.', 503)
        require(parsed.scheme == 'https' or parsed.hostname == '127.0.0.1', 'SERVICE_CONFIGURATION', 'Remote service connections require HTTPS.', 503)
        self.origin, self.headers = origin.rstrip('/'), headers or {}
        context = ssl.create_default_context(cafile=ca_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if client_identity:
            require(parsed.scheme == 'https', 'SERVICE_CONFIGURATION', 'Client certificates require HTTPS.', 503)
            context.load_cert_chain(str(client_identity['cert']), str(client_identity['key']))
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))

    def call(self, method, path, body=None, headers=None, timeout=8, raw=False, with_status=False):
        require(isinstance(path, str) and path.startswith('/') and not path.startswith('//') and '\r' not in path and '\n' not in path, 'INVALID_PATH', 'Invalid service route.')
        payload = body if isinstance(body, bytes) else json.dumps(body, separators=(',', ':'), allow_nan=False).encode() if body is not None else None
        request = Request(self.origin + path, data=payload, method=method, headers={'Accept': 'application/json', 'Content-Type': 'application/json', **self.headers, **(headers or {})})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                data = response.read(4_000_001)
                if len(data) > 4_000_000:
                    raise EngineError(0, 'INVALID_RESPONSE', 'Service response exceeds profile limits.')
                value = data if raw else json.loads(data) if data else {}
                return (response.status, value) if with_status else value
        except HTTPError as error:
            status = error.code
            try:
                details = json.loads(error.read(65536))
            except (ValueError, UnicodeDecodeError):
                details = {}
            error.close()
            failure = EngineError(status, 'SERVICE_REJECTED', 'Configured service returned HTTP ' + str(status) + '.')
            failure.public_details = [{'parameter': x.get('parameterName'), 'code': x.get('userMessageGlobalisationCode')} for x in details.get('errors', []) if isinstance(x, dict)] if isinstance(details, dict) else []
            raise failure from error
        except (URLError, OSError, TimeoutError, ValueError) as error:
            raise EngineError(0, 'OUTCOME_UNKNOWN', 'Configured service outcome is unconfirmed.') from error


class Services:
    def __init__(self, app):
        self.app = app

    def configuration(self):
        path = self.app.store.directory / 'services.json'
        if not path.exists():
            return {}
        require(path.stat().st_mode & 0o077 == 0, 'SERVICE_CONFIGURATION', 'Service configuration must be readable only by its owner.', 503)
        value = json.loads(path.read_text())
        require(isinstance(value, dict), 'SERVICE_CONFIGURATION', 'Service configuration is invalid.', 503)
        return value

    def client(self, service, tenant, environment=None):
        config = self.configuration().get('services', {}).get(service)
        binding = tenant + '/' + environment if service == 'banking_core' and environment else tenant
        require(config and binding in config.get('tenant_bindings', {}), 'ADAPTER_UNAVAILABLE', 'No qualified service binding for this workspace and environment.', 503)
        headers = {}
        if config.get('username') and config.get('password'):
            headers['Authorization'] = 'Basic ' + base64.b64encode((config['username'] + ':' + config['password']).encode()).decode()
        if service == 'banking_core':
            require(config.get('classification') == 'simulation', 'SERVICE_CONFIGURATION', 'Banking product binding must identify a simulation tenant.', 503)
            bindings = config['tenant_bindings']
            require(len(set(bindings.values())) == len(bindings), 'SERVICE_CONFIGURATION', 'Each banking environment needs its own core tenant.', 503)
            headers['Fineract-Platform-TenantId'] = bindings[binding]
        identity = None
        if config.get('identity_reference'):
            from .deployment_identity import paths
            identity = paths(self.app.store.directory, config['identity_reference'], client=True)
        return Transport(config['origin'], headers, str(identity['ca']) if identity else config.get('ca_file'), identity), config

    def status(self):
        root = Path(__file__).resolve().parents[1]
        definitions = [('banking_core', 'Banking product service', '/fineract-provider/actuator/health'), ('api_contracts', 'Provider contract service', '/api/health'), ('monitoring', 'Transaction monitoring', '/health'), ('risk_studio', 'Monitoring rule authoring', '/health')]
        result = []
        try:
            configuration = self.configuration().get('services', {})
        except (DomainError, ValueError):
            configuration = {}
        for key, name, health in definitions:
            config = configuration.get(key)
            state, detail = 'source_imported', 'Pinned source is included; service binding and qualification are pending.'
            if config:
                try:
                    client = Transport(config['origin'], ca_file=config.get('ca_file'))
                    client.call('GET', health, timeout=1)
                    state, detail = 'connected', 'Configured service responds. Supported operation coverage is recorded separately.'
                except (EngineError, DomainError, KeyError):
                    state, detail = 'unavailable', 'Configured service did not pass its readiness probe.'
            result.append({'key': key, 'name': name, 'state': state, 'source_present': (root / 'engines' / key).is_dir(), 'detail': detail})
        return result

    def core_read(self, tenant, env, resource, identity=None):
        resources = {'clients': 'clients', 'loans': 'loans', 'loan-products': 'loanproducts', 'savings': 'savingsaccounts', 'savings-products': 'savingsproducts', 'offices': 'offices', 'currencies': 'currencies'}
        require(resource in resources, 'INVALID_RESOURCE', 'Unsupported banking product resource.')
        client, _ = self.client('banking_core', tenant, env)
        path = '/fineract-provider/api/v1/' + resources[resource]
        if identity:
            require(isinstance(identity, str) and identity.isdigit(), 'INVALID_ID', 'Core identity must be numeric.')
            path += '/' + identity
            if resource == 'loans':
                path += '?associations=repaymentSchedule,transactions'
        return client.call('GET', path)

    def binding_hash(self, service, tenant, env=None):
        from .util import digest
        _, config = self.client(service, tenant, env)
        binding = tenant + '/' + env if service == 'banking_core' else tenant
        return digest({'service': service, 'origin': config['origin'], 'tenant': config['tenant_bindings'][binding], 'classification': config.get('classification'), 'profile': config.get('profile', 'bank-products/1')})

    def core_command(self, tenant, env, data):
        from .util import canonical, digest, now, reference, uid
        self.app.environment(tenant, env, True)
        action, body, identity = data.get('action'), data.get('body'), data.get('resource_id')
        routes = {'create_client': '/clients', 'create_loan_product': '/loanproducts', 'apply_loan': '/loans', 'create_savings_product': '/savingsproducts', 'apply_savings': '/savingsaccounts', 'approve_loan': '/loans/{id}?command=approve', 'disburse_loan': '/loans/{id}?command=disburse', 'repay_loan': '/loans/{id}/transactions?command=repayment', 'approve_savings': '/savingsaccounts/{id}?command=approve', 'activate_savings': '/savingsaccounts/{id}?command=activate', 'deposit_savings': '/savingsaccounts/{id}/transactions?command=deposit', 'withdraw_savings': '/savingsaccounts/{id}/transactions?command=withdrawal'}
        require(isinstance(action, str) and action in routes and isinstance(body, dict), 'INVALID_COMMAND', 'Use a supported banking command and body.')
        path = routes[action]
        if '{id}' in path:
            require(type(identity) is int and identity > 0, 'INVALID_ID', 'Positive banking resource identity required.')
            path = path.replace('{id}', str(identity))
        client, _ = self.client('banking_core', tenant, env)
        key = reference(data.get('request_key'))
        fingerprint = digest({'action': action, 'body': body, 'resource_id': identity})
        with self.app.store.transaction() as db:
            old = db.execute("SELECT * FROM external_commands WHERE tenant_id=? AND environment_id=? AND service='banking_core' AND request_key=?", (tenant, env, key)).fetchone()
            if old:
                require(old['request_hash'] == fingerprint, 'IDEMPOTENCY_CONFLICT', 'Command identity already has a different payload.', 409)
                return dict(old)
            command_id = uid('command')
            db.execute('INSERT INTO external_commands(id,tenant_id,environment_id,service,action,path,body_json,request_key,request_hash,state,result_json,error,created_at,updated_at,binding_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (command_id, tenant, env, 'banking_core', action, path, canonical(body), key, fingerprint, 'pending', None, None, now(), now(), self.binding_hash('banking_core', tenant, env)))
            self.app.store.enqueue(db, tenant, env, 'core_command', command_id, {}, 'core:' + command_id)
            self.app.store.audit(db, tenant, env, 'core_command.accepted', command_id, {'action': action, 'fingerprint': fingerprint})
            return dict(db.execute('SELECT * FROM external_commands WHERE id=?', (command_id,)).fetchone())

    def reconcile_core(self, tenant, env, identity):
        from .util import now, uid
        with self.app.store.transaction() as db:
            command = self.app.banking._get(db, 'external_commands', tenant, env, identity)
            require(command['service'] == 'banking_core', 'NOT_FOUND', 'Banking command not found.', 404)
            if command['state'] in {'succeeded', 'rejected', 'pending'}:
                return command
            require(not command['lease_until'] or command['lease_until'] < time.time(), 'COMMAND_BUSY', 'Original command is still being observed.', 409)
            require(command['binding_hash'] and command['binding_hash'] == self.binding_hash('banking_core', tenant, env), 'BINDING_CHANGED', 'Original service binding is not available; preserve this command for investigation.', 409)
            db.execute("UPDATE external_commands SET state='pending',error=NULL,updated_at=? WHERE id=?", (now(), identity))
            self.app.store.enqueue(db, tenant, env, 'core_command', identity, {}, 'core-reconcile:' + uid('attempt'))
            self.app.store.audit(db, tenant, env, 'core_command.reconcile_requested', identity, {'fingerprint': command['request_hash']})
            return self.app.banking._get(db, 'external_commands', tenant, env, identity)

    def process_core(self, tenant, env, identity):
        from .util import canonical, now
        with self.app.store.reader() as db:
            command = self.app.banking._get(db, 'external_commands', tenant, env, identity)
        if command['state'] in {'succeeded', 'rejected'}:
            return command
        client, _ = self.client('banking_core', tenant, env)
        if command['binding_hash'] != self.binding_hash('banking_core', tenant, env):
            with self.app.store.transaction() as db:
                db.execute("UPDATE external_commands SET state='unknown',error='Original service binding changed; dispatch is blocked.',updated_at=? WHERE id=?", (now(), identity))
                return self.app.banking._get(db, 'external_commands', tenant, env, identity)
        # Fence before dispatch. A crash or timeout requires explicit reconciliation,
        # never an automatic fresh financial command.
        token = secrets.token_hex(16)
        with self.app.store.transaction() as db:
            if not db.execute("UPDATE external_commands SET state='unknown',updated_at=?,lease_token=?,lease_until=? WHERE id=? AND state IN ('pending','unknown') AND (lease_until IS NULL OR lease_until<?)", (now(), token, time.time()+45, identity, time.time())).rowcount:
                return self.app.banking._get(db, 'external_commands', tenant, env, identity)
        state, result, error = 'unknown', None, None
        try:
            result = client.call('POST', '/fineract-provider/api/v1' + command['path'], json.loads(command['body_json']), {'Idempotency-Key': identity}, timeout=30)
            state = 'succeeded'
        except EngineError as failure:
            error = failure.message
            result = {'errors': getattr(failure, 'public_details', [])}
            if 400 <= failure.status < 500 and failure.status not in {408, 409, 429}:
                state = 'rejected'
        with self.app.store.transaction() as db:
            changed = db.execute('UPDATE external_commands SET state=?,result_json=?,error=?,updated_at=?,lease_token=NULL,lease_until=NULL WHERE id=? AND lease_token=?', (state, canonical(result) if result else None, error, now(), identity, token)).rowcount
            if changed:
                self.app.store.audit(db, tenant, env, 'core_command.' + state, identity, {'action': command['action']})
            return self.app.banking._get(db, 'external_commands', tenant, env, identity)

    def contract_services(self, tenant, env):
        client, _ = self.client('api_contracts', tenant)
        values = client.call('GET', '/api/services?page=0&size=100')
        return [x for x in values if x.get('name', '').startswith('Avenra-' + env + '-')] if isinstance(values, list) else []

    def invoke_contract(self, tenant, env, identity, data):
        client, _ = self.client('api_contracts', tenant)
        response = client.call('GET', '/api/services/' + quote(identity, safe=''))
        service = response.get('service', response)
        require(service.get('name', '').startswith('Avenra-' + env + '-'), 'NOT_FOUND', 'Contract does not belong to this environment.', 404)
        path, method = data.get('path'), data.get('method', 'GET')
        require(isinstance(path, str) and path.startswith('/') and not path.startswith('//') and '..' not in path and all(c.isalnum() or c in '/_.-' for c in path), 'INVALID_PATH', 'Use a bounded mock resource path.')
        require(isinstance(method, str) and method in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}, 'INVALID_METHOD', 'Unsupported mock method.')
        route = '/rest/' + quote(service['name'], safe='') + '/' + quote(service['version'], safe='') + path
        return client.call(method, route, data.get('body'))

    def import_contract(self, tenant, env, data):
        import copy
        from .util import canonical, digest, uid
        self.app.environment(tenant, env, True)
        specification = data.get('specification')
        require(isinstance(specification, dict) and str(specification.get('openapi', '')).startswith('3.') and isinstance(specification.get('info'), dict) and isinstance(specification.get('paths'), dict), 'INVALID_CONTRACT', 'Supply an OpenAPI 3 contract with info and paths.')
        def validate(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    require(not key.lower().startswith('x-microcks-') and key != 'externalValue', 'UNSUPPORTED_CONTRACT', 'Executable extensions and external examples are disabled.')
                    if key == '$ref':
                        require(isinstance(item, str) and item.startswith('#/'), 'EXTERNAL_REFERENCE', 'Resolve external schema references before import.')
                    validate(item)
            elif isinstance(value, list):
                for item in value:
                    validate(item)
        validate(specification)
        specification = copy.deepcopy(specification)
        original_name = specification['info'].get('title', 'Provider')
        require(isinstance(original_name, str) and 1 <= len(original_name) <= 100, 'INVALID_CONTRACT', 'Contract title is invalid.')
        specification['info']['title'] = 'Avenra-' + env + '-' + original_name
        specification['servers'] = [{'url': 'http://127.0.0.1'}]
        for item in specification['paths'].values():
            if not isinstance(item, dict):
                continue
            for method, operation in item.items():
                if method.lower() not in {'get', 'put', 'post', 'delete', 'patch', 'head', 'options'} or not isinstance(operation, dict):
                    continue
                for response in operation.get('responses', {}).values():
                    if not isinstance(response, dict):
                        continue
                    for media in response.get('content', {}).values():
                        if isinstance(media, dict) and 'example' in media and 'examples' not in media:
                            media['examples'] = {'default': {'value': media.pop('example')}}
        payload = canonical(specification).encode()
        require(len(payload) <= 60_000, 'INVALID_CONTRACT', 'Contract exceeds this import profile.')
        client, _ = self.client('api_contracts', tenant)
        boundary = uid('boundary')
        multipart = ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="provider.json"\r\nContent-Type: application/json\r\n\r\n').encode() + payload + ('\r\n--' + boundary + '--\r\n').encode()
        raw = client.call('POST', '/api/artifact/upload?mainArtifact=true', multipart, {'Content-Type': 'multipart/form-data; boundary=' + boundary}, timeout=20, raw=True)
        try:
            result = json.loads(raw)
        except ValueError:
            result = {'message': raw.decode('utf-8', errors='replace')[:2000]}
        with self.app.store.transaction() as db:
            self.app.store.audit(db, tenant, env, 'contract.imported', specification['info']['title'], {'sha256': digest(specification)})
        return {'name': specification['info']['title'], 'result': result, 'sha256': digest(specification)}
