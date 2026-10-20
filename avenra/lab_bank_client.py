"""Bounded mutually authenticated client for the synthetic bank contract."""
from contextlib import contextmanager
import http.client
import hashlib
import json
import ssl
import time
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .errors import DomainError, require
from .gateways import EngineError
from .lab_identity import CONTRACT_HASH, LabIdentity
from .util import canonical, digest, uid

SCOPE_FIELDS = ['worker_id', 'bank_id', 'tenant_id', 'environment_id', 'participant_id', 'role', 'ledger_name']


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:raise ValueError('Duplicate JSON field')
            value[key] = item
        return value
    def constant(_):raise ValueError('Non-finite JSON')
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    canonical(value)  # Refuse overflowing numeric values as well.
    return value


class LabBankClient:
    def __init__(self, identity, endpoint=None, expected_binding=None):
        self.identity, self.document = identity, identity.document
        origin = endpoint or self.document['endpoint']
        parsed = urlsplit(origin)
        require(parsed.scheme == 'https' and parsed.hostname == '127.0.0.1' and parsed.port and not parsed.path and not parsed.query and not parsed.fragment and '@' not in parsed.netloc,
                'LOOPBACK_REQUIRED', 'Synthetic bank connections require a literal loopback HTTPS origin.')
        require(origin == self.document['endpoint'] and (expected_binding is None or identity.binding == expected_binding), 'IDENTITY_CONFLICT', 'Synthetic bank authority differs from the pinned connection.', 409)
        from .bank_recovery import resolve
        self.recovery_route = resolve(identity)
        if self.recovery_route: parsed = urlsplit(self.recovery_route['origin'])
        self.host, self.port = parsed.hostname, parsed.port
        self.context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(identity.ca))
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.load_cert_chain(str(identity.directory / self.document['client_cert']), str(identity.directory / self.document['client_key']))
        self.public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(self.document['server_signer_public']))
        self.last_channel = None
        self.receipts = []

    def envelope(self, action, effect=None, lose_response=False):
        issued = int(time.time())
        return {'format': 'avenra-bank-command/1', 'transport_hash': CONTRACT_HASH,
                'scope': {k: self.document[k] for k in SCOPE_FIELDS}, 'request_id': uid('command'),
                'key_id': self.document['client_key_id'], 'issued_at': issued, 'expires_at': issued + 30,
                'action': action, 'effect': effect, 'lose_response': lose_response}

    def send(self, document, signature=None):
        signature = signature if signature is not None else self.identity.signer.sign(canonical(document).encode()).hex()
        encoded = canonical({'document': document, 'signature': signature}).encode()
        trace = {'request': document, 'request_signature': signature, 'client_signer_public': self.document['client_signer_public'],
                 'server_signer_public': self.document['server_signer_public'], 'identity_epoch': self.document['identity_epoch'], 'outcome': 'unknown'}
        self.receipts.append(trace)
        connection = http.client.HTTPSConnection(self.host, self.port, context=self.context, timeout=4)
        try:
            connection.connect()
            self.last_channel = {'tls_version': connection.sock.version(), 'peer_certificate_sha256': hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()}
            if self.recovery_route:
                require(self.last_channel['peer_certificate_sha256'] == self.recovery_route['server_certificate_sha256'],
                        'RECOVERY_BANK_BINDING', 'The recovered listener must retain the original bank TLS identity.', 409)
                self.last_channel.update(logical_endpoint=self.document['endpoint'], physical_endpoint=self.recovery_route['origin'], recovery_set_id=self.recovery_route['set_id'])
            trace['channel'] = self.last_channel
            connection.request('POST', '/v1/commands', encoded, {'Content-Type': 'application/json', 'Content-Length': str(len(encoded))})
            response = connection.getresponse()
            raw = response.read(32769)
            require(len(raw) <= 32768, 'INVALID_RESPONSE', 'Synthetic bank response exceeds its limit.')
            wrapper = strict_json(raw)
            require(isinstance(wrapper, dict) and set(wrapper) == {'document', 'signature'}, 'INVALID_RESPONSE', 'Use a signed bank response.')
            result = wrapper['document']
            self.public.verify(bytes.fromhex(wrapper['signature']), canonical(result).encode())
            require(result['request_hash'] == digest(document) and result['request_id'] == document['request_id'] and result['scope'] == document['scope'] and result['transport_hash'] == CONTRACT_HASH,
                    'INVALID_RESPONSE', 'Bank response does not match the request and scope.')
            trace.update(response=wrapper, outcome='accepted' if result['ok'] else 'rejected')
            if response.status != 200 or not result['ok']:
                raise EngineError(response.status, result.get('error_code', 'OUTCOME_UNKNOWN'), 'Synthetic bank rejected or could not confirm the request.')
            return result['result']
        except EngineError:
            raise
        except (OSError, TimeoutError, http.client.HTTPException, ValueError, KeyError, TypeError, InvalidSignature, DomainError) as error:
            raise EngineError(0, 'OUTCOME_UNKNOWN', 'Synthetic bank response is unconfirmed; retain the original financial identity.') from error
        finally:
            connection.close()

    def request(self, action, effect=None, lose_response=False):
        return self.send(self.envelope(action, effect, lose_response))

    def probe(self):
        result = self.request('probe')
        require(result['worker_id'] == self.document['worker_id'] and result['transport_hash'] == CONTRACT_HASH and result['official_conformance'] is False,
                'INVALID_RESPONSE', 'Synthetic bank probe returned a different authority.', 409)
        return {**result, 'channel': self.last_channel, 'credential_epoch': self.document['identity_epoch']}


class LabEffectGateway:
    def __init__(self, directory, configuration, operation, leg):
        identity = LabIdentity(directory, configuration['secret_reference'])
        self.client = LabBankClient(identity, configuration['endpoint'], configuration['credential_binding'])
        self.operation, self.leg = operation, leg
        self.body = {'postings': [{'source': leg['source'], 'destination': leg['destination'], 'amount': leg['amount_paise'], 'asset': 'INR/2'}],
                     'reference': leg['id'], 'metadata': {'operation_id': operation['id'], 'leg': leg['kind'], 'profile': operation['profile']}}

    @property
    def receipts(self):return self.client.receipts

    def _effect(self, ledger, reference):
        require(ledger == self.client.document['ledger_name'] and reference == self.leg['id'], 'CONNECTION_CONFLICT', 'Financial query differs from the pinned bank scope.', 409)
        return {'operation_id': self.operation['id'], 'leg_id': self.leg['id'], 'leg_kind': self.leg['kind'], 'body': self.body}

    def transactions(self, ledger, reference):
        return self.client.request('status', self._effect(ledger, reference))['transactions']

    def post(self, ledger, body, reference, origin=None):
        require(body == self.body, 'CONNECTION_CONFLICT', 'Financial dispatch differs from the accepted bank leg.', 409)
        return self.client.request('post', self._effect(ledger, reference), lose_response=origin is True)['transaction']

    @contextmanager
    def lost_response(self, identity):
        yield True


def verify_trace(trace):
    try:
        request = trace['request']
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(trace['client_signer_public'])).verify(bytes.fromhex(trace['request_signature']), canonical(request).encode())
        if 'response' in trace:
            wrapper = trace['response']
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(trace['server_signer_public'])).verify(bytes.fromhex(wrapper['signature']), canonical(wrapper['document']).encode())
            if wrapper['document']['request_hash'] != digest(request) or wrapper['document']['scope'] != request['scope'] or wrapper['document']['request_id'] != request['request_id']:return False
        channel = trace.get('channel', {})
        transport_valid = channel.get('tls_version') in {'TLSv1.2','TLSv1.3'} or (not channel and trace['outcome'] == 'unknown' and 'response' not in trace)
        return request['transport_hash'] == CONTRACT_HASH and transport_valid
    except (KeyError, ValueError, TypeError, InvalidSignature):return False
