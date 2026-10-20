"""Bounded, registered HTTPS/JWS peers; no tenant-supplied network destinations."""
import hashlib
import hmac
from http.client import HTTPException, HTTPSConnection
import json
from pathlib import Path
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPSHandler, ProxyHandler, build_opener
import uuid

from cryptography.hazmat.primitives import serialization

from ..deployment_identity import client_context
from ..errors import DomainError, require
from ..gateways import NoRedirect, EngineError
from ..lab_identity import private_read
from ..util import canonical
from .contracts import VERSIONS, stamp, timestamp, identifier
from .crypto import document, sign, verify


def message(role, **fields): return {'ver': VERSIONS[role], 'timestamp': stamp(), 'txnid': str(uuid.uuid4()), **fields}


def load_public(value):
    key = serialization.load_pem_public_key(value.encode())
    return key


class Client:
    def __init__(self, directory, config, key, kid, role):
        parsed = urlsplit(config['origin'])
        require(parsed.scheme == 'https' and parsed.hostname == '127.0.0.1' and not (parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password), 'AA_PEER_CONFIGURATION', 'Synthetic wire peers require an exact approved loopback HTTPS origin.', 503)
        self.origin, self.config, self.key, self.kid, self.role = config['origin'], config, key, kid, role
        self.directory = Path(directory);self.context = client_context(directory, config['identity_reference'])
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=self.context))
        self.keys = {config['kid']:load_public(config['public_key'])}
        from .routing import resolve
        resolve(self.directory,self.config)  # Restored clients never initialize with implicit source egress.

    def call(self, method, path, body=None, control=False, timeout=8):
        require(path.startswith('/') and not path.startswith('//') and all(c not in path for c in '\r\n?#'), 'InvalidRequest', 'Use a canonical financial-data route.')
        raw = canonical(body).encode() if body is not None else b''
        signed = path.encode() if method == 'GET' else raw
        headers = {'Content-Type':'application/json','Accept':'application/json','x-jws-signature':sign(signed,self.key,self.kid),
                   ('x-avenra-aa-control' if control else 'client_api_key' if self.role == 'aa' else 'aa_api_key'):self.config['control_secret' if control else 'api_key']}
        from .routing import resolve
        route=resolve(self.directory,self.config);connection=None
        try:
            try:
                if route:
                    destination=urlsplit(route['origin'])
                    connection=HTTPSConnection(destination.hostname,destination.port,context=self.context,timeout=timeout)
                    connection.connect()
                    certificate=hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
                    require(certificate==route['server_certificate_sha256'],'RECOVERY_AA_BINDING','The replacement listener does not hold the captured AA TLS identity.',409)
                    self.last_channel={'logical_origin':self.origin,'physical_origin':route['origin'],'set_id':route['set_id'],'server_certificate_sha256':certificate}
                    connection.request(method,self.config.get('prefix','')+path,body=raw if body is not None else None,headers=headers)
                    response=connection.getresponse()
                else:
                    request=Request(self.origin+self.config.get('prefix','')+path,method=method,data=raw if body is not None else None,headers=headers)
                    try:response=self.opener.open(request,timeout=timeout)
                    except HTTPError as error:response=error
            except (URLError,OSError,TimeoutError,HTTPException):raise EngineError(0,'OUTCOME_UNKNOWN','Financial-data peer outcome is unconfirmed; retain the original request identity.')
            with response:
                try: received = response.read(4_000_001)
                except (HTTPException,OSError,TimeoutError):raise EngineError(0,'OUTCOME_UNKNOWN','Financial-data response was incomplete; recover the original request identity.')
                require(len(received) <= 4_000_000, 'InvalidResponse', 'Peer response exceeds its budget.', 502)
                length=response.headers.get('Content-Length')
                if length is not None and (not length.isdigit() or len(received)!=int(length)):
                    raise EngineError(0,'OUTCOME_UNKNOWN','Financial-data response framing was incomplete; recover the original request identity.')
                try:verify(received,response.headers.get('x-jws-signature',''),self.keys)
                except DomainError:
                    # An unsigned gateway error or invalid peer reply cannot
                    # prove that accepted work was rejected by its authority.
                    raise EngineError(0,'OUTCOME_UNKNOWN','Financial-data response authentication is unconfirmed; retain the original request identity.')
                value = document(received)
                if response.status >= 400: raise DomainError(value.get('errorCode','InvalidResponse'),value.get('errorMsg','Peer refused the financial-data operation.'),response.status)
                return value
        finally:
            if connection:connection.close()


def authenticated(config, method, path, raw, headers, current=None, control=False):
    header = 'x-avenra-aa-control' if control else 'aa_api_key'
    expected = config['control_secret' if control else 'api_key']
    require(hmac.compare_digest(headers.get(header,''),expected), 'Unauthorized', 'Registered participant authentication required.', 401)
    verify(path.encode() if method=='GET' else raw, headers.get('x-jws-signature',''), {config['peer_kid']:load_public(config['peer_public_key'])})
    value = document(raw) if method != 'GET' else None
    if value is not None and not control:
        require(identifier(value.get('txnid')), 'InvalidRequest', 'Use a UUID transaction identity.')
        clock=time.time() if current is None else current
        if 'timestamp' in value: require(abs(timestamp(value['timestamp'])-clock)<=300,'InvalidRequest','Message timestamp is outside the selected replay window.')
        elif 'createTimestamp' in value: require(timestamp(value['createTimestamp'])<=clock+300,'InvalidRequest','Consent creation time is in the future.')
    return value
