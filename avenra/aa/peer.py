"""Trusted synthetic financial-data counterparties, with isolated role state."""
import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import hmac
import json
import logging
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..deployment_identity import server_context, paths
from ..errors import DomainError, require
from ..gateways import AccountingGateway,EngineError
from ..lab_identity import private_read
from ..private_config import read_document
from ..util import canonical, digest
from .contracts import Contracts, VERSIONS, stamp, timestamp
from .crypto import document, sign, verify, decode64, b64, material, ephemeral, encrypt, decrypt, seal_review
from .semantics import active, data_range, valid_detail, window, expiry
from .ledger_data import statement, NAMESPACE
from .wire import Client, authenticated, load_public, message
from .notices import NoticeQueue


class Peer:
    def __init__(self, path):
        self.path = Path(path);self.directory = self.path.parent;self.config = read_document(path)
        require(self.config.get('format') == 'avenra-aa-peer/1' and self.config.get('classification') == 'synthetic' and self.config.get('role') in {'fip','fiu'}, 'AA_PEER_CONFIGURATION', 'Only configured synthetic financial-data roles can start.', 503)
        self.role = self.config['role'];self.contracts = Contracts(self.directory)
        self.key = serialization.load_pem_private_key(private_read(self.directory/'signing-key.pem'),password=None)
        self.wrap = private_read(self.directory/'state.key');require(len(self.wrap)==32,'AA_PEER_CONFIGURATION','Preserve the role wrapping identity.',503)
        self.state = self.directory/'role.db';self.stopped = threading.Event()
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS replay(txnid TEXT PRIMARY KEY,path TEXT NOT NULL,hash TEXT NOT NULL,response TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS links(id TEXT PRIMARY KEY,customer TEXT NOT NULL,account TEXT NOT NULL,state TEXT NOT NULL,UNIQUE(customer,account));
            CREATE TABLE IF NOT EXISTS challenges(id TEXT PRIMARY KEY,customer TEXT NOT NULL,accounts TEXT NOT NULL,token TEXT NOT NULL,token_hash TEXT NOT NULL,attempts INTEGER NOT NULL,expiry REAL NOT NULL,complete INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS consents(id TEXT PRIMARY KEY,artifact TEXT NOT NULL,detail TEXT NOT NULL,state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,consent_id TEXT NOT NULL,request TEXT NOT NULL,state TEXT NOT NULL,window INTEGER NOT NULL,expiry REAL NOT NULL,response TEXT,error TEXT,lease REAL,token TEXT);
            CREATE TABLE IF NOT EXISTS notices(id TEXT PRIMARY KEY,path TEXT NOT NULL,body TEXT NOT NULL,state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS keys(id TEXT PRIMARY KEY,consent_id TEXT NOT NULL,request_hash TEXT NOT NULL,private TEXT NOT NULL,material TEXT NOT NULL,detail TEXT NOT NULL,expiry REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS received(id TEXT PRIMARY KEY,kind TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS results(id TEXT PRIMARY KEY,hash TEXT NOT NULL,expires REAL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,hash TEXT NOT NULL,path TEXT NOT NULL,body TEXT NOT NULL,response TEXT);
            CREATE TABLE IF NOT EXISTS faults(id TEXT PRIMARY KEY,path TEXT NOT NULL,remaining INTEGER NOT NULL,triggered INTEGER NOT NULL DEFAULT 0);''')
        self.state.chmod(0o600)
        self.notices=NoticeQueue(self)
        self.upstream = Client(self.directory,self.config['aa'],self.key,self.config['kid'],'aa')
        from .routing import resolve, node_binding
        from .runtime import location
        route = resolve(self.directory,self.config['aa'])
        self.recovered = route is not None;self.upstream_ready=False;self.upstream_check_at=0
        accounting = node_binding(location(self.directory)[0],self.path)[0]['accounting_origin'] if route else self.config.get('accounting_origin','http://127.0.0.1:4830')
        self.accounting = AccountingGateway(accounting)

    def work_ready(self):
        from .runtime import work_allowed
        if not work_allowed(self.directory):return False
        if not self.recovered:return True
        current=time.monotonic()
        if current>=self.upstream_check_at:
            try:
                response=self.upstream.call('GET','/Heartbeat',timeout=1)
                self.upstream_ready=response.get('Status')=='UP'
            except (EngineError,DomainError):self.upstream_ready=False
            self.upstream_check_at=time.monotonic()+(.5 if self.upstream_ready else .2)
        return self.upstream_ready

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.state,timeout=10,isolation_level=None);db.row_factory=sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
        try:
            if write: db.execute('BEGIN IMMEDIATE')
            yield db
            if write: db.commit()
        except BaseException:
            if write: db.rollback()
            raise
        finally: db.close()

    def seal(self, value):
        nonce=secrets.token_bytes(12);return b64(nonce+AESGCM(self.wrap).encrypt(nonce,value,self.config['id'].encode()))
    def open(self, value):
        raw=decode64(value);return AESGCM(self.wrap).decrypt(raw[:12],raw[12:],self.config['id'].encode())
    def response(self, data, **fields): return {'ver':VERSIONS[self.role],'timestamp':stamp(),'txnid':data['txnid'],**fields}
    def account(self, reference):
        value=next((a for a in self.config.get('accounts',[]) if a['account_id']==reference),None)
        require(value,'InvalidAccountRefNumber','Account reference does not belong to this provider.');return value
    def linked(self, db, reference, customer):
        row=db.execute("SELECT * FROM links WHERE id=? AND customer=? AND state='LINKED'",(reference,customer)).fetchone()
        require(row,'InvalidLinkRefNumber','Account linkage is not active.',403);return row
    def public_account(self, account):
        return {'FIType':'DEPOSIT','accType':'SAVINGS','accRefNumber':account['account_id'],'maskedAccNumber':account['masked']}

    def notify(self, db, path, body):
        self.notices.enqueue(db,path,body)

    def token_link(self, db, data):
        row=db.execute('SELECT * FROM challenges WHERE id=?',(data['refNumber'],)).fetchone()
        require(row and row['expiry']>time.time(),'InvalidRefNumber','Link challenge expired or is unknown.')
        require(not row['complete'],'InvalidRefNumber','This challenge has already been consumed; start a new link request.')
        require(row['attempts']<3,'InvalidToken','Link challenge is locked.',403)
        expected=hmac.new(self.wrap,(row['id']+'\0'+data['token']).encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,row['token_hash']):
            db.execute('UPDATE challenges SET attempts=attempts+1 WHERE id=?',(row['id'],));db.commit()
            raise DomainError('InvalidToken','Link token was refused.',403)
        accounts=json.loads(row['accounts']);details=[]
        for reference in accounts:
            account=self.account(reference);old=db.execute('SELECT * FROM links WHERE customer=? AND account=?',(row['customer'],reference)).fetchone()
            link=old['id'] if old else str(uuid.uuid4())
            if old:db.execute("UPDATE links SET state='LINKED' WHERE id=?",(link,))
            else:db.execute('INSERT INTO links VALUES(?,?,?,?)',(link,row['customer'],reference,'LINKED'))
            details.append({'customerAddress':row['customer'],'linkRefNumber':link,'accRefNumber':reference,'status':'LINKED'})
            notice=message('aa',Notifier={'type':'FIP','id':self.config['id']},AccountLinkStatusNotification={'accRefNumber':reference,'customerAddress':row['customer'],'linkRefNumber':link,'linkStatus':'LINKED'})
            self.notify(db,'/Account/link/Notification',notice)
        db.execute('UPDATE challenges SET complete=1 WHERE id=?',(row['id'],))
        return self.response(data,AccLinkDetails=details)

    def provider(self, db, path, data, groups, consent_status=None):
        if path=='/Accounts/discover':
            customer=data['Customer'];matched=[]
            for a in self.config['accounts']:
                if a['customer']==customer['id'] and any(i['category']=='STRONG' and i['type']=='MOBILE' and hmac.compare_digest(i['value'],a['identifier']) for i in customer['Identifiers']) and 'DEPOSIT' in data['FITypes']:matched.append(self.public_account(a))
            return self.response(data,DiscoveredAccounts=matched)
        if path=='/Accounts/link':
            customer=data['Customer'];accounts=customer['Accounts'];require(0<len(accounts)<=20 and len({a['accRefNumber'] for a in accounts})==len(accounts),'InvalidRequest','Choose distinct provider accounts.')
            for a in accounts:
                known=self.account(a['accRefNumber']);require(known['customer']==customer['id'] and a==self.public_account(known),'InvalidAccountRefNumber','Account does not belong to this customer.')
            identity,token=str(uuid.uuid4()),str(secrets.randbelow(1_000_000)).zfill(6)
            token_hash=hmac.new(self.wrap,(identity+'\0'+token).encode(),hashlib.sha256).hexdigest()
            db.execute('INSERT INTO challenges VALUES(?,?,?,?,?,0,?,0)',(identity,customer['id'],canonical([a['accRefNumber'] for a in accounts]),self.seal(token.encode()),token_hash,time.time()+self.config.get('challenge_seconds',120)))
            return self.response(data,AuthenticatorType='TOKEN',RefNumber=identity)
        if path=='/Accounts/link/verify':return self.token_link(db,data)
        if path=='/Accounts/link/{RefNumber}/{Token}':return self.token_link(db,{'txnid':str(uuid.uuid4()),'refNumber':groups[0],'token':groups[1]})
        if path=='/Accounts/delink':
            account=data['Account'];row=self.linked(db,account['linkRefNumber'],account['customerAddress'])
            db.execute("UPDATE links SET state='DELINKED' WHERE id=?",(row['id'],))
            return self.response(data,AccLinkDetails={'customerAddress':row['customer'],'linkRefNumber':row['id'],'status':'DELINKED'})
        if path=='/Consent':
            raw,_=verify(None,data['signedConsent'],{self.config['peer_kid']:load_public(self.config['peer_public_key'])},False)
            detail=document(raw);self.contracts.model('fip',{'$ref':'#/definitions/SignedConsentDetail'},detail);valid_detail(detail)
            require(detail['DataConsumer']=={'id':self.config['aa_id'],'type':'AA'} and detail['DataProvider']=={'id':self.config['id'],'type':'FIP'},'InvalidConsentDetail','Consent party binding differs from the registered peers.')
            require(detail['Accounts'] and len(detail['Accounts'])<=20,'InvalidConsentDetail','Consent must name bounded linked accounts.')
            for account in detail['Accounts']:
                linked=self.linked(db,account['linkRefNumber'],detail['Customer']['id']);known=self.account(linked['account'])
                require(account=={'fiType':'DEPOSIT','fipId':self.config['id'],'accType':'SAVINGS','linkRefNumber':linked['id'],'maskedAccNumber':known['masked']},'InvalidConsentDetail','Consent account binding differs from its link.')
            old=db.execute('SELECT * FROM consents WHERE id=?',(data['consentId'],)).fetchone()
            if old:require(json.loads(old['artifact'])['signedConsent']==data['signedConsent'],'InvalidConsentId','Consent identity has changed content.',409)
            else:db.execute('INSERT INTO consents VALUES(?,?,?,?)',(data['consentId'],canonical(data),canonical(detail),consent_status or data['status']))
            return self.response(data,response='OK')
        if path=='/Consent/Notification':
            event=data['ConsentStatusNotification'];require(data['Notifier']=={'id':self.config['aa_id'],'type':'AA'},'Unauthorized','Notification authority differs.',403)
            row=db.execute('SELECT * FROM consents WHERE id=?',(event['consentId'],)).fetchone();require(row,'InvalidConsentId','Unknown consent.')
            desired=consent_status or event['consentStatus']
            require(row['state']!='REVOKED' or desired=='REVOKED','ConsentRevoked','Revoked consent cannot be reactivated.',403)
            db.execute('UPDATE consents SET state=? WHERE id=?',(desired,event['consentId']))
            return self.response(data,response='OK')
        if path=='/FI/request':
            row=db.execute('SELECT * FROM consents WHERE id=?',(data['Consent']['id'],)).fetchone();require(row,'InvalidConsentId','Consent is unknown to this provider.')
            detail=json.loads(row['detail']);active(row['state'],detail,time.time());data_range(detail,data['FIDataRange'],time.time());material(data['KeyMaterial'],time.time())
            signature=json.loads(row['artifact'])['signedConsent'].split('.')[2]
            require(hmac.compare_digest(signature,data['Consent']['digitalSignature']),'SignatureDoesNotMatch','Original consent signature differs.')
            for a in detail['Accounts']:self.linked(db,a['linkRefNumber'],detail['Customer']['id'])
            bucket=window(timestamp(detail['consentStart']),time.time(),detail['Frequency']['unit'])
            count=db.execute("SELECT count(*) FROM sessions WHERE consent_id=? AND state<>'FAILED'"+(' AND window=?' if detail['fetchType']=='PERIODIC' else ''),(row['id'],bucket) if detail['fetchType']=='PERIODIC' else (row['id'],)).fetchone()[0]
            require(count<(int(detail['Frequency']['value']) if detail['fetchType']=='PERIODIC' else 1),'ConsentFrequencyExceeded','The consent request frequency has been consumed.',403)
            identity=str(uuid.uuid4());end=min(time.time()+600,timestamp(detail['consentExpiry']),timestamp(data['KeyMaterial']['DHPublicKey']['expiry']))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,NULL,NULL,NULL,NULL)',(identity,row['id'],canonical(data),'ACTIVE',bucket,end))
            return self.response(data,consentId=row['id'],sessionId=identity)
        if path in {'/FI/fetch','/FI/fetch/{sessionId}'}:
            identity=data['sessionId'] if data else groups[0];row=db.execute('SELECT * FROM sessions WHERE id=?',(identity,)).fetchone();require(row,'InvalidSessionId','Unknown data session.')
            consent=db.execute('SELECT * FROM consents WHERE id=?',(row['consent_id'],)).fetchone();detail=json.loads(consent['detail']);active(consent['state'],detail,time.time())
            for a in detail['Accounts']:self.linked(db,a['linkRefNumber'],detail['Customer']['id'])
            require(row['expiry']>time.time(),'FIDataExpired','Data session has expired.',403)
            require(row['state']=='COMPLETED','FIDataNotReady','Data has not completed successfully.',503 if row['state']=='ACTIVE' else 409)
            response=json.loads(row['response']);request=data or {'txnid':str(uuid.uuid4())}
            if data and (data.get('fipId') and data['fipId']!=self.config['id']):raise DomainError('InvalidFIPId','Provider filter differs.')
            selected={r['id'] for r in data.get('linkRefNumber',[])} if data else set()
            available={d['linkRefNumber'] for f in response['FI'] for d in f['data']}
            require(selected<=available,'InvalidLinkRefNumber','Requested link is outside this session.')
            if selected:response['FI']=[f for f in response['FI'] if f['data'][0]['linkRefNumber'] in selected]
            return self.response(request,FI=response['FI'])
        raise DomainError('NotFound','Provider route not supported.',404)

    def consumer(self, db, path, data):
        require(data['Notifier']=={'id':self.config['aa_id'],'type':'AA'},'Unauthorized','Notification authority differs.',403)
        db.execute('INSERT INTO received VALUES(?,?,?)',(data['txnid'],path,canonical(data)))
        return self.response(data,response='OK')

    def control(self, db, path, data):
        if path=='/control/fault':
            require(set(data)=={'request_id','path'} and data['path'] in {'/FI/request','/FI/process','/Consent','/Consent/Notification','/FI/Notification','/Notification/after_accept'},'InvalidRequest','Choose a bounded synthetic fault.')
            old=db.execute('SELECT * FROM faults WHERE id=?',(data['request_id'],)).fetchone()
            if old:require(old['path']==data['path'],'CONFLICT','Synthetic fault identity has changed content.',409)
            else:db.execute('INSERT INTO faults VALUES(?,?,1,0)',(data['request_id'],data['path']))
            return {'armed':not old or old['remaining']>0,'classification':'synthetic_fault'}
        if self.role=='fip' and path=='/control/challenge':
            row=db.execute('SELECT * FROM challenges WHERE id=?',(data.get('refNumber'),)).fetchone();require(row and row['expiry']>time.time(),'InvalidRefNumber','Synthetic challenge is unavailable.')
            return {'classification':'synthetic_customer_channel','token':self.open(row['token']).decode()}
        if self.role=='fiu' and path=='/control/key':
            artifact=data['consent'];raw,_=verify(None,artifact['signedConsent'],{self.config['peer_kid']:load_public(self.config['peer_public_key'])},False)
            detail=document(raw);self.contracts.model('aa',{'$ref':'#/definitions/SignedConsentDetail'},detail);valid_detail(detail);active(artifact['status'],detail,time.time())
            require(detail['DataConsumer']=={'id':self.config['id'],'type':'FIU'},'InvalidConsentDetail','Consent names a different information user.',403)
            identity=data['request_id'];hashed=digest(data);old=db.execute('SELECT * FROM keys WHERE id=?',(identity,)).fetchone()
            if old:require(old['request_hash']==hashed,'CONFLICT','Key request identity has changed content.',409);return {'KeyMaterial':json.loads(old['material']),'reference':identity}
            end=min(time.time()+600,timestamp(detail['consentExpiry']));private,public=ephemeral(end)
            db.execute('INSERT INTO keys VALUES(?,?,?,?,?,?,?)',(identity,artifact['consentId'],hashed,self.seal(private),canonical(public),canonical(detail),end))
            return {'KeyMaterial':public,'reference':identity}
        if self.role=='fiu' and path=='/control/decrypt':
            row=db.execute('SELECT * FROM keys WHERE id=?',(data['reference'],)).fetchone();require(row and row['expiry']>time.time(),'InvalidKey','Consumer ephemeral key is unavailable.')
            detail=json.loads(row['detail']);active('ACTIVE',detail,time.time());bounds=data['range'];data_range(detail,bounds,time.time())
            result=[];seen=set();allowed={a['linkRefNumber']:a for a in detail['Accounts']}
            for group in data['FI']:
                for item in group['data']:
                    link=item['linkRefNumber'];require(link in allowed and link not in seen and group['fipID']==allowed[link]['fipId'] and item['maskedAccNumber']==allowed[link]['maskedAccNumber'],'InvalidFI','Encrypted data recipient/account binding differs.')
                    xml=decrypt(item['encryptedFI'],self.open(row['private']),json.loads(row['material']),group['KeyMaterial'],time.time());root=self.contracts.document(xml)
                    require(root.get('linkedAccRef')==link and root.get('maskedAccNumber')==allowed[link]['maskedAccNumber'],'InvalidFI','Decrypted account differs from signed consent.')
                    permitted={'PROFILE':'Profile','SUMMARY':'Summary','TRANSACTIONS':'Transactions'}
                    require(all(child.tag.rsplit('}',1)[-1] in {permitted[s] for s in detail['consentTypes']} for child in root),'InvalidFI','Decrypted sections exceed consent.')
                    for transaction in root.findall('.//{'+NAMESPACE+'}Transaction'):
                        require(timestamp(bounds['from'])<=timestamp(transaction.get('transactionTimestamp'))<=timestamp(bounds['to']),'InvalidFI','Decrypted transactions exceed requested dates.')
                    seen.add(link);result.append({'linkRefNumber':link,'sha256':hashlib.sha256(xml).hexdigest(),'xml':xml.decode()})
            require(seen==set(allowed),'FIDataNotReady','Consumer has not received every requested account.',409)
            if detail['consentMode']=='STORE':
                original=db.execute('SELECT * FROM results WHERE id=?',(data['reference'],)).fetchone();hashed=digest({k:v for k,v in data.items() if k!='recipient_jwk'})
                if original:require(original['hash']==hashed,'CONFLICT','Stored data identity has different encrypted input.',409)
                else:db.execute('INSERT INTO results VALUES(?,?,?,?)',(data['reference'],hashed,expiry(detail,time.time()),self.seal(canonical(result).encode())))
            return {'classification':'synthetic_FIU_result','encryptedResult':seal_review({'reference':data['reference'],'documents':result,'retained':detail['consentMode']=='STORE'},data['recipient_jwk']), 'documents':len(result)}
        if path=='/control/notifications':return self.notices.status(db,data)
        if path=='/control/notifications/retry':return self.notices.retry(db,data)
        if path=='/control/status':
            return {'role':self.role,'classification':'synthetic','sessions':[dict(r) for r in db.execute('SELECT id,state,error FROM sessions ORDER BY id')],'notifications':db.execute('SELECT count(*) FROM received').fetchone()[0],'response_loss_faults':db.execute('SELECT COALESCE(SUM(triggered),0) FROM faults').fetchone()[0]}
        raise DomainError('NotFound','Counterparty control route not found.',404)

    def handle(self, method, path, raw, headers):
        control=path.startswith('/control/')
        data=authenticated(self.config,method,path,raw,headers,control=control)
        consent_status=None
        if control and self.role=='fiu' and path in {'/control/consent','/control/request'}:
            from .contracts import identifier
            require(identifier(data.get('request_id')),'InvalidRequest','Use a UUID command identity.')
            hashed=digest(data)
            with self.db(True) as db:
                previous=db.execute('SELECT * FROM commands WHERE id=?',(data['request_id'],)).fetchone()
                if previous:
                    require(previous['hash']==hashed and previous['path']==path,'CONFLICT','Managed command identity has changed content.',409)
                    if previous['response']:return json.loads(previous['response'])
                    command=json.loads(previous['body'])
                elif path=='/control/consent':
                    command=message('aa',ConsentDetail=data['detail']);command['txnid']=data['request_id']
                    db.execute('INSERT INTO commands VALUES(?,?,?,?,NULL)',(data['request_id'],hashed,path,canonical(command)))
                else:
                    public=self.control(db,'/control/key',{'consent':data['consent'],'request_id':data['request_id']})
                    command=message('aa',Consent={'id':data['consent']['consentId'],'digitalSignature':data['consent']['signedConsent'].split('.')[2]},FIDataRange=data['range'],KeyMaterial=public['KeyMaterial']);command['txnid']=data['request_id']
                    db.execute('INSERT INTO commands VALUES(?,?,?,?,NULL)',(data['request_id'],hashed,path,canonical(command)))
            command['timestamp']=stamp()
            result=self.upstream.call('POST','/Consent' if path=='/control/consent' else '/FI/request',command)
            with self.db(True) as db:db.execute('UPDATE commands SET response=? WHERE id=?',(canonical(result),data['request_id']))
            return result
        if not control:
            template,_,groups=self.contracts.route(self.role,method,path)
            if data:
                require(data.get('ver')==VERSIONS[self.role],'NoSuchVersion','Unsupported selected API version.',404)
                self.contracts.validate(self.role,method,path,data)
            if template=='/Heartbeat':return {'ver':VERSIONS[self.role],'timestamp':stamp(),'Status':'UP'}
            consent_id=None
            if self.role=='fip':
                if template=='/Consent/Notification':consent_id=data['ConsentStatusNotification']['consentId']
                elif template=='/Consent':consent_id=data['consentId']
                elif template=='/FI/request':consent_id=data['Consent']['id']
                elif template in {'/FI/fetch','/FI/fetch/{sessionId}'}:
                    with self.db() as db:original=db.execute('SELECT consent_id FROM sessions WHERE id=?',(data['sessionId'] if data else groups[0],)).fetchone()
                    require(original,'InvalidSessionId','Unknown provider data session.');consent_id=original['consent_id']
            if consent_id:
                latest=self.upstream.call('POST','/Consent/fetch',message('aa',consentId=consent_id));consent_status=latest['status']
        with self.db(True) as db:
            if not control and consent_status is not None:
                existing=db.execute('SELECT * FROM consents WHERE id=?',(consent_id,)).fetchone()
                if existing:
                    require(json.loads(existing['artifact'])['signedConsent']==latest['signedConsent'],'InvalidConsentId','Original authoritative consent content has changed.',409)
                    require(existing['state']!='REVOKED' or consent_status=='REVOKED','ConsentRevoked','Revoked provider consent cannot be reactivated.',403)
                    db.execute('UPDATE consents SET state=? WHERE id=?',(consent_status,consent_id))
            cache=data and not control and template not in {'/FI/fetch','/FI/fetch/{sessionId}'}
            if cache:
                old=db.execute('SELECT * FROM replay WHERE txnid=?',(data['txnid'],)).fetchone()
                if old:
                    require(old['path']==path and old['hash']==digest({k:v for k,v in data.items() if k!='timestamp'}),'IdempotencyError','Transaction identity has different wire content.',409)
                    return json.loads(old['response'])
            result=self.control(db,path,data) if control else self.provider(db,template,data,groups,consent_status) if self.role=='fip' else self.consumer(db,template,data)
            if not control:
                self.contracts.validate(self.role,method,path,result,True)
                if cache:db.execute('INSERT INTO replay VALUES(?,?,?,?)',(data['txnid'],path,digest({k:v for k,v in data.items() if k!='timestamp'}),canonical(result)))
            return result

    def process(self):
        if not self.work_ready():return
        if self.role=='fip':
            with self.db(True) as db:
                row=db.execute("SELECT * FROM sessions WHERE state='ACTIVE' AND (lease IS NULL OR lease<?) ORDER BY id LIMIT 1",(time.time(),)).fetchone()
                if row:
                    token=secrets.token_hex(16);db.execute('UPDATE sessions SET lease=?,token=? WHERE id=?',(time.time()+30,token,row['id']))
            if row:
                try:
                    with self.db() as db:consent=db.execute('SELECT * FROM consents WHERE id=?',(row['consent_id'],)).fetchone();detail=json.loads(consent['detail']);request=json.loads(row['request']);active(consent['state'],detail,time.time())
                    with self.db(True) as db:
                        fault=db.execute("SELECT * FROM faults WHERE path='/FI/process' AND remaining>0 ORDER BY id LIMIT 1").fetchone()
                        if fault:db.execute('UPDATE faults SET remaining=remaining-1,triggered=triggered+1 WHERE id=?',(fault['id'],))
                    if fault:raise DomainError('SYNTHETIC_SOURCE_FAILURE','Declared synthetic provider data failure.')
                    groups=[]
                    for a in detail['Accounts']:
                        with self.db() as db:linked=self.linked(db,a['linkRefNumber'],detail['Customer']['id'])
                        source={**self.account(linked['account']),'link_ref':linked['id']}
                        xml=statement(self.accounting,source,detail,request['FIDataRange'],self.contracts)
                        encrypted,public=encrypt(xml,request['KeyMaterial'],row['expiry'],time.time())
                        groups.append({'fipID':self.config['id'],'data':[{'linkRefNumber':linked['id'],'maskedAccNumber':source['masked'],'encryptedFI':encrypted}],'KeyMaterial':public})
                    response={'FI':groups};state,error='COMPLETED',None
                except Exception as failure:
                    state,error,response='FAILED',getattr(failure,'code',type(failure).__name__),None
                with self.db(True) as db:
                    current=db.execute('SELECT token FROM sessions WHERE id=?',(row['id'],)).fetchone()
                    if current['token']==token:
                        db.execute('UPDATE sessions SET state=?,response=?,error=?,lease=NULL WHERE id=?',(state,canonical(response) if response else None,error,row['id']))
                        statuses=[{'linkRefNumber':a['linkRefNumber'],'FIStatus':'READY' if state=='COMPLETED' else 'TIMEOUT','description':'Synthetic provider completed' if state=='COMPLETED' else 'Synthetic provider failed'} for a in detail['Accounts']]
                        self.notify(db,'/FI/Notification',message('aa',Notifier={'id':self.config['id'],'type':'FIP'},FIStatusNotification={'sessionId':row['id'],'sessionStatus':state,'FIStatusResponse':[{'fipID':self.config['id'],'Accounts':statuses}]}))
        with self.db(True) as db:
            db.execute('DELETE FROM results WHERE expires IS NOT NULL AND expires<?',(time.time(),))
            db.execute("UPDATE keys SET private='' WHERE expiry<? AND private<>''",(time.time(),))
            db.execute("UPDATE sessions SET response=NULL,state='EXPIRED' WHERE expiry<? AND response IS NOT NULL",(time.time(),))

    async def __call__(self, scope, receive, send):
        if scope['type']=='lifespan':
            while True:
                event=await receive()
                if event['type']=='lifespan.startup':
                    def work(task):
                        while not self.stopped.wait(.2):
                            try:
                                if self.work_ready():task()
                            except Exception:logging.exception('Synthetic financial-data role task failed')
                    self.threads=[threading.Thread(target=work,args=(task,),daemon=True) for task in [self.process,self.notices.deliver]]
                    for thread in self.threads:thread.start()
                    await send({'type':'lifespan.startup.complete'})
                else:
                    self.stopped.set()
                    for thread in self.threads:await asyncio.to_thread(thread.join,16)
                    await send({'type':'lifespan.shutdown.complete'});return
        elif scope['type']!='http':return
        try:
            require(scope['scheme']=='https','TLS_REQUIRED','Financial-data peers require mutual TLS.',403)
            headers={}
            for key,value in scope['headers']:
                name=key.decode();require(name not in headers,'InvalidRequest','Repeated headers are refused.');headers[name]=value.decode('latin-1')
            limit=4_000_000 if scope['path']=='/control/decrypt' else 65536
            length=headers.get('content-length','0');require(length.isdigit() and int(length)<=limit and 'transfer-encoding' not in headers,'InvalidRequest','Invalid body framing.',413)
            body=bytearray();end=time.monotonic()+5
            while True:
                try:event=await asyncio.wait_for(receive(),max(.001,end-time.monotonic()))
                except TimeoutError:raise DomainError('InvalidRequest','Request body arrival timed out.',408)
                if event['type']=='http.disconnect':return
                body.extend(event.get('body',b''));require(len(body)<=limit,'InvalidRequest','Body budget exceeded.',413)
                if not event.get('more_body'):break
            require(len(body)==int(length),'InvalidRequest','Body framing differs.')
            ready=scope['path']=='/control/status' or await asyncio.to_thread(self.work_ready)
            require(ready,'RECOVERY_INACTIVE','This AA participant is paused until activation and its authenticated aggregator is serving.',503)
            value=await asyncio.to_thread(self.handle,scope['method'],scope['path'],bytes(body),headers);status=200
        except EngineError:
            status=503;value={'ver':VERSIONS[self.role],'timestamp':stamp(),'txnid':str(uuid.uuid4()),'errorCode':'PeerOutcomeUnconfirmed','errorMsg':'Authoritative consent is temporarily unavailable; the original identity is retained.'}
        except DomainError as error:
            status=error.status;value={'ver':VERSIONS[self.role],'timestamp':stamp(),'txnid':str(uuid.uuid4()),'errorCode':error.code,'errorMsg':error.message}
        except Exception:
            logging.exception('Synthetic financial-data peer dispatch failed')
            status=500;value={'ver':VERSIONS[self.role],'timestamp':stamp(),'txnid':str(uuid.uuid4()),'errorCode':'InternalError','errorMsg':'Synthetic role request could not complete.'}
        raw=canonical(value).encode();signature=sign(raw,self.key,self.config['kid']);drop=False
        if status==200 and scope['method']=='POST' and not scope['path'].startswith('/control/'):
            with self.db(True) as db:
                fault=db.execute('SELECT * FROM faults WHERE path=? AND remaining>0 ORDER BY id LIMIT 1',(scope['path'],)).fetchone()
                if fault:db.execute('UPDATE faults SET remaining=remaining-1,triggered=triggered+1 WHERE id=?',(fault['id'],));drop=True
        await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json'),(b'content-length',str(len(raw)).encode()),(b'cache-control',b'no-store'),(b'x-jws-signature',signature.encode())]});await send({'type':'http.response.body','body':raw[:-16] if drop else raw})


def main():
    parser=argparse.ArgumentParser(description='Avenra trusted synthetic financial-data counterparty');parser.add_argument('--config',required=True);parser.add_argument('--recovery-data');args=parser.parse_args()
    from .runtime import service_lease,launch_binding
    with service_lease(args.config):
        binding=launch_binding(args.config,args.recovery_data)
        app=Peer(args.config);identity=paths(app.directory,app.config['identity_reference']);context=server_context(app.directory,app.config['identity_reference'],True)
        port=app.config['port']
        if binding:
            from .routing import register_process,origin
            _,node=register_process(args.recovery_data,args.config);port=origin(node['origin'])
        import uvicorn
        uvicorn.run(app,host='127.0.0.1',port=port,http='h11',ws='none',lifespan='on',proxy_headers=False,access_log=False,server_header=False,limit_concurrency=16,timeout_keep_alive=3,
                    ssl_certfile=str(identity['cert']),ssl_keyfile=str(identity['key']),ssl_context_factory=lambda *_:context)


if __name__=='__main__':main()
