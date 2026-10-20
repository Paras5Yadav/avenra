"""Adversarial and recovery cases for actual privately deployed data roles."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request

from avenra.aa.contracts import stamp
from avenra.aa.crypto import b64,decode64,derive,document,ephemeral,material,sign,signing_key,verify
from avenra.aa.fixture import start,stop
from avenra.aa.peer import Peer
from avenra.aa.recovery import snapshot,restore
from avenra.aa.wire import Client,message
from avenra.errors import DomainError
from avenra.private_config import write_document
from avenra.util import canonical


def run_cases(ctx):
    app,api,scope,check,denied,wait=[ctx[k] for k in ['app','api','scope','check','denied','wait']]
    tenant,env,config,loaded,consent,detail,links,session,jwk=[ctx[k] for k in ['tenant','env','config','loaded','consent','detail','links','session','jwk']]
    root,directory,deployment,processes=[ctx[k] for k in ['ROOT','directory','deployment','processes']]
    fiu=consent['fiu_id'];roles={x['id']:Peer(Path(x['directory'])/'peer.json') for x in deployment['roles']}
    recipient=roles[fiu];other=next(p for i,p in roles.items() if p.role=='fiu' and i!=fiu)
    providers=[p for p in roles.values() if p.role=='fip'];provider=providers[0]
    signed=ctx['signed'];bounds={'from':detail['FIDataRange']['from'],'to':stamp()}
    _,public=ephemeral(time.time()+180)
    request=message('aa',Consent={'id':consent['id'],'digitalSignature':signed.split('.')[2]},FIDataRange=bounds,KeyMaterial=public)

    def wire_raw(peer,raw,signature=None,secret=None):
        client=peer.upstream
        req=Request(client.origin+client.config['prefix']+'/FI/request',data=raw,headers={'Content-Type':'application/json','client_api_key':secret or client.config['api_key'],'x-jws-signature':signature or sign(raw,peer.key,peer.config['kid'])})
        try:response=client.opener.open(req,timeout=10)
        except HTTPError as error:response=error
        with response:
            body=response.read();verify(body,response.headers['x-jws-signature'],client.keys);value=document(body)
            if response.status>=400:raise DomainError(value['errorCode'],value['errorMsg'],response.status)
            return value

    raw=canonical(request).encode()
    vector={'cryptoAlg':'ECDH','curve':'X25519','DHPublicKey':{'expiry':stamp(time.time()+120),'KeyValue':b64(bytes.fromhex('de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f'))},'Nonce':'WY9hFnr4WLFd9mVvItMIdBjFygVDpPpoi/BZ3Z3lBfY='}
    derived,_=derive(bytes.fromhex('77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a'),'2OZz6xYAnS6a83WcOZFLQH/0YVcl1vWE+zespfGAWFo=',vector,time.time())
    check('The published X25519/HKDF reference vector yields the independent expected key',derived.hex()=='3b434ed3f93ce5ef1115a89955a50c0fa3ef09f449a842fed3bf81c2939f4261')
    denied('A correct message signature cannot replace the registered participant API secret',lambda:wire_raw(recipient,raw,secret='invalid-synthetic-key'),'Unauthorized')
    denied('A correct API secret cannot replace the registered RSA signing identity',lambda:wire_raw(recipient,raw,sign(raw,signing_key(),recipient.config['kid'])),'SignatureDoesNotMatch')
    denied('Signed duplicated JSON fields are refused before session creation',lambda:wire_raw(recipient,b'{"txnid":"a","txnid":"b"}'),'InvalidRequest')
    denied('Signed nonfinite JSON values are refused',lambda:wire_raw(recipient,b'{"txnid":NaN}'),'InvalidRequest')
    denied('Stale signed requests cannot create a fresh session',lambda:recipient.upstream.call('POST','/FI/request',{**request,'timestamp':stamp(time.time()-900)}),'InvalidRequest')
    denied('Unselected wire versions are refused explicitly',lambda:recipient.upstream.call('POST','/FI/request',{**request,'ver':'99.0.0'}),'NoSuchVersion')
    denied('Unknown request fields cannot silently alter the selected contract',lambda:recipient.upstream.call('POST','/FI/request',{**request,'extra':'unsafe'}),'InvalidRequest')
    denied('An information user cannot fetch another recipient consent',lambda:other.upstream.call('POST','/Consent/fetch',message('aa',consentId=consent['id'])),'InvalidConsentId')
    denied('An information user cannot fetch another recipient session',lambda:other.upstream.call('POST','/FI/fetch',message('aa',sessionId=session['sessionId'])),'InvalidSessionId')
    denied('An information provider cannot impersonate an information user',lambda:provider.upstream.call('POST','/FI/request',request),'Unauthorized')
    denied('A substituted original consent signature is refused',lambda:recipient.upstream.call('POST','/FI/request',{**request,'Consent':{**request['Consent'],'digitalSignature':'altered'}}),'SignatureDoesNotMatch')
    denied('A range outside signed consent is refused',lambda:recipient.upstream.call('POST','/FI/request',{**request,'FIDataRange':{'from':stamp(time.time()-86400*3),'to':stamp()}}),'InvalidDateRange')
    bad=deepcopy(public);bad['Nonce']=b64(bytes(31))
    denied('A short ECDH nonce is refused before work is accepted',lambda:recipient.upstream.call('POST','/FI/request',{**request,'KeyMaterial':bad}),'InvalidKey')
    bad=deepcopy(public);bad['DHPublicKey']['KeyValue']=b64(bytes(32))
    denied('Low-order X25519 public keys are refused before work is accepted',lambda:recipient.upstream.call('POST','/FI/request',{**request,'KeyMaterial':bad}),'InvalidKey')
    bad=deepcopy(public);bad['DHPublicKey']['expiry']=stamp(time.time()-1)
    denied('Expired recipient keys cannot start a data session',lambda:recipient.upstream.call('POST','/FI/request',{**request,'KeyMaterial':bad}),'InvalidKey')
    denied('A participant cannot grant synthetic customer approval through a notification',lambda:recipient.upstream.call('POST','/Consent/Notification',message('aa',Notifier={'id':fiu,'type':'FIU'},ConsentStatusNotification={'consentId':consent['id'],'consentStatus':'ACTIVE'})),'Unauthorized')
    with app.store.reader() as db:
        grants=[dict(r) for r in db.execute('SELECT * FROM aa_provider_consents WHERE consent_id=?',(consent['id'],))]
        own=next(g for g in grants if g['fip_id']==provider.config['id']);foreign=next(g for g in grants if g['fip_id']!=provider.config['id'])
    denied('A provider cannot revoke a different provider grant',lambda:provider.upstream.call('POST','/Consent/Notification',message('aa',Notifier={'id':provider.config['id'],'type':'FIP'},ConsentStatusNotification={'consentId':foreign['id'],'consentStatus':'REVOKED'})),'Unauthorized')
    denied('A provider cannot read a different provider grant',lambda:provider.upstream.call('POST','/Consent/fetch',message('aa',consentId=foreign['id'])),'InvalidConsentId')

    # Every corrupted payload is sent to the actual FIU TLS process; the AA
    # only forwards ciphertext and the independently generated browser key.
    original=app.aa.assemble(tenant,env,session['sessionId'],fiu,{'txnid':str(uuid.uuid4())})
    with app.store.reader() as db:row=dict(db.execute('SELECT * FROM aa_sessions WHERE id=?',(session['sessionId'],)).fetchone())
    decrypt_request={'reference':row['key_reference'],'range':json.loads(row['request_json'])['FIDataRange'],'FI':original['FI'],'recipient_jwk':jwk}
    bad=deepcopy(decrypt_request);encoded=bytearray(decode64(bad['FI'][0]['data'][0]['encryptedFI']));encoded[-1]^=1;bad['FI'][0]['data'][0]['encryptedFI']=b64(encoded)
    denied('The FIU refuses a modified GCM authentication tag',lambda:app.aa.peer(tenant,env,fiu).call('POST','/control/decrypt',bad,control=True),'InvalidFI')
    bad=deepcopy(decrypt_request);bad['FI'][0]['data'][0]['maskedAccNumber']='wrong'
    denied('The FIU refuses ciphertext with a substituted account binding',lambda:app.aa.peer(tenant,env,fiu).call('POST','/control/decrypt',bad,control=True),'InvalidFI')
    bad=deepcopy(decrypt_request);bad['FI'].pop()
    denied('The FIU refuses an incomplete account inventory',lambda:app.aa.peer(tenant,env,fiu).call('POST','/control/decrypt',bad,control=True),'FIDataNotReady')
    denied('A different FIU has no private key for this session',lambda:app.aa.peer(tenant,env,other.config['id']).call('POST','/control/decrypt',decrypt_request,control=True),'InvalidKey')
    denied('Deposit XML with external entities is refused',lambda:loaded['contracts'].document(b'<!DOCTYPE Account [<!ENTITY x SYSTEM "file:///etc/passwd">]><Account>&x;</Account>'),'InvalidFI')
    wrong=ctx['oracle']({'mode':'review','jwe':ctx['inspection']['encryptedResult'],'private_key':ctx['pem'](signing_key()).decode()},must_succeed=False)
    check('A different browser private key cannot open the review envelope',wrong is None)

    # A real provider closes its response after committing the original FI
    # request. Its replay journal must recover the one accepted provider ID.
    client=app.aa.peer(tenant,env,provider.config['id'])
    client.call('POST','/control/fault',{'request_id':str(uuid.uuid4()),'path':'/FI/request'},control=True)
    lost_id=str(uuid.uuid4());lost=api(scope+'/consents/'+consent['id']+'/request',{'request_id':lost_id,'range':bounds})
    wait(lambda:api(scope),lambda x:next(s for s in x['sessions'] if s['id']==lost['sessionId'])['state']=='COMPLETED',timeout=30)
    status=client.call('POST','/control/status',{},control=True)
    check('A committed request with a genuinely truncated TLS response recovers to completion',status['response_loss_faults']==1)
    with app.store.reader() as db:
        lost_row=dict(db.execute('SELECT * FROM aa_sessions WHERE id=?',(lost['sessionId'],)).fetchone())
        jobs=[dict(r) for r in db.execute("SELECT j.* FROM aa_jobs j JOIN aa_session_legs l ON j.resource_id=l.id WHERE l.session_id=? AND j.kind='request'",(lost['sessionId'],))]
    with provider.db() as db:
        durable={json.loads(r['request'])['txnid']:r['id'] for r in db.execute('SELECT * FROM sessions')}
        replay=[json.loads(r['response']) for r in db.execute("SELECT response FROM replay WHERE path='/FI/request'")]
    affected=[j for j in jobs if j['target_id']==provider.config['id']]
    check('Response recovery retains one provider session for each original accepted wire identity',all(json.loads(j['body_json'])['txnid'] in durable for j in affected) and len({r['sessionId'] for r in replay})==len(replay))
    with app.store.reader() as db:attempts=[db.execute("SELECT attempts FROM outbox WHERE aggregate_id=? AND kind='aa_delivery'",(j['id'],)).fetchone()[0] for j in affected]
    check('The dropped acknowledgement causes a durable retry of an original request job',any(n>=2 for n in attempts))
    repeated=api(scope+'/consents/'+consent['id']+'/request',{'request_id':lost_id,'range':bounds})
    check('Managed retry after a lost response preserves the original aggregate session',repeated['sessionId']==lost['sessionId'] and lost_row['key_reference']==lost_id)

    # Stop and restart the very same role process, retaining private key and
    # SQLite journal. The source and native ledger are never replaced.
    spec=next(s for s in deployment['processes'] if s['name']==provider.config['id']);index=next(i for i,p in enumerate(processes) if str(Path(spec['directory'])/'peer.json') in p.args)
    stop(processes[index]);processes[index]=start(spec,root)
    wait(lambda:client.call('POST','/control/status',{},control=True))
    check('A restarted provider serves the original encrypted session without creating a new financial request',api(scope+'/sessions/'+lost['sessionId']+'/inspect',{'recipient_jwk':jwk})['documents']==3)

    # Retain failed source attempts, reissue only failed legs, and preserve
    # the AA session and FIU key rather than starting an unrelated operation.
    client.call('POST','/control/fault',{'request_id':str(uuid.uuid4()),'path':'/FI/process'},control=True)
    failed=api(scope+'/consents/'+consent['id']+'/request',{'request_id':str(uuid.uuid4()),'range':bounds})
    wait(lambda:api(scope),lambda x:next(s for s in x['sessions'] if s['id']==failed['sessionId'])['state']=='FAILED',timeout=30)
    def leg_rows():
        with app.store.reader() as db:return [dict(r) for r in db.execute('SELECT * FROM aa_session_legs WHERE session_id=?',(failed['sessionId'],))]
    wait(leg_rows,lambda rows:all(r['state'] in {'ready','FAILED'} for r in rows))
    before=leg_rows();ready={r['id']:r['provider_session'] for r in before if r['state']=='ready'}
    with app.store.reader() as db:key_before=db.execute('SELECT key_reference FROM aa_sessions WHERE id=?',(failed['sessionId'],)).fetchone()[0]
    resumed=api(scope+'/sessions/'+failed['sessionId']+'/resume',{})
    wait(lambda:api(scope),lambda x:next(s for s in x['sessions'] if s['id']==failed['sessionId'])['state']=='COMPLETED',timeout=30)
    after=leg_rows()
    with app.store.reader() as db:key_after=db.execute('SELECT key_reference FROM aa_sessions WHERE id=?',(failed['sessionId'],)).fetchone()[0]
    check('Explicit recovery preserves the failed aggregate session and recipient key',resumed['sessionId']==failed['sessionId'] and key_before==key_after)
    check('Explicit recovery preserves already completed sibling provider sessions',bool(ready) and all(r['provider_session']==ready[r['id']] for r in after if r['id'] in ready))
    with provider.db() as db:errors=[r['error'] for r in db.execute("SELECT error FROM sessions WHERE state='FAILED'")]
    check('The original failed provider attempt remains retained as evidence','SYNTHETIC_SOURCE_FAILURE' in errors)

    # Simultaneous admission is exercised against the real AA HTTP service
    # and its actual SQLite/PostgreSQL transaction boundary, not a mock.
    def new_consent(changes,selected=None):
        value={**deepcopy(detail),'consentExpiry':stamp(time.time()+240),**changes}
        result=api(scope+'/consents',{'fiu_id':fiu,'ConsentDetail':value,'request_id':str(uuid.uuid4())})
        row=next(c for c in api(scope)['consents'] if c['handle']==result['ConsentHandle'])
        api(scope+'/consents/'+row['id']+'/approve',{'synthetic_customer_approved':True,'link_ids':selected or [x['id'] for x in links]})
        return row,value
    once,_=new_consent({'fetchType':'ONETIME','Frequency':{'unit':'DAY','value':1}})
    def admission(_):
        try:return api(scope+'/consents/'+once['id']+'/request',{'request_id':str(uuid.uuid4()),'range':bounds})
        except DomainError as error:return error.code
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(admission,range(4)))
    check('Concurrent one-time requests admit exactly one session',sum(isinstance(r,dict) for r in results)==1 and results.count('ConsentFrequencyExceeded')==3)
    once_session=next(r for r in results if isinstance(r,dict))
    wait(lambda:api(scope),lambda x:next(s for s in x['sessions'] if s['id']==once_session['sessionId'])['state']=='COMPLETED')

    # An intentionally empty transaction filter is a legitimate result; no
    # fictitious transaction is inserted to satisfy the public XML schema.
    filtered,_=new_consent({'consentTypes':['SUMMARY','TRANSACTIONS'],'DataFilter':[{'type':'TRANSACTIONAMOUNT','operator':'>','value':'100000000.00'}]})
    empty=api(scope+'/consents/'+filtered['id']+'/request',{'request_id':str(uuid.uuid4()),'range':bounds})
    wait(lambda:api(scope),lambda x:next(s for s in x['sessions'] if s['id']==empty['sessionId'])['state']=='COMPLETED')
    decoded=ctx['oracle']({'mode':'review','jwe':api(scope+'/sessions/'+empty['sessionId']+'/inspect',{'recipient_jwk':jwk})['encryptedResult'],'private_key':ctx['pem'](ctx['review_key']).decode()})
    check('An empty filtered statement validates without invented transaction rows',len(decoded['documents'])==3 and all('<Transactions' not in d['xml'] for d in decoded['documents']))

    stored,_=new_consent({'consentMode':'STORE'},[links[0]['id']])
    stored_session=api(scope+'/consents/'+stored['id']+'/request',{'request_id':str(uuid.uuid4()),'range':bounds})
    wait(lambda:api(scope),lambda x:next(s for s in x['sessions'] if s['id']==stored_session['sessionId'])['state']=='COMPLETED')
    envelope=api(scope+'/sessions/'+stored_session['sessionId']+'/inspect',{'recipient_jwk':jwk})
    decoded=ctx['oracle']({'mode':'review','jwe':envelope['encryptedResult'],'private_key':ctx['pem'](ctx['review_key']).decode()})
    with app.store.reader() as db:stored_ref=db.execute('SELECT key_reference FROM aa_sessions WHERE id=?',(stored_session['sessionId'],)).fetchone()[0]
    with recipient.db() as db:retained=dict(db.execute('SELECT * FROM results WHERE id=?',(stored_ref,)).fetchone())
    check('STORE mode retains only a wrapped recipient result with a consent-derived deadline',decoded['retained'] and '<Account' not in retained['body'] and 86380<retained['expires']-time.time()<=86400)
    # Install an already-expired retention fixture, then observe its real
    # background purge. This does not claim that a whole real day elapsed.
    with recipient.db(True) as db:db.execute('UPDATE results SET expires=? WHERE id=?',(time.time()-1,stored_ref))
    def retained_count():
        with recipient.db() as db:return db.execute('SELECT count(*) FROM results WHERE id=?',(stored_ref,)).fetchone()[0]
    wait(retained_count,lambda n:n==0)
    check('The actual FIU worker purges an already-expired encrypted STORE fixture',retained_count()==0)

    # Replay of read messages rechecks current authority and active links.
    fetch_body=message('aa',consentId=consent['id']);recipient.upstream.call('POST','/Consent/fetch',fetch_body)
    with app.store.reader() as db:first_leg=dict(db.execute('SELECT * FROM aa_session_legs WHERE session_id=? AND fip_id=? LIMIT 1',(session['sessionId'],provider.config['id'])).fetchone())
    provider_body=message('fip',sessionId=first_leg['provider_session']);client.call('POST','/FI/fetch',provider_body)
    api(scope+'/consents/'+consent['id']+'/state',{'state':'PAUSED'})
    wait(lambda:provider_state(provider,own['id']),lambda state:state=='PAUSED')
    denied('Replaying a previously successful provider fetch rechecks paused consent',lambda:client.call('POST','/FI/fetch',provider_body),'ConsentPaused')
    check('Replaying a consent status read observes its current paused state',recipient.upstream.call('POST','/Consent/fetch',fetch_body)['status']=='PAUSED')
    api(scope+'/consents/'+consent['id']+'/state',{'state':'ACTIVE'})
    wait(lambda:provider_state(provider,own['id']),lambda state:state=='ACTIVE')
    client.call('POST','/Consent/Notification',message('fip',Notifier={'id':config['id'],'type':'AA'},ConsentStatusNotification={'consentId':own['id'],'consentStatus':'PAUSED'}))
    check('A delayed older pause notification cannot overwrite the latest approved state',provider_state(provider,own['id'])=='ACTIVE')

    short,_=new_consent({'consentExpiry':stamp(time.time()+3)},[links[0]['id']])
    wait(lambda:api(scope),lambda x:next(c for c in x['consents'] if c['id']==short['id'])['state']=='EXPIRED',timeout=8)
    denied('Natural clock expiry blocks new data admission',lambda:api(scope+'/consents/'+short['id']+'/request',{'request_id':str(uuid.uuid4()),'range':bounds}),'ConsentExpired')

    # Three refused challenges persist across requests. The successful
    # customer channel token does not unlock an exhausted challenge.
    account=provider.public_account(next(a for a in provider.config['accounts'] if a['customer']==detail['Customer']['id']))
    challenge=client.call('POST','/Accounts/link',message('fip',Customer={'id':detail['Customer']['id'],'Accounts':[account]}))
    token=client.call('POST','/control/challenge',{'refNumber':challenge['RefNumber']},control=True)['token'];wrong='000000' if token!='000000' else '111111'
    for attempt in range(3):denied('A refused provider challenge consumes attempt '+str(attempt+1),lambda:client.call('POST','/Accounts/link/verify',message('fip',refNumber=challenge['RefNumber'],token=wrong)),'InvalidToken')
    denied('A locked challenge refuses even the correct customer token',lambda:client.call('POST','/Accounts/link/verify',message('fip',refNumber=challenge['RefNumber'],token=token)),'InvalidToken')
    with provider.db() as db:used=db.execute('SELECT * FROM challenges WHERE complete=1 LIMIT 1').fetchone()
    denied('An already consumed challenge cannot authorize a fresh linking operation',lambda:client.call('POST','/Accounts/link/verify',message('fip',refNumber=used['id'],token=provider.open(used['token']).decode())),'InvalidRefNumber')

    api(scope+'/links/'+links[0]['id']+'/delink',{})
    denied('Delinking an approved account blocks access to its completed aggregate session',lambda:api(scope+'/sessions/'+empty['sessionId']+'/inspect',{'recipient_jwk':jwk}),'InvalidLinkRefNumber')

    # Private role checkpoints include consistent SQLite copies and wrapped
    # FIU material; restore remains deliberately inactive and verifies first.
    checkpoint=directory/'aa-checkpoint';saved=snapshot(directory,checkpoint);restored=directory/'inactive-role-restore';result=restore(checkpoint,restored)
    relative=Path(recipient.path).parent.relative_to(directory/'aa-scopes')
    with sqlite3.connect(restored/'aa-scopes'/relative/'role.db') as db:preserved=db.execute('SELECT private FROM keys WHERE id=?',(row['key_reference'],)).fetchone()[0]
    with recipient.db() as db:original_private=db.execute('SELECT private FROM keys WHERE id=?',(row['key_reference'],)).fetchone()[0]
    check('Inactive recovery preserves all role journals and wrapped recipient material',result['activation']=='inactive' and saved['role_databases']==len(roles) and preserved==original_private)
    entries=json.loads((checkpoint/'manifest.json').read_text())['files'];target=checkpoint/'files'/entries[0]['path'];target.write_bytes(target.read_bytes()+b'corrupt')
    denied('Tampered role recovery input is refused before a destination is created',lambda:restore(checkpoint,directory/'refused-role-restore'),'AA_RECOVERY_INTEGRITY')
    check('A refused role restore leaves no partially activated installation',not (directory/'refused-role-restore').exists())


def provider_state(provider,identity):
    with provider.db() as db:
        row=db.execute('SELECT state FROM consents WHERE id=?',(identity,)).fetchone()
        return row['state'] if row else None
