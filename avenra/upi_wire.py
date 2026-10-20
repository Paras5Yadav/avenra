"""Owned, versioned XML representation of the existing synthetic UPI messages.

Authentication covers the original canonical JSON envelope, not XMLDSIG. This
is deliberately a separate contract from an unavailable official UPI schema.
"""
from functools import lru_cache
import hashlib
import json
import math
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from lxml import etree

from .errors import DomainError, require
from .util import canonical, digest

WIRE_LOCAL = 'avenra-xml-participant/1'
WIRE_TLS = 'avenra-xml-tls-synthetic-bank/1'
NAMESPACE = 'urn:avenra:upi:wire:1'
MEDIA_TYPE = 'application/vnd.avenra.upi.v1+xml'
MAXIMUM = 8192
_Q = '{' + NAMESPACE + '}'
HEADER = {
    'format': 'format', 'profileHash': 'profile_hash', 'tenantId': 'tenant_id',
    'environmentId': 'environment_id', 'paymentId': 'payment_id', 'messageId': 'message_id',
    'step': 'step', 'generation': 'generation', 'sender': 'sender', 'recipient': 'recipient',
    'kind': 'kind', 'keyId': 'key_id', 'issuedAt': 'issued_at', 'expiresAt': 'expires_at',
}
PAYLOAD = {'intentHash': 'intent_hash', 'amountPaise': 'amount_paise', 'currency': 'currency',
           'operationId': 'operation_id', 'outcome': 'outcome'}

# The executable schema is source-bound in the application fingerprint and the
# signed release; it has no includes, imports, external resources or wildcards.
XSD = r'''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
 xmlns:a="urn:avenra:upi:wire:1" targetNamespace="urn:avenra:upi:wire:1"
 elementFormDefault="qualified" attributeFormDefault="unqualified">
 <xs:simpleType name="reference"><xs:restriction base="xs:string"><xs:minLength value="1"/><xs:maxLength value="160"/><xs:pattern value="[A-Za-z0-9_.:@/\\-]+"/></xs:restriction></xs:simpleType>
 <xs:simpleType name="hash"><xs:restriction base="xs:string"><xs:length value="64"/><xs:pattern value="[0-9a-f]+"/></xs:restriction></xs:simpleType>
 <xs:simpleType name="signature"><xs:restriction base="xs:string"><xs:length value="128"/><xs:pattern value="[0-9a-f]+"/></xs:restriction></xs:simpleType>
 <xs:simpleType name="number"><xs:restriction base="xs:string"><xs:maxLength value="32"/></xs:restriction></xs:simpleType>
 <xs:simpleType name="amount"><xs:restriction base="xs:positiveInteger"><xs:maxInclusive value="100000000"/></xs:restriction></xs:simpleType>
 <xs:simpleType name="step"><xs:restriction base="xs:integer"><xs:minInclusive value="-1"/><xs:maxInclusive value="7"/></xs:restriction></xs:simpleType>
 <xs:simpleType name="generation"><xs:restriction base="xs:nonNegativeInteger"><xs:maxInclusive value="1000000"/></xs:restriction></xs:simpleType>
 <xs:simpleType name="kind"><xs:restriction base="xs:string">
  <xs:enumeration value="collect.request"/><xs:enumeration value="collect.present"/>
  <xs:enumeration value="payment.submit"/><xs:enumeration value="debit.request"/>
  <xs:enumeration value="debit.response"/><xs:enumeration value="credit.request"/>
  <xs:enumeration value="credit.response"/><xs:enumeration value="payment.result"/>
  <xs:enumeration value="merchant.notification"/>
 </xs:restriction></xs:simpleType>
 <xs:simpleType name="outcome"><xs:restriction base="xs:string"><xs:enumeration value="confirmed"/><xs:enumeration value="declined"/><xs:enumeration value="succeeded"/><xs:enumeration value="reversed"/></xs:restriction></xs:simpleType>
 <xs:element name="Message"><xs:complexType><xs:sequence>
  <xs:element name="Header"><xs:complexType>
   <xs:attribute name="format" use="required" fixed="avenra-payment-message/1"/>
   <xs:attribute name="profileHash" type="a:hash" use="required"/>
   <xs:attribute name="tenantId" type="a:reference" use="required"/>
   <xs:attribute name="environmentId" type="a:reference" use="required"/>
   <xs:attribute name="paymentId" type="a:reference" use="required"/>
   <xs:attribute name="messageId" type="a:reference" use="required"/>
   <xs:attribute name="step" type="a:step" use="required"/>
   <xs:attribute name="generation" type="a:generation" use="required"/>
   <xs:attribute name="sender" type="a:reference" use="required"/>
   <xs:attribute name="recipient" type="a:reference" use="required"/>
   <xs:attribute name="kind" type="a:kind" use="required"/>
   <xs:attribute name="keyId" type="a:reference" use="required"/>
   <xs:attribute name="issuedAt" type="a:number" use="required"/>
   <xs:attribute name="expiresAt" type="a:number" use="required"/>
  </xs:complexType></xs:element>
  <xs:element name="Payload"><xs:complexType>
   <xs:attribute name="intentHash" type="a:hash" use="required"/>
   <xs:attribute name="amountPaise" type="a:amount" use="required"/>
   <xs:attribute name="currency" use="required" fixed="INR"/>
   <xs:attribute name="operationId" type="a:reference"/>
   <xs:attribute name="outcome" type="a:outcome"/>
  </xs:complexType></xs:element>
  <xs:element name="Authentication"><xs:complexType><xs:simpleContent><xs:extension base="a:signature">
   <xs:attribute name="algorithm" use="required" fixed="Ed25519-canonical-json"/>
  </xs:extension></xs:simpleContent></xs:complexType></xs:element>
 </xs:sequence><xs:attribute name="version" use="required" fixed="1"/>
 <xs:attribute name="contractHash" type="a:hash" use="required"/>
 </xs:complexType></xs:element>
</xs:schema>'''


def contract():
    from .upi import PROFILE_HASH
    return {'id': 'avenra-upi-xml/1', 'authority': 'synthetic', 'namespace': NAMESPACE,
            'media_type': MEDIA_TYPE, 'maximum_bytes': MAXIMUM, 'flow_profile_hash': PROFILE_HASH,
            'schema_sha256': hashlib.sha256(XSD.encode()).hexdigest(),
            'authentication': 'Original Ed25519 signature over the canonical JSON envelope; not XMLDSIG.',
            'rules': ['exact_fields', 'no_dtd_entities_processing_instructions_or_comments',
                      'integer_paise', 'canonical_json_numeric_lexemes', 'original_message_identity',
                      'original_scoped_public_key', 'pinned_flow_profile', 'no_implicit_protocol_fallback'],
            'official_conformance': False}


@lru_cache(maxsize=1)
def schema():
    return etree.XMLSchema(etree.fromstring(XSD.encode()))


def _number(text, integer=False):
    require(isinstance(text, str) and len(text) <= 32, 'UPI_WIRE_SCHEMA', 'Invalid wire numeric field.')
    try:
        value = json.loads(text)
        valid = type(value) in ({int} if integer else {int, float}) and math.isfinite(value)
        valid = valid and canonical(value) == text
    except (ValueError, TypeError, OverflowError):
        valid = False
    require(valid, 'UPI_WIRE_SCHEMA', 'Use a finite canonical JSON number of the declared type.')
    return value


def _validate(document, signature):
    from .upi import PROFILE, PROFILE_HASH, ROUTES
    require(isinstance(document, dict) and set(document) == set(HEADER.values()) | {'payload'},
            'UPI_WIRE_SCHEMA', 'Wire envelope fields differ from this contract.')
    payload = document['payload']
    require(isinstance(payload, dict) and set(payload) == set(PAYLOAD.values()),
            'UPI_WIRE_SCHEMA', 'Wire payload fields differ from this contract.')
    require(document['format'] == PROFILE['message_format'] and document['profile_hash'] == PROFILE_HASH,
            'UPI_WIRE_VERSION', 'Use the selected immutable synthetic flow profile.', 409)
    require(type(document['step']) is int and document['step'] in ROUTES and
            type(document['generation']) is int and 0 <= document['generation'] <= 1000000 and
            document['kind'] == ROUTES[document['step']][0], 'UPI_WIRE_ROUTE', 'Message kind and route step disagree.')
    for key in set(HEADER.values()) - {'step', 'generation', 'issued_at', 'expires_at', 'format', 'profile_hash', 'kind'}:
        require(isinstance(document[key], str) and re.fullmatch(r'[A-Za-z0-9_.:@/\-]{1,160}', document[key]),
                'UPI_WIRE_SCHEMA', 'Invalid wire identity field.')
    require(document['sender'] != document['recipient'] and document['key_id'].startswith(document['sender'] + '_k'),
            'UPI_WIRE_ROUTE', 'Signing key and participant route disagree.')
    for key in ['issued_at', 'expires_at']:
        value = document[key]
        require(type(value) in {int, float} and 0 <= value <= 9007199254740991 and math.isfinite(value),
                'UPI_WIRE_SCHEMA', 'Invalid wire validity interval.')
    require(document['expires_at'] - document['issued_at'] == PROFILE['message_lifetime_seconds'],
            'UPI_WIRE_SCHEMA', 'The wire validity interval differs from the selected profile.')
    require(type(payload['amount_paise']) is int and 1 <= payload['amount_paise'] <= 100000000 and
            payload['currency'] == 'INR' and isinstance(payload['intent_hash'], str) and
            re.fullmatch('[0-9a-f]{64}', payload['intent_hash']), 'UPI_WIRE_SCHEMA', 'Invalid financial payload.')
    operation, outcome, step = payload['operation_id'], payload['outcome'], document['step']
    require(operation is None or (isinstance(operation, str) and re.fullmatch(r'[A-Za-z0-9_.:@/\-]{1,160}', operation)),
            'UPI_WIRE_SCHEMA', 'Invalid financial operation identity.')
    expected = {None} if step in {-1, 0, 1, 2} else {'confirmed', 'declined'} if step == 3 else {'confirmed'} if step == 4 else {'succeeded', 'declined', 'reversed'}
    require((operation is None) == (step in {-1, 0}) and isinstance(outcome, (str, type(None))) and outcome in expected,
            'UPI_WIRE_ROUTE', 'Financial operation and result disagree with the message phase.')
    require(isinstance(signature, str) and re.fullmatch('[0-9a-f]{128}', signature),
            'UPI_WIRE_SIGNATURE', 'Use the original Ed25519 message signature.', 403)


def encode(document, signature):
    _validate(document, signature)
    root = etree.Element(_Q + 'Message', nsmap={'a': NAMESPACE}, version='1', contractHash=digest(contract()))
    header = etree.SubElement(root, _Q + 'Header')
    for attribute, field in HEADER.items():
        value = document[field]
        header.set(attribute, canonical(value) if type(value) in {int, float} else value)
    payload = etree.SubElement(root, _Q + 'Payload')
    for attribute, field in PAYLOAD.items():
        value = document['payload'][field]
        if value is not None:payload.set(attribute, str(value))
    auth = etree.SubElement(root, _Q + 'Authentication', algorithm='Ed25519-canonical-json')
    auth.text = signature
    raw = etree.tostring(root, encoding='UTF-8')
    require(len(raw) <= MAXIMUM, 'UPI_WIRE_SIZE', 'Wire message exceeds the selected contract limit.', 413)
    return raw


def decode(raw):
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAXIMUM, 'UPI_WIRE_SIZE', 'Use a bounded XML wire message.', 413)
    try:
        text = raw.decode('utf-8')
        require(not text.startswith('\ufeff') and '<!' not in text and '<?' not in text,
                'UPI_WIRE_XML', 'Declarations, DTDs, entities, comments and processing instructions are unsupported.')
        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True,
                                 huge_tree=False, recover=False, remove_comments=False, remove_pis=False)
        root = etree.fromstring(raw, parser=parser)
        require(schema().validate(root), 'UPI_WIRE_SCHEMA', 'XML does not match the selected wire schema.')
        require(root.get('contractHash') == digest(contract()), 'UPI_WIRE_VERSION', 'Unknown or altered wire contract.', 409)
        header, payload, auth = root
        document = {field: header.get(attribute) for attribute, field in HEADER.items()}
        for field in ['step', 'generation', 'issued_at', 'expires_at']:
            document[field] = _number(document[field], field in {'step', 'generation'})
        document['payload'] = {field: payload.get(attribute) for attribute, field in PAYLOAD.items()}
        document['payload']['amount_paise'] = _number(payload.get('amountPaise'), True)
        signature = auth.text
        _validate(document, signature)
        return document, signature
    except (UnicodeError, etree.XMLSyntaxError, ValueError, TypeError) as error:
        raise DomainError('UPI_WIRE_XML', 'Malformed UTF-8 XML wire message.') from error


def authenticate(raw, public_key):
    document, signature = decode(raw)
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(bytes.fromhex(signature), canonical(document).encode())
    except (ValueError, TypeError, InvalidSignature) as error:
        raise DomainError('UPI_WIRE_SIGNATURE', 'Wire envelope authentication failed.', 403) from error
    return document, signature


class XmlParticipantAdapter:
    """Execute the original durable handler after a real encode/parse/auth boundary."""
    def deliver(self, upi, tenant, env, payment, message, token):
        raw = encode(json.loads(message['document_json']), message['signature'])
        with upi.store.transaction() as db:
            key = db.execute('SELECT public_key FROM upi_keys WHERE id=? AND participant_id=?', (message['key_id'], message['sender'])).fetchone()
            require(key, 'UPI_WIRE_SIGNATURE', 'Original scoped verification key is unavailable.', 403)
            document, signature = authenticate(raw, key['public_key'])
            upi._verify(db, payment, document, signature)
            require(db.execute('SELECT 1 FROM upi_payments WHERE id=? AND tenant_id=? AND environment_id=? AND lease_token=?',
                               (payment['id'], tenant, env, token)).fetchone(), 'PAYMENT_BUSY', 'Participant delivery lease changed.', 409)
            # A crash after validation and before the financial handler reuses
            # this exact message and proof. It never makes a second money ID.
            if not db.execute("SELECT 1 FROM upi_events WHERE message_id=? AND event='wire.validated'", (message['id'],)).fetchone():
                upi._event(db, payment, message['id'], 'wire.validated',
                           {'contract_hash': digest(contract()), 'wire_sha256': hashlib.sha256(raw).hexdigest(),
                            'wire_bytes': len(raw), 'document_hash': digest(document), 'key_id': document['key_id']})
        decoded = {**dict(message), 'document_json': canonical(document), 'signature': signature}
        return upi._handle(tenant, env, payment, decoded, token)


def export(upi, tenant, env, identity):
    upi.app.environment(tenant, env)
    with upi.store.reader() as db:
        message = upi.get(db, 'upi_envelopes', tenant, env, identity)
    return encode(json.loads(message['document_json']), message['signature'])


def receive(upi, tenant, env, raw):
    upi.app.environment(tenant, env)
    document, signature = decode(raw)
    require((document['tenant_id'], document['environment_id']) == (tenant, env),
            'UPI_WIRE_SCOPE', 'Wire envelope belongs to another workspace or environment.', 403)
    with upi.store.reader() as db:
        key = db.execute('SELECT k.public_key FROM upi_keys k JOIN upi_participants p ON p.id=k.participant_id WHERE k.id=? AND p.id=? AND p.tenant_id=? AND p.environment_id=?',
                         (document['key_id'], document['sender'], tenant, env)).fetchone()
        require(key, 'UPI_WIRE_SIGNATURE', 'Original scoped verification key is unavailable.', 403)
    authenticate(raw, key['public_key'])
    # Preserve the established API semantics: inspect an already accepted
    # immutable delivery; only the durable worker executes financial effects.
    receipt = upi.receive(tenant, env, {'envelope': document, 'signature': signature})
    return {**receipt, 'wire_contract_hash': digest(contract()), 'wire_sha256': hashlib.sha256(raw).hexdigest(),
            'financial_dispatch': False, 'official_conformance': False}


def read_body(handler):
    require(handler.headers.get('Content-Type', '').lower() in {MEDIA_TYPE, MEDIA_TYPE + '; charset=utf-8'},
            'CONTENT_TYPE', 'Use the advertised versioned UPI XML media type and UTF-8.', 415)
    require(not handler.headers.get('Content-Encoding') and not handler.headers.get('Transfer-Encoding'),
            'INVALID_BODY', 'Encoded and chunked wire bodies are unsupported.')
    lengths = handler.headers.get_all('Content-Length') or []
    require(len(lengths) == 1 and lengths[0].isdigit() and len(lengths[0]) <= 5,
            'INVALID_BODY', 'Use one bounded Content-Length.')
    size = int(lengths[0])
    require(0 < size <= MAXIMUM, 'UPI_WIRE_SIZE', 'Wire message exceeds the selected contract limit.', 413)
    raw = handler.rfile.read(size)
    require(len(raw) == size, 'INVALID_BODY', 'Wire body length differs from its framing.')
    return raw
