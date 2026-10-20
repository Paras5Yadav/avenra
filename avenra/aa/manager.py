"""AA consent and encrypted-data routing inside the existing application layer."""
import hashlib
import hmac
import json
from pathlib import Path
import re
import time
import uuid

from cryptography.hazmat.primitives import serialization

from ..errors import DomainError, require
from ..gateways import EngineError
from ..lab_identity import private_read
from ..private_config import deployment, read_document
from ..util import canonical, digest, now, uid
from .contracts import Contracts, VERSIONS, identifier, stamp, timestamp
from .crypto import document, sign, verify, material
from .semantics import valid_detail, active, data_range, window
from .wire import Client, load_public, message
from .notices import validated_policy

TABLES=['aa_schema','aa_links','aa_consents','aa_provider_consents','aa_sessions','aa_session_legs','aa_jobs','aa_inbox','aa_events']


class Aggregator:
    def __init__(self, app):
        self.app,self.store=app,app.store;self.loaded={}
        with self.store.transaction() as db:
            exists=db.execute("SELECT to_regclass('aa_schema')").fetchone()[0] if hasattr(self.store,'pool') else db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='aa_schema'").fetchone()
            if not exists:
                require(deployment(self.store.directory).get('database',{}).get('mode')!='runtime','MIGRATION_REQUIRED','Install the financial-data schema with the migration identity.',503)
                source=Path(__file__).with_name('schema.sql').read_text()
                if hasattr(self.store,'pool'):source=source.replace(' REAL',' DOUBLE PRECISION')
                for statement in source.split(';'):
                    if statement.strip():db.execute(statement)
                for action in ['UPDATE','DELETE']:
                    if hasattr(self.store,'pool'):db.execute(f'CREATE TRIGGER aa_events_{action.lower()} BEFORE {action} ON aa_events FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record()')
                    else:db.execute(f"CREATE TRIGGER aa_events_{action.lower()} BEFORE {action} ON aa_events BEGIN SELECT RAISE(ABORT,'Financial data events are immutable'); END")
            require(db.execute('SELECT version FROM aa_schema').fetchone()[0]==1,'MIGRATION_REQUIRED','Financial-data schema requires its matching application version.',503)

    def scope(self, tenant, env):
        config=read_document(self.store.directory/'aa-scopes'/env/'profile.json')
        require(config and config.get('format')=='avenra-aa-scope/1' and config.get('classification')=='synthetic' and config.get('tenant_id')==tenant and config.get('environment_id')==env,'AA_UNAVAILABLE','This environment has no configured financial-data peer deployment.',503)
        directory=self.store.directory/'aa-scopes'/env
        fingerprint=digest(config)
        if self.loaded.get(env,{}).get('hash')!=fingerprint:
            key=serialization.load_pem_private_key(private_read(directory/'signing-key.pem'),password=None)
            self.loaded[env]={'hash':fingerprint,'key':key,'contracts':Contracts(directory),'directory':directory,'delivery_policy':validated_policy(config.get('delivery_policy',{}))}
        return config,self.loaded[env]

    def peer(self, tenant, env, identity):
        config,loaded=self.scope(tenant,env);peer=config['peers'].get(identity);require(peer,'InvalidPeer','Financial-data peer is not registered.',404)
        return Client(loaded['directory'],peer,loaded['key'],config['kid'],peer['role'])

    def deliveries(self,tenant,env,identity,notice=None):
        self.app.environment(tenant,env)
        return self.peer(tenant,env,identity).call('POST','/control/notifications',{'notice_id':notice} if notice else {},control=True)

    def retry_notice(self,tenant,env,identity,notice,data,actor):
        require(set(data)=={'request_id'} and identifier(data['request_id']),'InvalidRequest','Use a UUID retry identity.')
        self.app.environment(tenant,env,True)
        result=self.peer(tenant,env,identity).call('POST','/control/notifications/retry',{'notice_id':notice,'request_id':data['request_id'],'actor_hash':digest({'principal':actor})},control=True)
        with self.store.transaction() as db:
            self.store.audit(db,tenant,env,'aa.notification_retry',notice,{'participant':identity,'retry_id':data['request_id'],'generation':result['generation'],'actor':actor})
        return result

    def retry_delivery(self,tenant,env,identity,data,actor):
        require(set(data)=={'request_id'} and identifier(data['request_id']),'InvalidRequest','Use a UUID recovery identity.')
        self.app.environment(tenant,env,True);binding=self.scope(tenant,env)[1]['hash'];hashed=digest({'job_id':identity,'actor':actor})
        response={'job_id':identity,'request_id':data['request_id'],'state':'pending'}
        with self.store.transaction() as db:
            previous=db.execute('SELECT * FROM aa_events WHERE id=?',(data['request_id'],)).fetchone()
            if previous:
                require(previous['tenant_id']==tenant and previous['environment_id']==env and previous['kind']=='delivery_retried' and previous['resource_id']==identity and previous['payload_hash']==hashed,'CONFLICT','Recovery identity has changed scope.',409);return response
            job=self.get(db,'aa_jobs',tenant,env,identity)
            require(job['kind'] in {'notice','consent'} and job['state']=='failed','AA_DELIVERY_NOT_FAILED','Retry exhausted notices/consent deliveries; data sessions use their original session recovery.',409)
            require(job['binding_hash']==binding,'BINDING_CHANGED','Restore the original participant binding before delivery recovery.',409)
            work=db.execute("SELECT * FROM outbox WHERE aggregate_id=? AND tenant_id=? AND environment_id=? AND kind='aa_delivery'",(identity,tenant,env)).fetchone();require(work,'NotFound','Original delivery work is missing.',404)
            current=self.app.runtime.clock(db);require(work['state']!='leased' or (work['lease_until'] is not None and work['lease_until']<current),'DELIVERY_BUSY','The original delivery is still in flight.',409)
            db.execute("UPDATE aa_jobs SET state='pending' WHERE id=?",(identity,))
            db.execute("UPDATE outbox SET state='ready',attempts=0,available_at=?,lease_owner=NULL,lease_token=NULL,lease_until=NULL,last_error=NULL,completed_at=NULL WHERE id=?",(current,work['id']))
            db.execute('INSERT INTO aa_events VALUES(?,?,?,?,?,?,?)',(data['request_id'],tenant,env,'delivery_retried',identity,hashed,now()))
            self.store.audit(db,tenant,env,'aa.delivery_retried',identity,{'actor':actor,'retry_id':data['request_id'],'previous_error':job['error'],'previous_attempts':work['attempts'],'participant':job['target_id']})
        return response

    def delivery_failure(self,tenant,env,job,code,attempts):
        policy=self.scope(tenant,env)[1]['delivery_policy'];exhausted=job['kind'] in {'notice','consent'} and attempts>=policy['maximum_attempts']
        with self.store.transaction() as db:
            db.execute('UPDATE aa_jobs SET state=?,error=? WHERE id=?',('failed' if exhausted else 'unknown',code,job['id']))
            self.store.audit(db,tenant,env,'aa.delivery_unconfirmed',job['id'],{'code':code,'attempt':attempts,'exhausted':exhausted,'participant':job['target_id']})
        delay=min(policy['maximum_seconds'],policy['base_seconds']*2**min(max(attempts-1,0),16))
        return {'pending':not exhausted,'retry_after':delay}

    def event(self, db, tenant, env, kind, identity, payload):
        db.execute('INSERT INTO aa_events VALUES(?,?,?,?,?,?,?)',(str(uuid.uuid4()),tenant,env,kind,identity,digest(payload),now()))
        self.store.audit(db,tenant,env,'aa.'+kind,identity,{'payload_hash':digest(payload)})

    def get(self, db, table, tenant, env, identity, code='NotFound'):
        require(table in TABLES,'InvalidRequest','Invalid financial-data resource.')
        row=db.execute('SELECT * FROM '+table+' WHERE id=? AND tenant_id=? AND environment_id=?',(identity,tenant,env)).fetchone()
        require(row,code,'Financial-data resource not found in this environment.',404 if code=='NotFound' else 400);return dict(row)

    def job(self, db, tenant, env, kind, target, resource, path, body):
        identity=str(uuid.uuid4());binding=self.scope(tenant,env)[1]['hash'];db.execute('INSERT INTO aa_jobs VALUES(?,?,?,?,?,?,?,?,?,NULL,?,?)',(identity,tenant,env,kind,target,resource,path,canonical(body),'pending',binding,now()))
        self.store.enqueue(db,tenant,env,'aa_delivery',identity,{},'aa:'+identity)
        return identity

    def overview(self, tenant, env):
        self.app.environment(tenant,env)
        try:config,loaded=self.scope(tenant,env)
        except DomainError as error:
            if error.code!='AA_UNAVAILABLE':raise
            return {'configured':False,'classification':'synthetic','remaining':['Install synthetic AA/FIP/FIU peers and byte-bound public contracts.'],'official_conformance':False}
        with self.store.reader() as db:
            consents=[dict(r) for r in db.execute('SELECT id,handle,fiu_id,customer,state,request_json,created_at FROM aa_consents WHERE tenant_id=? AND environment_id=? ORDER BY created_at DESC',(tenant,env))]
            links=[dict(r) for r in db.execute('SELECT * FROM aa_links WHERE tenant_id=? AND environment_id=? ORDER BY id',(tenant,env))]
            sessions=[dict(r) for r in db.execute('SELECT id,consent_id,fiu_id,state,expires_at,key_reference,created_at FROM aa_sessions WHERE tenant_id=? AND environment_id=? ORDER BY created_at DESC',(tenant,env))]
            jobs=[dict(r) for r in db.execute('SELECT id,kind,state,error FROM aa_jobs WHERE tenant_id=? AND environment_id=? ORDER BY created_at DESC LIMIT 100',(tenant,env))]
        for c in consents:
            if c['state'] not in {'REVOKED','PENDING'} and timestamp(json.loads(c['request_json'])['ConsentDetail']['consentExpiry'])<=time.time():c['state']='EXPIRED'
            c.pop('request_json')
        for s in sessions:
            if s['expires_at']<=time.time():s['state']='EXPIRED'
        return {'configured':True,'classification':'synthetic','official_conformance':False,'contract_metadata':loaded['contracts'].metadata(),
                'peers':[{'id':k,'role':p['role']} for k,p in config['peers'].items()],'customers':config['customers'],'links':links,'consents':consents,'sessions':sessions,'jobs':jobs,
                'scope':'Synthetic AA/FIP/FIU wire and deposit data exchange. Aggregator stores routing/consent metadata, not FI plaintext or recipient private keys. Advertised FIP metadata inconsistency and newer inter-AA contracts remain unqualified.'}

    def discover(self, tenant, env, data):
        config,_=self.scope(tenant,env);customer=next((c for c in config['customers'] if c['vua']==data.get('customer')),None)
        require(customer,'InvalidRequest','Choose a configured synthetic customer.')
        client=self.peer(tenant,env,data['fip_id']);require(config['peers'][data['fip_id']]['role']=='fip','InvalidPeer','Select an information provider.')
        body=message('fip',Customer={'id':customer['vua'],'Identifiers':[{'category':'STRONG','type':'MOBILE','value':customer['identifier']}]},FITypes=['DEPOSIT'])
        result=client.call('POST','/Accounts/discover',body);self.scope(tenant,env)[1]['contracts'].validate('fip','POST','/Accounts/discover',result,True)
        return result

    def link(self, tenant, env, data):
        config,loaded=self.scope(tenant,env);require(config['peers'].get(data.get('fip_id'),{}).get('role')=='fip','InvalidPeer','Select a configured provider.')
        client=self.peer(tenant,env,data['fip_id']);request=message('fip',Customer={'id':data['customer'],'Accounts':data['accounts']})
        result=client.call('POST','/Accounts/link',request);loaded['contracts'].validate('fip','POST','/Accounts/link',result,True)
        # This control channel stands in for the synthetic customer's provider
        # channel. It is never a real SMS, bank credential or production bypass.
        challenge=client.call('POST','/control/challenge',{'refNumber':result['RefNumber']},control=True)
        linked=client.call('POST','/Accounts/link/verify',message('fip',refNumber=result['RefNumber'],token=challenge['token']))
        loaded['contracts'].validate('fip','POST','/Accounts/link/verify',linked,True)
        with self.store.transaction() as db:
            for account in linked['AccLinkDetails']:
                masked=next(a['maskedAccNumber'] for a in data['accounts'] if a['accRefNumber']==account['accRefNumber'])
                previous=db.execute('SELECT * FROM aa_links WHERE environment_id=? AND fip_id=? AND account_ref=?',(env,data['fip_id'],account['accRefNumber'])).fetchone()
                if previous:db.execute("UPDATE aa_links SET state='LINKED' WHERE id=?",(previous['id'],))
                else:db.execute('INSERT INTO aa_links VALUES(?,?,?,?,?,?,?,?)',(account['linkRefNumber'],tenant,env,data['fip_id'],account['customerAddress'],account['accRefNumber'],masked,'LINKED'))
                self.event(db,tenant,env,'linked',account['linkRefNumber'],account)
        return linked

    def delink(self, tenant, env, identity):
        with self.store.reader() as db:link=self.get(db,'aa_links',tenant,env,identity)
        result=self.peer(tenant,env,link['fip_id']).call('POST','/Accounts/delink',message('fip',Account={'customerAddress':link['customer'],'linkRefNumber':link['id']}))
        with self.store.transaction() as db:db.execute("UPDATE aa_links SET state='DELINKED' WHERE id=? AND tenant_id=? AND environment_id=?",(identity,tenant,env));self.event(db,tenant,env,'delinked',identity,result)
        return result

    def consent_request(self, db, tenant, env, peer_id, data):
        config,_=self.scope(tenant,env);detail=data['ConsentDetail'];valid_detail(detail)
        require(config['peers'][peer_id]['role']=='fiu' and detail['DataConsumer']=={'id':peer_id,'type':'FIU'},'InvalidConsentDetail','Consent recipient differs from authenticated information user.',403)
        require(any(c['vua']==detail['Customer'].get('id') for c in config['customers']),'InvalidRequest','Unknown synthetic customer.')
        identity,handle=str(uuid.uuid4()),str(uuid.uuid4())
        db.execute('INSERT INTO aa_consents VALUES(?,?,?,?,?,?,?,?,?,?,?)',(identity,tenant,env,handle,peer_id,detail['Customer']['id'],canonical(data),'PENDING',None,self.scope(tenant,env)[1]['hash'],now()))
        self.event(db,tenant,env,'consent_requested',identity,data)
        return {'ver':VERSIONS['aa'],'timestamp':stamp(),'txnid':data['txnid'],'Customer':{'id':detail['Customer']['id']},'ConsentHandle':handle}

    def consent_read(self, tenant, env, identity):
        with self.store.reader() as db:row=self.get(db,'aa_consents',tenant,env,identity)
        return {**{k:row[k] for k in ['id','handle','state','customer','fiu_id','created_at']},'request':json.loads(row['request_json']),'artifact':json.loads(row['artifact_json']) if row['artifact_json'] else None}

    def approve(self, tenant, env, identity, data):
        require(set(data)=={'synthetic_customer_approved','link_ids'} and data['synthetic_customer_approved'] is True and isinstance(data['link_ids'],list) and 0<len(data['link_ids'])<=20 and len(set(data['link_ids']))==len(data['link_ids']),'InvalidRequest','Explicitly approve a bounded set of linked synthetic customer accounts.')
        config,loaded=self.scope(tenant,env)
        with self.store.transaction() as db:
            row=self.get(db,'aa_consents',tenant,env,identity);require(row['state']=='PENDING','InvalidConsentId','Consent request is no longer pending.',409)
            require(row['binding_hash']==loaded['hash'],'BINDING_CHANGED','Original financial-data peers must be restored before continuing this consent.',409)
            request=json.loads(row['request_json']);detail=request['ConsentDetail'];require(timestamp(detail['consentExpiry'])>time.time(),'ConsentExpired','Consent request has expired.',403)
            links=[self.get(db,'aa_links',tenant,env,x) for x in data['link_ids']]
            require(all(x['state']=='LINKED' and x['customer']==row['customer'] for x in links),'InvalidLinkRefNumber','All links must belong to this synthetic customer.')
            accounts=[{'fiType':'DEPOSIT','fipId':x['fip_id'],'accType':'SAVINGS','linkRefNumber':x['id'],'maskedAccNumber':x['masked']} for x in links]
            complete={**detail,'DataProvider':{'id':config['id'],'type':'AA'},'Customer':{'id':row['customer']},'Accounts':accounts}
            loaded['contracts'].model('aa',{'$ref':'#/definitions/SignedConsentDetail'},complete)
            signed=sign(canonical(complete).encode(),loaded['key'],config['kid'],False)
            artifact={'ver':VERSIONS['aa'],'txnid':str(uuid.uuid4()),'consentId':identity,'createTimestamp':stamp(),'signedConsent':signed,'status':'ACTIVE','ConsentUse':{'logUri':'urn:avenra:synthetic:consent-use:'+identity,'count':0,'lastUseDateTime':stamp()}}
            db.execute("UPDATE aa_consents SET state='ACTIVE',artifact_json=? WHERE id=?",(canonical(artifact),identity))
            for link,account in zip(links,accounts):
                provider_id=str(uuid.uuid4());provider_detail={**complete,'DataConsumer':{'id':config['id'],'type':'AA'},'DataProvider':{'id':link['fip_id'],'type':'FIP'},'Accounts':[account]}
                loaded['contracts'].model('fip',{'$ref':'#/definitions/SignedConsentDetail'},provider_detail)
                provider_artifact={**artifact,'ver':VERSIONS['fip'],'txnid':str(uuid.uuid4()),'consentId':provider_id,'signedConsent':sign(canonical(provider_detail).encode(),loaded['key'],config['kid'],False)}
                db.execute('INSERT INTO aa_provider_consents VALUES(?,?,?,?,?,?,?,?)',(provider_id,tenant,env,identity,link['id'],link['fip_id'],canonical(provider_artifact),'pending'))
                self.job(db,tenant,env,'consent',link['fip_id'],provider_id,'/Consent',provider_artifact)
            self.job(db,tenant,env,'notice',row['fiu_id'],identity,'/Consent/Notification',message('fiu',Notifier={'id':config['id'],'type':'AA'},ConsentStatusNotification={'consentId':identity,'consentHandle':row['handle'],'consentStatus':'ACTIVE'}))
            self.event(db,tenant,env,'consent_approved',identity,artifact)
        return self.consent_read(tenant,env,identity)

    def change(self, db, tenant, env, row, state):
        require(state in {'ACTIVE','PAUSED','REVOKED','EXPIRED'},'InvalidRequest','Unsupported consent transition.')
        require(row['state'] not in {'REVOKED','EXPIRED'} or row['state']==state,'ConsentRevoked','Terminal consent cannot be reactivated.',403)
        require(row['artifact_json'],'InvalidConsentId','Consent has no signed approval.')
        config,_=self.scope(tenant,env);artifact=json.loads(row['artifact_json']);artifact['status']=state
        db.execute('UPDATE aa_consents SET state=?,artifact_json=? WHERE id=?',(state,canonical(artifact),row['id']))
        for provider in db.execute('SELECT * FROM aa_provider_consents WHERE consent_id=?',(row['id'],)):
            self.job(db,tenant,env,'notice',provider['fip_id'],provider['id'],'/Consent/Notification',message('fip',Notifier={'id':config['id'],'type':'AA'},ConsentStatusNotification={'consentId':provider['id'],'consentStatus':state}))
        self.job(db,tenant,env,'notice',row['fiu_id'],row['id'],'/Consent/Notification',message('fiu',Notifier={'id':config['id'],'type':'AA'},ConsentStatusNotification={'consentId':row['id'],'consentStatus':state}))
        self.event(db,tenant,env,'consent_'+state.lower(),row['id'],artifact)

    def consent_action(self, tenant, env, identity, data):
        require(set(data)=={'state'},'InvalidRequest','Choose a consent state.')
        with self.store.transaction() as db:self.change(db,tenant,env,self.get(db,'aa_consents',tenant,env,identity),data['state'])
        return self.consent_read(tenant,env,identity)

    def request_fi(self, db, tenant, env, peer_id, data):
        row=self.get(db,'aa_consents',tenant,env,data['Consent']['id'],'InvalidConsentId');require(row['fiu_id']==peer_id,'InvalidConsentId','Consent belongs to a different information user.',403)
        require(row['binding_hash']==self.scope(tenant,env)[1]['hash'],'BINDING_CHANGED','Consent peer binding has changed.',409)
        artifact=json.loads(row['artifact_json']) if row['artifact_json'] else {};require(artifact,'InvalidConsentId','Consent has no approved artifact.')
        detail=document(verify(None,artifact['signedConsent'],{self.scope(tenant,env)[0]['kid']:self.scope(tenant,env)[1]['key'].public_key()},False)[0]);active(row['state'],detail,time.time());data_range(detail,data['FIDataRange'],time.time());material(data['KeyMaterial'],time.time())
        require(hmac.compare_digest(data['Consent']['digitalSignature'],artifact['signedConsent'].split('.')[2]),'SignatureDoesNotMatch','Original consent signature differs.')
        links=list(db.execute('SELECT * FROM aa_provider_consents WHERE consent_id=?',(row['id'],)))
        for provider in links:
            linked=self.get(db,'aa_links',tenant,env,provider['link_id']);require(linked['state']=='LINKED','InvalidLinkRefNumber','A consent account has been delinked.',403)
        bucket=window(timestamp(detail['consentStart']),time.time(),detail['Frequency']['unit'])
        count=db.execute("SELECT count(*) FROM aa_sessions WHERE consent_id=? AND state<>'FAILED'"+(' AND frequency_window=?' if detail['fetchType']=='PERIODIC' else ''),(row['id'],bucket) if detail['fetchType']=='PERIODIC' else (row['id'],)).fetchone()[0]
        require(count<(int(detail['Frequency']['value']) if detail['fetchType']=='PERIODIC' else 1),'ConsentFrequencyExceeded','Consent frequency has been consumed.',403)
        identity=str(uuid.uuid4());end=min(time.time()+600,timestamp(detail['consentExpiry']),timestamp(data['KeyMaterial']['DHPublicKey']['expiry']))
        db.execute('INSERT INTO aa_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(identity,tenant,env,row['id'],peer_id,canonical(data),'ACTIVE',bucket,end,None,now()))
        for provider in links:
            leg=str(uuid.uuid4());db.execute('INSERT INTO aa_session_legs VALUES(?,?,?,?,?,?,NULL,?,NULL)',(leg,tenant,env,identity,provider['id'],provider['fip_id'],'pending'))
            request=message('fip',Consent={'id':provider['id'],'digitalSignature':json.loads(provider['artifact_json'])['signedConsent'].split('.')[2]},FIDataRange=data['FIDataRange'],KeyMaterial=data['KeyMaterial'])
            self.job(db,tenant,env,'request',provider['fip_id'],leg,'/FI/request',request)
        self.event(db,tenant,env,'fi_requested',identity,{'consent':row['id'],'range':data['FIDataRange'],'public_material':data['KeyMaterial']})
        return {'ver':VERSIONS['aa'],'timestamp':stamp(),'txnid':data['txnid'],'consentId':row['id'],'sessionId':identity}

    def assemble(self, tenant, env, identity, peer_id, data):
        config,loaded=self.scope(tenant,env)
        with self.store.reader() as db:
            session=self.get(db,'aa_sessions',tenant,env,identity,'InvalidSessionId');consent=self.get(db,'aa_consents',tenant,env,session['consent_id']);artifact=json.loads(consent['artifact_json'])
            detail=document(verify(None,artifact['signedConsent'],{config['kid']:loaded['key'].public_key()},False)[0]);active(consent['state'],detail,time.time())
            require(session['fiu_id']==peer_id,'InvalidSessionId','Data session belongs to another recipient.',403)
            require(session['expires_at']>time.time(),'FIDataExpired','Data session expired.',403)
            legs=[dict(r) for r in db.execute('SELECT * FROM aa_session_legs WHERE session_id=? ORDER BY id',(identity,))]
            for provider in db.execute('SELECT * FROM aa_provider_consents WHERE consent_id=?',(consent['id'],)):require(self.get(db,'aa_links',tenant,env,provider['link_id'])['state']=='LINKED','InvalidLinkRefNumber','A consent account is delinked.',403)
        require(legs and all(x['state']=='ready' for x in legs),'FIDataNotReady','Not every provider account has completed.',503)
        groups=[]
        for leg in legs:
            received=self.peer(tenant,env,leg['fip_id']).call('POST','/FI/fetch',message('fip',sessionId=leg['provider_session']))
            loaded['contracts'].validate('fip','POST','/FI/fetch',received,True)
            groups.extend(received['FI'])
        allowed={a['linkRefNumber']:a for a in detail['Accounts']};observed=[]
        for group in groups:
            material(group['KeyMaterial'],time.time())
            for entry in group['data']:
                require(entry['linkRefNumber'] in allowed and group['fipID']==allowed[entry['linkRefNumber']]['fipId'] and entry['maskedAccNumber']==allowed[entry['linkRefNumber']]['maskedAccNumber'],'InvalidFI','Provider encrypted output exceeds signed account scope.',502)
                observed.append(entry['linkRefNumber'])
        require(set(observed)==set(allowed) and len(observed)==len(allowed),'FIDataNotReady','Encrypted output is incomplete or duplicated.',502)
        selected={x['id'] for x in data.get('linkRefNumber',[])};require(selected<=set(allowed),'InvalidLinkRefNumber','Requested account is outside the consent.')
        if data.get('fipId'):require(data['fipId'] in {a['fipId'] for a in detail['Accounts']},'InvalidFIPId','Provider filter is outside the consent.')
        if selected or data.get('fipId'):groups=[{**g,'data':[x for x in g['data'] if not selected or x['linkRefNumber'] in selected]} for g in groups if not data.get('fipId') or g['fipID']==data['fipId']];groups=[g for g in groups if g['data']]
        response={'ver':VERSIONS['aa'],'timestamp':stamp(),'txnid':data['txnid'],'FI':groups}
        with self.store.transaction() as db:self.event(db,tenant,env,'fi_forwarded',identity,{'ciphertext_hash':digest(response),'accounts':observed})
        return response

    def wire(self, tenant, env, method, path, raw, headers):
        config,loaded=self.scope(tenant,env);secret=headers.get('client_api_key','')
        identity=next((k for k,p in config['peers'].items() if hmac.compare_digest(p['aa_api_key'],secret)),None)
        require(identity,'Unauthorized','Registered financial-data participant required.',401)
        peer=config['peers'][identity]
        verify(path.encode() if method=='GET' else raw,headers.get('x-jws-signature',''),{peer['kid']:load_public(peer['public_key'])})
        template,_,groups=loaded['contracts'].route('aa',method,path)
        data=document(raw) if method=='POST' else None
        if data:
            require(identifier(data.get('txnid')) and abs(timestamp(data.get('timestamp'))-time.time())<=300,'InvalidRequest','Use a UUID identity and fresh message timestamp.')
            require(data.get('ver')==VERSIONS['aa'],'NoSuchVersion','Unsupported AA catalog version.',404);loaded['contracts'].validate('aa',method,path,data)
        if template=='/Heartbeat':return {'ver':VERSIONS['aa'],'timestamp':stamp(),'Status':'UP'}
        if template in {'/FI/fetch','/FI/fetch/{sessionId}'}:
            require(peer['role']=='fiu','Unauthorized','Only a registered information user can fetch data.',403)
            return self.assemble(tenant,env,data['sessionId'] if data else groups[0],identity,data or {'txnid':str(uuid.uuid4())})
        with self.store.transaction() as db:
            cache=data and template not in {'/Consent/fetch','/Consent/handle'}
            if cache:
                previous=db.execute('SELECT * FROM aa_inbox WHERE environment_id=? AND peer_id=? AND txnid=?',(env,identity,data['txnid'])).fetchone()
                if previous:require(previous['body_hash']==digest({k:v for k,v in data.items() if k!='timestamp'}) and previous['path']==path,'IdempotencyError','Transaction identity has changed content.',409);return json.loads(previous['response_json'])
            if template=='/Consent':result=self.consent_request(db,tenant,env,identity,data)
            elif template in {'/Consent/handle','/Consent/handle/{consentHandle}'}:
                handle=data['ConsentHandle'] if data else groups[0];row=db.execute('SELECT * FROM aa_consents WHERE handle=? AND tenant_id=? AND environment_id=? AND fiu_id=?',(handle,tenant,env,identity)).fetchone();require(row,'InvalidConsentHandle','Unknown consent handle.')
                result={'ver':VERSIONS['aa'],'timestamp':stamp(),'txnid':data['txnid'] if data else str(uuid.uuid4()),'ConsentHandle':handle,'ConsentStatus':{'id':row['id'],'status':'PENDING' if row['state']=='PENDING' else 'READY'}}
            elif template in {'/Consent/fetch','/Consent/{id}'}:
                wanted=data['consentId'] if data else groups[0]
                if peer['role']=='fip':
                    provider=self.get(db,'aa_provider_consents',tenant,env,wanted,'InvalidConsentId');require(provider['fip_id']==identity,'InvalidConsentId','Provider grant belongs to another participant.',403)
                    row=self.get(db,'aa_consents',tenant,env,provider['consent_id'],'InvalidConsentId');result=json.loads(provider['artifact_json']);result['status']=row['state'];result['ver']=VERSIONS['aa']
                else:
                    row=self.get(db,'aa_consents',tenant,env,wanted,'InvalidConsentId');require(row['fiu_id']==identity,'InvalidConsentId','Consent belongs to another information user.',403);require(row['artifact_json'],'InvalidConsentId','Consent is not approved.')
                    result=json.loads(row['artifact_json'])
                result['txnid']=data['txnid'] if data else str(uuid.uuid4())
                if result['status']!='REVOKED' and timestamp(json.loads(row['request_json'])['ConsentDetail']['consentExpiry'])<=time.time():result['status']='EXPIRED'
                uses=db.execute("SELECT count(*) count,MAX(created_at) latest FROM aa_sessions WHERE consent_id=? AND state<>'FAILED'",(row['id'],)).fetchone()
                result['ConsentUse']={**result['ConsentUse'],'count':uses['count'],'lastUseDateTime':uses['latest'] or result['ConsentUse']['lastUseDateTime']}
            elif template=='/FI/request':require(peer['role']=='fiu','Unauthorized','Only an information user can request data.',403);result=self.request_fi(db,tenant,env,identity,data)
            elif template=='/Consent/Notification':
                n=data['ConsentStatusNotification']
                if peer['role']=='fip':
                    provider=self.get(db,'aa_provider_consents',tenant,env,n['consentId'],'InvalidConsentId');require(provider['fip_id']==identity,'Unauthorized','Provider notification belongs to another grant.',403)
                    row=self.get(db,'aa_consents',tenant,env,provider['consent_id'],'InvalidConsentId')
                else:row=self.get(db,'aa_consents',tenant,env,n['consentId'],'InvalidConsentId')
                require(data['Notifier']=={'id':identity,'type':peer['role'].upper()} and n['consentStatus']=='REVOKED' and (row['fiu_id']==identity or peer['role']=='fip'),'Unauthorized','Participant notification cannot grant customer consent.',403)
                self.change(db,tenant,env,row,'REVOKED');result={'ver':VERSIONS['aa'],'timestamp':stamp(),'txnid':data['txnid'],'response':'OK'}
            elif template in {'/FI/Notification','/Account/link/Notification'}:
                require(peer['role']=='fip' and data['Notifier']=={'id':identity,'type':'FIP'},'Unauthorized','Notification provider differs.',403)
                # Notifications are observations. Provider fetch and exact account
                # inventory, not arrival order, determine aggregate completion.
                self.event(db,tenant,env,'provider_observation',data['txnid'],data);result={'ver':VERSIONS['aa'],'timestamp':stamp(),'txnid':data['txnid'],'response':'OK'}
            else:raise DomainError('NotFound','AA operation not found.',404)
            loaded['contracts'].validate('aa',method,path,result,True)
            if cache:db.execute('INSERT INTO aa_inbox VALUES(?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),tenant,env,identity,data['txnid'],path,digest({k:v for k,v in data.items() if k!='timestamp'}),canonical(result)))
        return result

    def process(self, tenant, env, identity):
        with self.store.reader() as db:job=self.get(db,'aa_jobs',tenant,env,identity)
        if job['state'] in {'done','failed','cancelled'}:return {'pending':False}
        with self.store.reader() as db:
            work=db.execute("SELECT attempts FROM outbox WHERE aggregate_id=? AND kind='aa_delivery'",(identity,)).fetchone();attempts=max(1,work['attempts'] if work else 1)
        policy=self.scope(tenant,env)[1]['delivery_policy']
        require(job['binding_hash']==self.scope(tenant,env)[1]['hash'],'BINDING_CHANGED','Restore the original financial-data peer binding before recovery.',409)
        if job['kind'] in {'request','fetch'}:
            with self.store.transaction() as db:
                leg=self.get(db,'aa_session_legs',tenant,env,job['resource_id']);session=self.get(db,'aa_sessions',tenant,env,leg['session_id'])
                if session['expires_at']<=time.time():
                    db.execute("UPDATE aa_sessions SET state='EXPIRED' WHERE id=?",(session['id'],));db.execute("UPDATE aa_jobs SET state='done',error='FIDataExpired' WHERE id=?",(identity,));return {'pending':False}
        try:
            if job['kind']=='request':
                with self.store.reader() as db:
                    leg=self.get(db,'aa_session_legs',tenant,env,job['resource_id']);provider=self.get(db,'aa_provider_consents',tenant,env,leg['provider_consent_id'])
                if provider['state']!='active':return {'pending':True}
            body=json.loads(job['body_json'])
            if job['kind']=='fetch':body=message('fip',sessionId=body['sessionId'])
            elif 'timestamp' in body:body['timestamp']=stamp()
            result=self.peer(tenant,env,job['target_id']).call('POST',job['path'],body,timeout=policy['timeout_seconds'])
            role=self.scope(tenant,env)[0]['peers'][job['target_id']]['role'];self.scope(tenant,env)[1]['contracts'].validate(role,'POST',job['path'],result,True)
            with self.store.transaction() as db:
                current=self.get(db,'aa_jobs',tenant,env,identity)
                if current['state']=='done':return {'pending':False}
                db.execute("UPDATE aa_jobs SET state='done',error=NULL WHERE id=?",(identity,))
                if job['kind']=='consent':db.execute("UPDATE aa_provider_consents SET state='active' WHERE id=?",(job['resource_id'],))
                elif job['kind']=='request':
                    db.execute("UPDATE aa_session_legs SET provider_session=?,state='ACTIVE' WHERE id=?",(result['sessionId'],job['resource_id']))
                    self.job(db,tenant,env,'fetch',job['target_id'],job['resource_id'],'/FI/fetch',message('fip',sessionId=result['sessionId']))
                elif job['kind']=='fetch':
                    leg=self.get(db,'aa_session_legs',tenant,env,job['resource_id']);provider=self.get(db,'aa_provider_consents',tenant,env,leg['provider_consent_id']);artifact=json.loads(provider['artifact_json']);detail=document(verify(None,artifact['signedConsent'],{self.scope(tenant,env)[0]['kid']:self.scope(tenant,env)[1]['key'].public_key()},False)[0])
                    expected={a['linkRefNumber'] for a in detail['Accounts']};observed=[x['linkRefNumber'] for f in result['FI'] for x in f['data']]
                    require(set(observed)==expected and len(observed)==len(expected) and all(g['fipID']==job['target_id'] for g in result['FI']),'InvalidFI','Provider response is incomplete or outside its grant.',502)
                    db.execute("UPDATE aa_session_legs SET state='ready',response_hash=? WHERE id=?",(digest(result['FI']),leg['id']))
                    pending=db.execute("SELECT count(*) FROM aa_session_legs WHERE session_id=? AND state<>'ready'",(leg['session_id'],)).fetchone()[0]
                    if not pending:
                        session=self.get(db,'aa_sessions',tenant,env,leg['session_id']);db.execute("UPDATE aa_sessions SET state='COMPLETED' WHERE id=?",(session['id'],))
                        config,_=self.scope(tenant,env)
                        status=[]
                        for provider in db.execute('SELECT * FROM aa_provider_consents WHERE consent_id=?',(session['consent_id'],)):
                            status.append({'fipID':provider['fip_id'],'Accounts':[{'linkRefNumber':provider['link_id'],'FIStatus':'READY','description':'Encrypted provider result observed'}]})
                        self.job(db,tenant,env,'notice',session['fiu_id'],session['id'],'/FI/Notification',message('fiu',Notifier={'id':config['id'],'type':'AA'},FIStatusNotification={'sessionId':session['id'],'sessionStatus':'COMPLETED','FIStatusResponse':status}))
                self.event(db,tenant,env,'delivery_completed',job['resource_id'],{'job':identity,'response_hash':digest(result)})
            return {'pending':False}
        except DomainError as error:
            if error.code=='FIDataNotReady' and error.status==503 and job['kind']=='fetch':return {'pending':True}
            if error.status==429 or error.status>=500 or error.code.startswith('RECOVERY_'):
                return self.delivery_failure(tenant,env,job,error.code,attempts)
            with self.store.transaction() as db:
                db.execute("UPDATE aa_jobs SET state='failed',error=? WHERE id=?",(error.code,identity))
                self.store.audit(db,tenant,env,'aa.delivery_refused',identity,{'code':error.code,'attempt':attempts,'participant':job['target_id']})
                if job['kind'] in {'request','fetch'}:
                    leg=self.get(db,'aa_session_legs',tenant,env,job['resource_id']);db.execute("UPDATE aa_sessions SET state='FAILED' WHERE id=?",(leg['session_id'],));db.execute("UPDATE aa_session_legs SET state='FAILED' WHERE id=?",(leg['id'],))
            raise
        except EngineError:
            return self.delivery_failure(tenant,env,job,'OUTCOME_UNKNOWN',attempts)

    def create_consent(self, tenant, env, data):
        config,loaded=self.scope(tenant,env);peer=data['fiu_id'];require(config['peers'].get(peer,{}).get('role')=='fiu','InvalidPeer','Select a registered information user.')
        # The FIU side signs its real wire request; this synthetic management
        # method delegates via that role rather than using the AA signing key.
        require(identifier(data.get('request_id')),'InvalidRequest','Use a UUID request identity.')
        return self.peer(tenant,env,peer).call('POST','/control/consent',{'detail':data['ConsentDetail'],'request_id':data['request_id']},control=True)

    def fetch_request(self, tenant, env, identity, data):
        config,loaded=self.scope(tenant,env)
        with self.store.reader() as db:row=self.get(db,'aa_consents',tenant,env,identity)
        require(row['artifact_json'],'InvalidConsentId','Approve consent before requesting data.')
        request_id=data['request_id'];require(identifier(request_id),'InvalidRequest','Use a UUID request identity.')
        result=self.peer(tenant,env,row['fiu_id']).call('POST','/control/request',{'consent':json.loads(row['artifact_json']),'request_id':request_id,'range':data['range']},control=True)
        with self.store.transaction() as db:db.execute('UPDATE aa_sessions SET key_reference=? WHERE id=? AND tenant_id=? AND environment_id=?',(request_id,result['sessionId'],tenant,env))
        return result

    def inspect(self, tenant, env, identity, data):
        with self.store.reader() as db:session=self.get(db,'aa_sessions',tenant,env,identity)
        require(session['key_reference'],'InvalidKey','Session has no managed synthetic FIU key reference.')
        request=json.loads(session['request_json']);encrypted=self.assemble(tenant,env,identity,session['fiu_id'],{'txnid':str(uuid.uuid4())})
        return self.peer(tenant,env,session['fiu_id']).call('POST','/control/decrypt',{'reference':session['key_reference'],'range':request['FIDataRange'],'FI':encrypted['FI'],'recipient_jwk':data['recipient_jwk']},control=True)

    def resume(self, tenant, env, identity):
        config,loaded=self.scope(tenant,env)
        with self.store.transaction() as db:
            session=self.get(db,'aa_sessions',tenant,env,identity);require(session['state']=='FAILED','InvalidSessionId','Only a failed aggregate session needs explicit resume.',409)
            consent=self.get(db,'aa_consents',tenant,env,session['consent_id']);require(consent['binding_hash']==loaded['hash'],'BINDING_CHANGED','Original peer binding is required for resume.',409)
            artifact=json.loads(consent['artifact_json']);detail=document(verify(None,artifact['signedConsent'],{config['kid']:loaded['key'].public_key()},False)[0]);active(consent['state'],detail,time.time())
            request=json.loads(session['request_json']);material(request['KeyMaterial'],time.time());require(session['expires_at']>time.time(),'FIDataExpired','Original session key has expired.',403)
            count=db.execute("SELECT count(*) FROM aa_sessions WHERE consent_id=? AND state<>'FAILED'"+(' AND frequency_window=?' if detail['fetchType']=='PERIODIC' else ''),(consent['id'],session['frequency_window']) if detail['fetchType']=='PERIODIC' else (consent['id'],)).fetchone()[0]
            require(count<(int(detail['Frequency']['value']) if detail['fetchType']=='PERIODIC' else 1),'ConsentFrequencyExceeded','Other accepted work has consumed the available frequency.',403)
            for leg in db.execute("SELECT * FROM aa_session_legs WHERE session_id=? AND state='FAILED'",(identity,)):
                provider=self.get(db,'aa_provider_consents',tenant,env,leg['provider_consent_id']);require(self.get(db,'aa_links',tenant,env,provider['link_id'])['state']=='LINKED','InvalidLinkRefNumber','Original provider account is not linked.',403)
                db.execute("UPDATE aa_jobs SET state='cancelled' WHERE resource_id=? AND state NOT IN ('done','failed')",(leg['id'],))
                db.execute("UPDATE aa_session_legs SET state='pending',provider_session=NULL,response_hash=NULL WHERE id=?",(leg['id'],))
                body=message('fip',Consent={'id':provider['id'],'digitalSignature':json.loads(provider['artifact_json'])['signedConsent'].split('.')[2]},FIDataRange=request['FIDataRange'],KeyMaterial=request['KeyMaterial'])
                self.job(db,tenant,env,'request',provider['fip_id'],leg['id'],'/FI/request',body)
            db.execute("UPDATE aa_sessions SET state='ACTIVE' WHERE id=?",(identity,));self.event(db,tenant,env,'fi_resumed',identity,{'key_reference':session['key_reference']})
        return {'sessionId':identity,'state':'ACTIVE'}
