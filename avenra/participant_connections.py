"""Versioned participant adapter boundary and scoped connection revisions.

Owned local and loopback mTLS synthetic adapters execute. Institutional
configuration is a draft, not official acceptance. Raw credentials never enter
this API; the TLS adapter resolves scoped private test-identity references.
"""
import hashlib
import hmac
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .errors import DomainError, require
from .gateways import EngineError
from .lab_identity import CONTRACT, CONTRACT_HASH, TLS_LAB, LabIdentity
from .upi_wire import WIRE_LOCAL, WIRE_TLS, XmlParticipantAdapter, contract as wire_contract
from .participant_peer_contract import ADAPTER as PEER, CONTRACT as PEER_CONTRACT, CONTRACT_HASH as PEER_HASH
from .participant_peer_adapter import PeerParticipantAdapter
from .util import canonical, digest, label, now, reference, uid

LOCAL = 'avenra-local-participant/1'
EXTERNAL = 'avenra-external-upi-pending/1'
SPECIFICATION_SOURCES = [
    {'kind': 'technical_specifications_and_cases', 'route': 'Certification portal and assigned certification contact',
     'url': 'https://www.npcisupport.org.in/portal/en/kb/certification/3-cz-certification-testing-tool-for-upi-aeps-imps-faqs'},
    {'kind': 'operating_and_settlement_guidance', 'route': 'Bank-facing operations portal and partner technical team',
     'url': 'https://www.npcisupport.org.in/portal/en/kb/upi/upi'},
    {'kind': 'vendor_access', 'route': 'Access associated with a participating institution',
     'url': 'https://www.npcisupport.org.in/portal/en/kb/articles/whether-asps-can-have-user-access-to-initiate-track-the-request-for-respective-banks'},
]
EXTERNAL_REQUIREMENTS = [
    {'id': 'wire_contract', 'type': 'specification', 'description': 'Current role/version-specific wire schemas and amendments', 'status': 'required'},
    {'id': 'credential_contract', 'type': 'specification', 'description': 'Applicable device, credential and cryptographic specifications', 'status': 'required'},
    {'id': 'lifecycle_contract', 'type': 'specification', 'description': 'Applicable response codes, deadlines, limits and financial lifecycle rules', 'status': 'required'},
    {'id': 'external_adapter', 'type': 'software', 'description': 'Implemented adapter for the selected institutional interface', 'status': 'not_implemented'},
    {'id': 'institutional_identity', 'type': 'access', 'description': 'Issued test identities, certificates, trust material and endpoint configuration', 'status': 'required'},
    {'id': 'connectivity', 'type': 'access', 'description': 'Authorized connectivity and callback routing', 'status': 'required'},
    {'id': 'acceptance_cases', 'type': 'acceptance', 'description': 'Applicable authoritative cases and observed independent interoperability', 'status': 'not_demonstrated'},
    {'id': 'deployment_qualification', 'type': 'operations', 'description': 'Security, capacity, availability and recovery acceptance for the deployment', 'status': 'not_demonstrated'},
]


def postgres_migration():
    text = Path(__file__).with_name('migrations').joinpath('009_connections.sql').read_text()
    text = re.sub(r"CREATE TRIGGER ([a-z_]+) BEFORE (UPDATE(?: OF [a-z_]+)?|DELETE) ON ([a-z_]+) BEGIN SELECT RAISE\(ABORT,'[^']+'\); END;",
                  lambda m: f'CREATE TRIGGER {m[1]} BEFORE {m[2]} ON {m[3]} FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();', text)
    return re.sub(r'\bINTEGER\b', 'BIGINT', text)


class LocalParticipantAdapter:
    id = LOCAL

    def deliver(self, upi, tenant, env, payment, message, token):
        return upi._handle(tenant, env, payment, message, token)


class TlsSyntheticBankAdapter(LocalParticipantAdapter):
    id = TLS_LAB


class ParticipantConnections:
    def __init__(self, upi):
        self.upi, self.app, self.store = upi, upi.app, upi.store
        # Implementations are registered in trusted code, never uploaded/imported
        # through a configuration document.
        self.adapters = {LOCAL: LocalParticipantAdapter(), TLS_LAB: TlsSyntheticBankAdapter(),
                         WIRE_LOCAL: XmlParticipantAdapter(), WIRE_TLS: XmlParticipantAdapter(), PEER: PeerParticipantAdapter()}

    def profiles(self):
        from .upi import PROFILE, PROFILE_HASH
        return [
            {'id': PROFILE['id'], 'hash': PROFILE_HASH, 'authority': 'synthetic', 'implemented': True,
             'roles': PROFILE['roles'], 'features': PROFILE['features'], 'rules': PROFILE['rules']},
            {'id': EXTERNAL, 'hash': digest({'id': EXTERNAL, 'requirements': EXTERNAL_REQUIREMENTS}),
             'authority': 'unqualified', 'implemented': False, 'roles': PROFILE['roles'],
             'requirements': EXTERNAL_REQUIREMENTS, 'specification_sources': SPECIFICATION_SOURCES},
            {'id': TLS_LAB, 'hash': digest({'id': TLS_LAB, 'flow_profile_hash': PROFILE_HASH, 'transport': CONTRACT}),
             'authority': 'synthetic', 'implemented': True, 'roles': CONTRACT['roles'], 'transport_contract': CONTRACT,
             'scope': 'Bank financial legs execute through separate loopback mTLS services; logical PSP/network actors and the native ledger remain shared lab infrastructure.'},
            {'id': WIRE_LOCAL, 'hash': digest({'id': WIRE_LOCAL, 'wire': wire_contract()}),
             'authority': 'synthetic', 'implemented': True, 'roles': PROFILE['roles'], 'wire_contract': wire_contract(),
             'scope': 'Versioned XML validation and original-message authentication before the same durable in-process participant handler.'},
            {'id': WIRE_TLS, 'hash': digest({'id': WIRE_TLS, 'wire': wire_contract(), 'transport': CONTRACT}),
             'authority': 'synthetic', 'implemented': True, 'roles': CONTRACT['roles'], 'wire_contract': wire_contract(), 'transport_contract': CONTRACT,
             'scope': 'Versioned XML participant validation plus existing independent loopback mTLS bank financial legs. Bank command transport remains the signed JSON bank-leg contract.'},
            {'id': PEER, 'hash': digest({'id': PEER, 'wire': wire_contract(), 'transport': PEER_CONTRACT}),
             'authority':'synthetic','implemented':True,'roles':PEER_CONTRACT['roles'],'wire_contract':wire_contract(),'transport_contract':PEER_CONTRACT,
             'scope':'Independent loopback mTLS PSP/network inboxes retain authenticated XML receipts before existing financial orchestration advances. Receipts do not establish native money movement or official interoperability.'},
        ]

    def _profile(self, adapter):
        return self.profiles()[{LOCAL: 0, EXTERNAL: 1, TLS_LAB: 2, WIRE_LOCAL: 3, WIRE_TLS: 4, PEER: 5}[adapter]]

    def _peer_client(self,tenant,env,participant,configuration):
        from .participant_peer_identity import PeerIdentity
        from .participant_peer_client import PeerClient
        identity=PeerIdentity(self.store.directory,configuration['secret_reference']);document=identity.document
        require((document['tenant_id'],document['environment_id'],document['participant_id'])==(tenant,env,participant),
                'IDENTITY_SCOPE','Private participant identity belongs to another environment or participant.',403)
        return PeerClient(identity,configuration['endpoint'],configuration.get('credential_binding'))

    def _tls_client(self, tenant, env, participant, configuration):
        from .lab_bank_client import LabBankClient
        identity = LabIdentity(self.store.directory, configuration['secret_reference'])
        document = identity.document
        require((document['tenant_id'], document['environment_id'], document['participant_id']) == (tenant, env, participant),
                'IDENTITY_SCOPE', 'Private synthetic identity belongs to another environment or participant.', 403)
        return LabBankClient(identity, configuration['endpoint'], configuration.get('credential_binding'))

    def _get(self, db, table, tenant, env, identity):
        return self.upi.get(db, table, tenant, env, identity)

    def create(self, tenant, env, data):
        from .upi import fields
        fields(data, ['name', 'participant_id', 'adapter_id', 'request_key'], ['endpoint', 'secret_reference', 'institution_reference'])
        self.app.environment(tenant, env, True)
        adapter = data['adapter_id']
        require(isinstance(adapter, str) and adapter in {LOCAL, EXTERNAL, TLS_LAB, WIRE_LOCAL, WIRE_TLS, PEER}, 'ADAPTER_UNSUPPORTED', 'Select a declared participant adapter.')
        participant_id = reference(data['participant_id'])
        config = {}
        if adapter in {LOCAL, WIRE_LOCAL}:
            require(not set(data) & {'endpoint', 'secret_reference', 'institution_reference'}, 'INVALID_CONNECTION', 'The local adapter requires no external settings.')
        else:
            endpoint = data.get('endpoint', '')
            require(isinstance(endpoint, str) and len(endpoint) <= 512 and not any(c.isspace() or ord(c) < 32 for c in endpoint), 'INVALID_CONNECTION', 'Use a bounded HTTPS endpoint.')
            try:
                parsed = urlsplit(endpoint)
                port = parsed.port
            except ValueError:
                raise DomainError('INVALID_CONNECTION', 'Use a valid HTTPS endpoint.')
            require(parsed.scheme == 'https' and parsed.hostname and '@' not in parsed.netloc and not parsed.query and not parsed.fragment and port != 0,
                    'INVALID_CONNECTION', 'Use HTTPS without embedded credentials, queries or fragments.')
            config['endpoint'] = endpoint
            if 'secret_reference' in data:
                secret = data['secret_reference']
                require(isinstance(secret, str) and re.fullmatch(r'secret://[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}', secret), 'INVALID_SECRET_REFERENCE', 'Provide only a secret://name reference; never a password, token or private key.')
                config['secret_reference'] = secret
            if 'institution_reference' in data:
                config['institution_reference'] = reference(data['institution_reference'])
            if adapter in {TLS_LAB, WIRE_TLS}:
                require('secret_reference' in config, 'IDENTITY_REQUIRED', 'Provision a private synthetic bank identity first.')
                client = self._tls_client(tenant, env, participant_id, config)
                config['credential_binding'] = client.identity.binding
                config['transport_hash'] = CONTRACT_HASH
            elif adapter == PEER:
                require('secret_reference' in config,'IDENTITY_REQUIRED','Provision a private independent participant identity first.')
                client=self._peer_client(tenant,env,participant_id,config)
                config['credential_binding']=client.identity.binding;config['transport_hash']=PEER_HASH
        name, key = label(data['name']), reference(data['request_key'])
        fingerprint = digest({'name': name, 'participant_id': participant_id, 'adapter_id': adapter, 'config': config})
        profile = self._profile(adapter)
        from .participant_capabilities import supports_current
        require(adapter not in self.adapters or supports_current(adapter,profile['hash']), 'RELEASE_CAPABILITIES', 'This release does not declare the selected participant contract.', 409)
        with self.store.transaction() as db:
            old = self.app.banking._existing(db, 'participant_connections', tenant, env, key, fingerprint)
            if old:
                return self._public(db, old)
            actor = self._get(db, 'upi_participants', tenant, env, participant_id)
            require(actor['role'] in profile['roles'], 'ROLE_UNSUPPORTED', 'This adapter does not support the participant role.')
            identity = uid('connection')
            db.execute('INSERT INTO participant_connections VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (identity, tenant, env, actor['id'], name, adapter, profile['hash'], canonical(config), digest(config), key, fingerprint, now()))
            self.store.audit(db, tenant, env, 'participant_connection.created', identity, {'adapter_id': adapter, 'config_hash': digest(config)})
            return self._public(db, self._get(db, 'participant_connections', tenant, env, identity))

    def _manifest(self, connection, actor):
        profile = self._profile(connection['adapter_id'])
        from .participant_capabilities import supports_current
        checks = [
            {'name': 'Release declares this exact participant contract', 'passed': supports_current(connection['adapter_id'],connection['profile_hash'])},
            {'name': 'Immutable configuration hash', 'passed': digest(json.loads(connection['config_json'])) == connection['config_hash']},
            {'name': 'Versioned profile hash', 'passed': connection['profile_hash'] == profile['hash']},
            {'name': 'Supported participant role', 'passed': actor['role'] in profile['roles']},
            {'name': 'Executable adapter registered', 'passed': connection['adapter_id'] in self.adapters},
        ]
        if connection['adapter_id'] == EXTERNAL:
            checks += [{'name': x['description'], 'passed': False, 'requirement_id': x['id'], 'type': x['type']} for x in EXTERNAL_REQUIREMENTS]
        return {'format': 'avenra-connection-check/1', 'tenant_id': connection['tenant_id'], 'environment_id': connection['environment_id'],
                'connection_id': connection['id'], 'participant_id': actor['id'], 'adapter_id': connection['adapter_id'],
                'config_hash': connection['config_hash'], 'profile_hash': connection['profile_hash'],
                'application_fingerprint': self.app.source_fingerprint, 'checks': checks,
                'passed': all(x['passed'] for x in checks), 'created_at': now(), 'official_conformance': False,
                'scope': 'Local adapter/configuration validation only. No external requests, credential validation or institutional acceptance is performed.'}

    def _signature(self, manifest_hash):
        return hmac.new(self.store.signing_key, ('connection-check:' + manifest_hash).encode(), hashlib.sha256).hexdigest()

    def validate(self, tenant, env, identity, data):
        from .upi import fields
        fields(data, [])
        with self.store.reader() as db:
            connection = self._get(db, 'participant_connections', tenant, env, identity)
            actor = self._get(db, 'upi_participants', tenant, env, connection['participant_id'])
        manifest = self._manifest(connection, actor)
        if connection['adapter_id'] in {TLS_LAB, WIRE_TLS, PEER}:
            probe, failure = None, None
            try:
                client = (self._peer_client if connection['adapter_id']==PEER else self._tls_client)(tenant, env, actor['id'], json.loads(connection['config_json']))
                probe = client.probe()
            except (DomainError, EngineError, OSError, ValueError, KeyError) as error:
                failure = getattr(error, 'code', 'PRIVATE_IDENTITY_UNAVAILABLE')
            manifest['checks'].append({'name': 'Observed mutual TLS and signed synthetic participant probe', 'passed': probe is not None, 'error_code': failure})
            manifest['probe'] = probe
            manifest['passed'] = all(x['passed'] for x in manifest['checks'])
            manifest['scope'] = 'Observed synthetic bank TLS/signature probe plus configuration validation; no financial dispatch or official interoperability acceptance.'
        with self.store.transaction() as db:
            manifest_hash, check_id = digest(manifest), uid('conncheck')
            db.execute('INSERT INTO participant_connection_checks VALUES(?,?,?,?,?,?,?,?)',
                       (check_id, tenant, env, identity, canonical(manifest), manifest_hash, self._signature(manifest_hash), now()))
            self.store.audit(db, tenant, env, 'participant_connection.checked', identity, {'check_id': check_id, 'passed': manifest['passed'], 'manifest_hash': manifest_hash})
            return {'id': check_id, 'manifest': manifest, 'manifest_hash': manifest_hash, 'signature_valid': True}

    def activate(self, tenant, env, identity, data):
        from .upi import fields
        fields(data, ['check_id'])
        check_id = reference(data['check_id'])
        with self.store.transaction() as db:
            connection = self._get(db, 'participant_connections', tenant, env, identity)
            actor = self._get(db, 'upi_participants', tenant, env, connection['participant_id'])
            current = self._manifest(connection, actor)
            require(current['passed'], 'CONNECTION_UNQUALIFIED', 'This adapter cannot execute. Credentials alone cannot supply its missing implementation or acceptance.', 409)
            receipt = self._get(db, 'participant_connection_checks', tenant, env, check_id)
            manifest = json.loads(receipt['manifest_json'])
            require(receipt['connection_id'] == identity and digest(manifest) == receipt['manifest_hash'] and hmac.compare_digest(receipt['signature'], self._signature(receipt['manifest_hash'])) and
                    manifest['passed'] and manifest['application_fingerprint'] == self.app.source_fingerprint and
                    manifest['config_hash'] == connection['config_hash'] and manifest['profile_hash'] == connection['profile_hash'],
                    'CONNECTION_CHECK_REQUIRED', 'Validate this exact connection revision with the current build before activation.', 409)
            if connection['adapter_id'] in {TLS_LAB, WIRE_TLS, PEER}:
                require(manifest.get('probe') and manifest['probe']['worker_id'], 'CONNECTION_CHECK_REQUIRED', 'An observed secure synthetic bank probe is required.', 409)
            db.execute('INSERT INTO participant_connection_bindings VALUES(?,?,?,?,?) ON CONFLICT(participant_id) DO UPDATE SET connection_id=excluded.connection_id,updated_at=excluded.updated_at',
                       (actor['id'], tenant, env, identity, now()))
            self.store.audit(db, tenant, env, 'participant_connection.activated', identity, {'check_id': receipt['id'], 'participant_id': actor['id']})
            return self._public(db, connection)

    def snapshot(self, db, tenant, env, topology):
        from .participant_capabilities import supports_current
        result = {}
        for role, participant in topology.items():
            binding = db.execute('SELECT connection_id FROM participant_connection_bindings WHERE participant_id=? AND tenant_id=? AND environment_id=?', (participant, tenant, env)).fetchone()
            if binding:
                connection = self._get(db, 'participant_connections', tenant, env, binding['connection_id'])
                require(connection['participant_id'] == participant and connection['adapter_id'] in self.adapters and role in self._profile(connection['adapter_id'])['roles'] and connection['profile_hash'] == self._profile(connection['adapter_id'])['hash'] and supports_current(connection['adapter_id'],connection['profile_hash']) and digest(json.loads(connection['config_json'])) == connection['config_hash'], 'CONNECTION_UNQUALIFIED', 'Participant connection is not executable for this profile.', 409)
                result[role] = {'connection_id': connection['id'], 'adapter_id': connection['adapter_id'], 'config_hash': connection['config_hash'], 'profile_hash': connection['profile_hash']}
            else:
                result[role] = {'connection_id': None, 'adapter_id': LOCAL, 'config_hash': digest({}), 'profile_hash': self.profiles()[0]['hash']}
        return result

    def _pinned(self, tenant, env, payment, role, recipient):
        from .upi import PROFILE_HASH
        snapshot = json.loads(payment['adapter_bindings_json'])
        # Pre-migration intents have no explicit overrides; their historical
        # execution path remains the owned local adapter.
        binding = snapshot.get(role, {'connection_id': None, 'adapter_id': LOCAL, 'config_hash': digest({}), 'profile_hash': PROFILE_HASH})
        require(binding['adapter_id'] in self.adapters and binding['profile_hash'] == self._profile(binding['adapter_id'])['hash'], 'ADAPTER_UNSUPPORTED', 'The pinned participant implementation is unavailable.', 409)
        connection = None
        if binding['connection_id']:
            with self.store.reader() as db:
                connection = self._get(db, 'participant_connections', tenant, env, binding['connection_id'])
            require(connection['participant_id'] == recipient and connection['adapter_id'] == binding['adapter_id'] and connection['config_hash'] == binding['config_hash'] and connection['profile_hash'] == binding['profile_hash'],
                    'CONNECTION_CONFLICT', 'Pinned participant configuration does not match this delivery.', 409)
        return binding, connection

    def deliver(self, tenant, env, payment, message, token):
        from .upi import ROUTES
        binding, _ = self._pinned(tenant, env, payment, ROUTES[message['step']][2], message['recipient'])
        return self.adapters[binding['adapter_id']].deliver(self.upi, tenant, env, payment, message, token)

    def financial_effect(self, tenant, env, payment, operation, kind, ledger):
        with self.store.reader() as db:
            leg = dict(db.execute('SELECT * FROM bank_legs WHERE operation_id=? AND kind=?', (operation['id'], kind)).fetchone())
        if leg['state'] in {'confirmed', 'declined'}:
            return leg['state']
        role = 'beneficiary' if kind == 'credit' else 'remitter'
        topology = json.loads(payment['topology_json'])
        binding, connection = self._pinned(tenant, env, payment, role, topology[role])
        gateway = None
        if binding['adapter_id'] in {TLS_LAB, WIRE_TLS}:
            from .lab_bank_client import LabEffectGateway
            configuration = json.loads(connection['config_json'])
            self._tls_client(tenant, env, topology[role], configuration)
            gateway = LabEffectGateway(self.store.directory, configuration, operation, leg)
        return self.app.banking._effect(operation, kind, ledger, gateway)

    def _public(self, db, connection):
        result = {k: v for k, v in connection.items() if k not in {'config_json', 'request_hash'}}
        result['configuration'] = json.loads(connection['config_json'])
        result['active'] = bool(db.execute('SELECT 1 FROM participant_connection_bindings WHERE participant_id=? AND connection_id=?', (connection['participant_id'], connection['id'])).fetchone())
        result['executable'] = connection['adapter_id'] in self.adapters
        result['official_conformance'] = False
        result['remaining'] = [] if result['executable'] else EXTERNAL_REQUIREMENTS
        receipt = db.execute('SELECT * FROM participant_connection_checks WHERE connection_id=? ORDER BY created_at DESC,id DESC LIMIT 1', (connection['id'],)).fetchone()
        result['last_check'] = None if not receipt else {'id': receipt['id'], 'manifest': json.loads(receipt['manifest_json']),
                                                       'signature_valid': digest(json.loads(receipt['manifest_json'])) == receipt['manifest_hash'] and hmac.compare_digest(receipt['signature'], self._signature(receipt['manifest_hash']))}
        return result

    def overview(self, tenant, env):
        self.app.environment(tenant, env)
        with self.store.reader() as db:
            connections = [self._public(db, dict(x)) for x in db.execute('SELECT * FROM participant_connections WHERE tenant_id=? AND environment_id=? ORDER BY created_at,id', (tenant, env))]
        return {'profiles': self.profiles(), 'connections': connections, 'default_adapter': LOCAL,
                'scope': 'Local participant and loopback mTLS synthetic-bank execution, plus future institutional drafts. Official UPI adapters are not implemented.'}
