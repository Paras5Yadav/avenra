"""Bounded mutually authenticated independent participant inbox client."""
import hashlib
import http.client
import ssl
import time
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .errors import DomainError,require
from .gateways import EngineError
from .lab_bank_client import strict_json
from .participant_peer_contract import CONTRACT,CONTRACT_HASH,SCOPE_FIELDS
from .util import canonical,digest,uid


class PeerClient:
    def __init__(self,identity,endpoint=None,expected_binding=None):
        self.identity,self.document=identity,identity.document
        origin=endpoint or self.document['endpoint'];parsed=urlsplit(origin)
        require(parsed.scheme=='https' and parsed.hostname=='127.0.0.1' and parsed.port and not parsed.path and not parsed.query and not parsed.fragment and '@' not in parsed.netloc,
                'LOOPBACK_REQUIRED','Synthetic participant connections require literal loopback HTTPS origins.')
        require(origin==self.document['endpoint'] and (expected_binding is None or expected_binding==identity.binding),
                'IDENTITY_CONFLICT','Participant authority differs from the accepted connection.',409)
        from .peer_recovery import resolve
        self.recovery = resolve(identity)
        physical = urlsplit(self.recovery['origin']) if self.recovery else parsed
        self.host,self.port=physical.hostname,physical.port
        self.context=ssl.create_default_context(ssl.Purpose.SERVER_AUTH,cafile=str(identity.ca));self.context.minimum_version=ssl.TLSVersion.TLSv1_2
        self.context.load_cert_chain(str(identity.directory/self.document['client_cert']),str(identity.directory/self.document['client_key']))
        self.public=Ed25519PublicKey.from_public_bytes(bytes.fromhex(self.document['server_signer_public']));self.receipts=[];self.last_channel=None

    def envelope(self,action,xml=None,virtual_time=0,lose_response=False):
        issued=int(time.time())
        return {'format':'avenra-participant-command/1','transport_hash':CONTRACT_HASH,
                'scope':{k:self.document[k] for k in SCOPE_FIELDS},'request_id':uid('peercommand'),
                'key_id':self.document['client_key_id'],'issued_at':issued,'expires_at':issued+30,
                'action':action,'virtual_time':virtual_time,'message_xml':xml.decode('utf-8') if isinstance(xml,bytes) else xml,
                'lose_response':lose_response}

    def send(self,document,signature=None):
        from .peer_recovery import resolve
        require(resolve(self.identity) == self.recovery, 'RECOVERY_PEER_BINDING',
                'Participant recovery routing changed after client construction; no endpoint was contacted.', 409)
        signature=signature if signature is not None else self.identity.signer.sign(canonical(document).encode()).hex()
        raw=canonical({'document':document,'signature':signature}).encode()
        require(len(raw)<=CONTRACT['maximum_bytes'],'INVALID_COMMAND','Participant command exceeds its bound.',413)
        trace={'request':document,'request_signature':signature,'outcome':'unknown','identity_epoch':self.document['identity_epoch']}
        self.receipts.append(trace)
        connection=http.client.HTTPSConnection(self.host,self.port,context=self.context,timeout=4)
        try:
            connection.connect();self.last_channel={'tls_version':connection.sock.version(),'peer_certificate_sha256':hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()}
            if self.recovery:
                require(self.last_channel['peer_certificate_sha256'] == self.recovery['server_certificate_sha256'],
                        'RECOVERY_PEER_BINDING', 'Recovered TLS server differs from its original certificate.', 409)
            trace['channel']=self.last_channel
            connection.request('POST','/v1/commands',raw,{'Content-Type':'application/json','Content-Length':str(len(raw))})
            response=connection.getresponse();encoded=response.read(CONTRACT['maximum_bytes']+1)
            require(len(encoded)<=CONTRACT['maximum_bytes'],'INVALID_RESPONSE','Participant response exceeds its bound.')
            wrapper=strict_json(encoded)
            require(isinstance(wrapper,dict) and set(wrapper)=={'document','signature'},'INVALID_RESPONSE','Use a signed participant response.')
            result=wrapper['document'];self.public.verify(bytes.fromhex(wrapper['signature']),canonical(result).encode())
            require(isinstance(result,dict) and set(result)=={'format','transport_hash','request_id','request_hash','scope','ok','result','error_code','responded_at'} and
                    result['format']=='avenra-participant-response/1' and result['transport_hash']==CONTRACT_HASH and
                    result['request_id']==document['request_id'] and result['request_hash']==digest(document) and result['scope']==document['scope'] and type(result['ok']) is bool,
                    'INVALID_RESPONSE','Participant response differs from the signed request and scope.')
            trace.update(response=wrapper,outcome='accepted' if result['ok'] else 'rejected')
            if response.status!=200 or not result['ok']:raise EngineError(response.status,result.get('error_code') or 'OUTCOME_UNKNOWN','Participant has not accepted this delivery; preserve its original identity.')
            require(result['error_code'] is None and isinstance(result['result'],dict),'INVALID_RESPONSE','Invalid successful participant acknowledgement.')
            return result['result']
        except EngineError:raise
        except (OSError,TimeoutError,http.client.HTTPException,ValueError,KeyError,TypeError,InvalidSignature,DomainError) as error:
            raise EngineError(0,'OUTCOME_UNKNOWN','Participant receipt is unconfirmed; preserve the original message and financial identities.') from error
        finally:connection.close()

    def probe(self):
        result=self.send(self.envelope('probe'))
        require(result.get('worker_id')==self.document['worker_id'] and result.get('transport_hash')==CONTRACT_HASH and
                result.get('scope')=={k:self.document[k] for k in SCOPE_FIELDS} and result.get('official_conformance') is False,
                'INVALID_RESPONSE','Participant probe differs from its pinned authority.',409)
        return {**result,'channel':self.last_channel,'identity_epoch':self.document['identity_epoch']}

    def deliver(self,raw,virtual_time,lose_response=False):
        from .upi_wire import decode
        document,_=decode(raw);result=self.send(self.envelope('deliver',raw,virtual_time,lose_response))
        require(result.get('format')=='avenra-participant-receipt/1' and result.get('transport_hash')==CONTRACT_HASH and
                result.get('scope')=={k:self.document[k] for k in SCOPE_FIELDS} and result.get('message_id')==document['message_id'] and
                result.get('payment_id')==document['payment_id'] and result.get('document_hash')==digest(document) and
                result.get('wire_sha256')==hashlib.sha256(raw).hexdigest() and result.get('step')==document['step'] and
                result.get('generation')==document['generation'] and result.get('financial_confirmation') is False and
                result.get('official_conformance') is False,'INVALID_RESPONSE','Participant receipt differs from the original message.',409)
        return result
