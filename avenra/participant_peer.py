"""Independently deployed scoped participant inbox; never owns bank balances."""
import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import ssl
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .errors import DomainError, require
from .lab_bank_client import strict_json
from .lab_identity import bank_service_lease, private_read
from .participant_peer_contract import CONTRACT, CONTRACT_HASH, RECEIVERS, SCOPE_FIELDS
from .upi_wire import authenticate, decode
from .util import canonical, digest, now


class ParticipantPeer:
    def __init__(self, configuration):
        self.path = Path(configuration).absolute()
        require(not self.path.is_symlink() and not self.path.parent.is_symlink(),
                'PRIVATE_IDENTITY_REQUIRED', 'Participant paths cannot be links.', 409)
        self.directory = self.path.parent
        from .recovery_state import require_active
        from .private_config import read_document
        self.installation=self.directory.parent.parent
        require_active(self.installation)
        require(not read_document(self.installation/'deployment.json',{}).get('recovery'),
                'RECOVERY_PARTICIPANT_UNQUALIFIED','Independent participant cold address rebinding is not yet qualified.',409)
        config = self.configuration()
        self.signer = Ed25519PrivateKey.from_private_bytes(private_read(self.directory/'server-signer.raw'))
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        require(self.signer.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex() == config['server_signer_public'],
                'IDENTITY_CONFLICT', 'Participant signing identity differs from its configured public key.', 409)
        self.capacity = asyncio.Semaphore(8)
        self.database_path = self.directory/'participant.sqlite'
        if self.database_path.exists():
            self._private_database()
        with self.database() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,
                  response_json TEXT,status INTEGER,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS inbox(id TEXT PRIMARY KEY,document_hash TEXT NOT NULL,
                  wire_hash TEXT NOT NULL,receipt_json TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS payment_state(id TEXT PRIMARY KEY,core_json TEXT NOT NULL,
                  operation_id TEXT,state TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS phases(payment_id TEXT NOT NULL,step INTEGER NOT NULL,
                  payload_hash TEXT NOT NULL,first_message_id TEXT NOT NULL,
                  PRIMARY KEY(payment_id,step));
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,message_id TEXT NOT NULL UNIQUE,
                  event TEXT NOT NULL,receipt_hash TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS immutable_inbox_update BEFORE UPDATE ON inbox BEGIN SELECT RAISE(ABORT,'Immutable inbox'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_inbox_delete BEFORE DELETE ON inbox BEGIN SELECT RAISE(ABORT,'Immutable inbox'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_phase_update BEFORE UPDATE ON phases BEGIN SELECT RAISE(ABORT,'Immutable phase'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_phase_delete BEFORE DELETE ON phases BEGIN SELECT RAISE(ABORT,'Immutable phase'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_event_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'Immutable event'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_event_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'Immutable event'); END;''')
        self.database_path.chmod(0o600)

    def _private_database(self):
        import os, stat
        info = self.database_path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and not info.st_mode & 0o077,
                'PRIVATE_IDENTITY_REQUIRED', 'Inbox requires private ordinary ownership.', 409)

    def configuration(self):
        config = strict_json(private_read(self.path))
        require(config.get('transport_hash') == CONTRACT_HASH and config.get('role') in RECEIVERS,
                'PROFILE_UNSUPPORTED', 'Select the declared synthetic participant transport.', 409)
        return config

    @contextmanager
    def database(self):
        if self.database_path.exists(): self._private_database()
        db = sqlite3.connect(self.database_path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db: yield db
        finally: db.close()

    def _verify(self, wrapper):
        require(isinstance(wrapper,dict) and set(wrapper)=={'document','signature'}, 'INVALID_COMMAND', 'Use a signed participant command.')
        document = wrapper['document']
        require(isinstance(document,dict) and set(document)=={'format','transport_hash','scope','request_id','key_id','issued_at','expires_at','action','virtual_time','message_xml','lose_response'},
                'INVALID_COMMAND', 'Participant command fields differ from the declared transport.')
        require(document['format']=='avenra-participant-command/1' and document['transport_hash']==CONTRACT_HASH,
                'PROFILE_UNSUPPORTED', 'Participant transport version differs.', 409)
        config = self.configuration()
        key_id = document['key_id']
        require(isinstance(key_id,str) and key_id in config['clients'], 'SIGNING_KEY_UNKNOWN', 'Unknown participant transport identity.', 403)
        key = config['clients'][key_id]
        require(not key['revoked'], 'SIGNING_KEY_REVOKED', 'Participant transport identity is revoked.', 403)
        try:
            signature = wrapper['signature']
            require(isinstance(signature,str) and re.fullmatch('[0-9a-f]{128}',signature), 'SIGNATURE_INVALID', 'Invalid transport signature.', 403)
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(key['public_key'])).verify(bytes.fromhex(signature),canonical(document).encode())
        except (ValueError,InvalidSignature) as error:
            raise DomainError('SIGNATURE_INVALID','Participant transport signature failed.',403) from error
        require(document['scope']=={k:config[k] for k in SCOPE_FIELDS}, 'PARTICIPANT_SCOPE', 'Command belongs to another participant.', 403)
        issued,expires=document['issued_at'],document['expires_at']
        require(type(issued) is int and type(expires) is int and 0<expires-issued<=30 and issued<=time.time()+5 and time.time()<expires,
                'COMMAND_EXPIRED','Participant command is outside its validity interval.',409)
        require(isinstance(document['request_id'],str) and re.fullmatch('peercommand_[0-9a-f]{20}',document['request_id']),
                'INVALID_COMMAND','Use a bounded participant transport identity.')
        require(document['action'] in {'probe','deliver'} and type(document['lose_response']) is bool and
                (not document['lose_response'] or document['action']=='deliver'), 'INVALID_COMMAND','Unsupported participant command or fault.')
        virtual=document['virtual_time']
        require(type(virtual) in {int,float} and math.isfinite(virtual) and 0<=virtual<=9007199254740991,
                'INVALID_COMMAND','Use the explicitly signed synthetic scenario clock.')
        require((document['action']=='probe' and document['message_xml'] is None) or
                (document['action']=='deliver' and isinstance(document['message_xml'],str) and 0<len(document['message_xml'])<=8192),
                'INVALID_COMMAND','Use the original bounded XML message for delivery.')
        return document,config

    def _accept(self, command, config):
        raw=command['message_xml'].encode('utf-8')
        document,signature=decode(raw)
        require((document['tenant_id'],document['environment_id'],document['recipient'])==
                (config['tenant_id'],config['environment_id'],config['participant_id']),
                'PARTICIPANT_SCOPE','XML is outside this participant scope.',403)
        message_id=document['message_id'];wire_hash=hashlib.sha256(raw).hexdigest()
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM inbox WHERE id=?',(message_id,)).fetchone()
            if old:
                require(old['wire_hash']==wire_hash and old['document_hash']==digest(document),
                        'MESSAGE_CONFLICT','Original participant message identity has different bytes.',409)
                return strict_json(old['receipt_json'])
            registry=config['registry'];key=registry.get(document['key_id'])
            require(key and key['participant_id']==document['sender'] and not key['revoked'],
                    'KEY_REVOKED','Original sender verification key is unavailable or revoked.',403)
            authenticate(raw,key['public_key'])
            policy=RECEIVERS[config['role']].get(document['step'])
            require(policy and (key['role'],document['kind'])==policy[:2],
                    'MESSAGE_ROUTE','Message kind or sender role cannot enter this participant.',403)
            require(document['issued_at']<=command['virtual_time']<document['expires_at'],
                    'MESSAGE_EXPIRED','A new participant acceptance requires an unexpired message.',409)
            payload=document['payload'];payment=document['payment_id'];step=document['step']
            core={k:payload[k] for k in ['intent_hash','amount_paise','currency']}
            state=db.execute('SELECT * FROM payment_state WHERE id=?',(payment,)).fetchone()
            require(not state or strict_json(state['core_json'])==core, 'PAYMENT_CONFLICT','Immutable participant payment content changed.',409)
            require(not state or not state['operation_id'] or state['operation_id']==payload['operation_id'],
                    'PAYMENT_CONFLICT','Participant payment references another financial operation.',409)
            phase=db.execute('SELECT * FROM phases WHERE payment_id=? AND step=?',(payment,step)).fetchone()
            require(not phase or phase['payload_hash']==digest(payload), 'MESSAGE_CONFLICT','Participant phase has conflicting immutable content.',409)
            if not phase and config['role']=='network' and step in {3,5}:
                previous=db.execute('SELECT payload_hash FROM phases WHERE payment_id=? AND step=?',(payment,1 if step==3 else 3)).fetchone()
                require(previous is not None and (step!=5 or state['state']=='debit_confirmed'),
                        'MESSAGE_ORDER','Network has not accepted the prerequisite phase.',409)
            if not phase and state:
                allowed = ((config['role']=='network' and step==1 and state['state']=='collect_received') or
                           (config['role']=='network' and step==3 and state['state']=='submission_received') or
                           (config['role']=='network' and step==5 and state['state']=='debit_confirmed') or
                           (config['role']=='payer_psp' and step==6 and state['state']=='collect_presented'))
                require(allowed, 'MESSAGE_ORDER','This new phase would rewind or contradict the participant observation.',409)
            require(db.execute('SELECT count(*) FROM inbox').fetchone()[0]<CONTRACT['maximum_inbox_messages'],
                    'PARTICIPANT_CAPACITY','Participant inbox is full; preserve the original identity and retry after operator action.',503)
            next_state=policy[2]
            if config['role']=='network' and step==3:next_state='debit_'+payload['outcome']
            if config['role']=='network' and step==5:next_state='result_'+payload['outcome']
            # A new generation of an already accepted phase is not permission
            # to rewind the participant's current observation.
            if not phase:
                db.execute('INSERT INTO phases VALUES(?,?,?,?)',(payment,step,digest(payload),message_id))
                db.execute('INSERT INTO payment_state VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET operation_id=COALESCE(payment_state.operation_id,excluded.operation_id),state=excluded.state,updated_at=excluded.updated_at',
                           (payment,canonical(core),payload['operation_id'],next_state,now()))
            else:next_state=state['state']
            receipt={'format':'avenra-participant-receipt/1','transport_hash':CONTRACT_HASH,
                     'scope':{k:config[k] for k in SCOPE_FIELDS},'message_id':message_id,
                     'payment_id':payment,'document_hash':digest(document),'wire_sha256':wire_hash,
                     'step':step,'generation':document['generation'],'observed_state':next_state,
                     'accepted_virtual_time':command['virtual_time'],'accepted_at':now(),
                     'financial_confirmation':False,'official_conformance':False}
            db.execute('INSERT INTO inbox VALUES(?,?,?,?,?)',(message_id,digest(document),wire_hash,canonical(receipt),now()))
            db.execute('INSERT INTO events(message_id,event,receipt_hash,created_at) VALUES(?,?,?,?)',
                       (message_id,'message.accepted',digest(receipt),now()))
            return receipt

    def command(self, wrapper):
        from .recovery_state import require_active
        require_active(self.installation)
        document,config=self._verify(wrapper)
        fingerprint=digest(document)
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM requests WHERE id=?',(document['request_id'],)).fetchone()
            require(not old or old['request_hash']==fingerprint,'COMMAND_CONFLICT','Participant transport identity has different immutable content.',409)
            if old and old['response_json']:
                return old['status'],strict_json(old['response_json']),bool(document['lose_response'] and old['status']==200)
            require(old or db.execute('SELECT count(*) FROM requests').fetchone()[0]<150000,
                    'PARTICIPANT_CAPACITY','Participant transport journal is full; operator action is required.',503)
            db.execute('INSERT OR IGNORE INTO requests VALUES(?,?,NULL,NULL,?)',(document['request_id'],fingerprint,now()))
        status,error=200,None
        try:
            result=({'worker_id':config['worker_id'],'transport_hash':CONTRACT_HASH,
                     'scope':{k:config[k] for k in SCOPE_FIELDS},'official_conformance':False}
                    if document['action']=='probe' else self._accept(document,config))
        except DomainError as failure:status,error,result=failure.status,failure.code,None
        response={'format':'avenra-participant-response/1','transport_hash':CONTRACT_HASH,
                  'request_id':document['request_id'],'request_hash':digest(document),'scope':document['scope'],
                  'ok':error is None,'result':result,'error_code':error,'responded_at':now()}
        signed={'document':response,'signature':self.signer.sign(canonical(response).encode()).hex()}
        with self.database() as db:
            db.execute('UPDATE requests SET response_json=?,status=? WHERE id=? AND response_json IS NULL',(canonical(signed),status,document['request_id']))
            stored=db.execute('SELECT * FROM requests WHERE id=?',(document['request_id'],)).fetchone()
        # Concurrent identical commands return the first persisted signed reply.
        return stored['status'],strict_json(stored['response_json']),bool(document['lose_response'] and error is None)

    async def __call__(self, scope, receive, send):
        if scope['type']=='lifespan':
            while True:
                event=await receive()
                if event['type']=='lifespan.startup':await send({'type':'lifespan.startup.complete'})
                elif event['type']=='lifespan.shutdown':await send({'type':'lifespan.shutdown.complete'});return
        if scope['type']!='http':return
        headers=scope['headers'];lengths=[v for k,v in headers if k==b'content-length'];types=[v for k,v in headers if k==b'content-type']
        valid=(scope['method']=='POST' and scope['path']=='/v1/commands' and not scope['query_string'] and
               types==[b'application/json'] and len(lengths)==1 and len(lengths[0])<=5 and lengths[0].isdigit() and
               0<int(lengths[0])<=CONTRACT['maximum_bytes'] and not any(k in {b'transfer-encoding',b'content-encoding'} for k,v in headers))
        status,result,drop=400,{'error':'Invalid participant command framing'},False
        if valid and not self.capacity.locked():
            async with self.capacity:
                try:
                    body,deadline=bytearray(),time.monotonic()+5
                    while True:
                        event=await asyncio.wait_for(receive(),max(.001,deadline-time.monotonic()))
                        if event['type']=='http.disconnect':return
                        body.extend(event.get('body',b''))
                        if len(body)>CONTRACT['maximum_bytes']:raise ValueError('body limit')
                        if not event.get('more_body'):break
                    if len(body)!=int(lengths[0]):raise ValueError('framing')
                    status,result,drop=await asyncio.to_thread(self.command,strict_json(body))
                except (ValueError,TypeError,KeyError,DomainError):pass
                except TimeoutError:status=408
        elif valid:status=503
        raw=canonical(result).encode()
        await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json'),(b'content-length',str(len(raw)).encode()),(b'cache-control',b'no-store'),(b'x-content-type-options',b'nosniff')]})
        await send({'type':'http.response.body','body':b'' if drop else raw})


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True);args=parser.parse_args()
    from urllib.parse import urlsplit
    import uvicorn
    with bank_service_lease(Path(args.config).absolute().parent):
        app=ParticipantPeer(args.config);origin=urlsplit(app.configuration()['endpoint'])
        require(origin.scheme=='https' and origin.hostname=='127.0.0.1' and origin.port and not origin.path and not origin.query and not origin.fragment and '@' not in origin.netloc,
                'LOOPBACK_REQUIRED','Synthetic participants bind only to literal loopback HTTPS origins.')
        uvicorn.run(app,host='127.0.0.1',port=origin.port,ssl_certfile=str(app.directory/'server.pem'),ssl_keyfile=str(app.directory/'server-key.pem'),
                    ssl_ca_certs=str(app.directory/'ca.pem'),ssl_cert_reqs=ssl.CERT_REQUIRED,limit_concurrency=16,
                    timeout_keep_alive=3,timeout_graceful_shutdown=12,server_header=False,access_log=False,log_level='warning')


if __name__=='__main__':main()
