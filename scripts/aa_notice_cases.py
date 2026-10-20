"""Real outage, recipient-commit crash, retry, migration and lease-fencing cases."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request

from avenra.aa.contracts import stamp
from avenra.aa.fixture import start,stop
from avenra.aa.peer import Peer
from avenra.aa.recovery import snapshot,restore
from avenra.aa.wire import message
from avenra.private_config import write_document
from avenra.util import canonical,digest,uid


def run_notice_cases(ctx):
    app,api,scope,check,denied,wait=[ctx[k] for k in ['app','api','scope','check','denied','wait']]
    root,directory,tenant,env,deployment,processes=[ctx[k] for k in ['ROOT','directory','tenant','env','deployment','processes']]
    with app.store.reader() as db:provider_id=db.execute("SELECT p.fip_id FROM aa_provider_consents p JOIN aa_links l ON l.id=p.link_id WHERE p.consent_id=? AND l.state='LINKED' ORDER BY p.fip_id LIMIT 1",(ctx['consent']['id'],)).fetchone()[0]
    spec=next(s for s in deployment['processes'] if s['name']==provider_id);provider=Peer(Path(spec['directory'])/'peer.json');client=app.aa.peer(tenant,env,provider_id)
    index=next(i for i,p in enumerate(processes) if str(provider.path) in p.args)
    gateway=next(s for s in deployment['processes'] if s['name']=='ingress');gateway_index=next(i for i,p in enumerate(processes) if str(Path(gateway['directory'])/'gateway.json') in p.args)
    base=scope+'/peers/'+provider_id+'/notices'
    def status(identity=None):return api(base+('/'+identity if identity else ''))
    def enqueue():
        with provider.db(True) as db:
            row=db.execute('SELECT body,path FROM notices ORDER BY id LIMIT 1').fetchone();body=json.loads(row['body']);body.update(txnid=str(uuid.uuid4()),timestamp=stamp(time.time()-900));provider.notify(db,row['path'],body)
        return body['txnid']
    def state(identity):return status(identity)['notices'][0]['state']
    def inbox_count(identity):
        with app.store.reader() as db:return db.execute('SELECT count(*) FROM aa_inbox WHERE environment_id=? AND peer_id=? AND txnid=?',(env,provider_id,identity)).fetchone()[0]
    wait(lambda:status()['counts'].get('pending',0),lambda n:n==0)
    check('The current console exposes scoped participant notification observations',status()['journal_version']==2 and 'policy' in status())
    # Stop the actual mTLS gateway, while the application API and provider
    # process keep serving. Two notices must not starve one another.
    stop(processes[gateway_index]);first,second=enqueue(),enqueue()
    wait(lambda:(state(first),state(second)),lambda x:x==('failed','failed'),timeout=12)
    failed=status(first);attempts=failed['attempts']
    check('A real connection outage becomes a visible exhausted notification',len(attempts)==3 and all(a['outcome']=='unconfirmed' and a['error']=='OUTCOME_UNKNOWN' for a in attempts))
    check('Retry due times prevent a tight dispatch loop',all(b['started_at']-a['finished_at']>=.07 for a,b in zip(attempts,attempts[1:])))
    check('An unavailable notice does not permanently starve its sibling',state(second)=='failed' and len(status(second)['attempts'])==3)
    # Provider data work remains independent of the interrupted notice thread.
    with app.store.reader() as db:
        grant=dict(db.execute("SELECT p.* FROM aa_provider_consents p JOIN aa_links l ON l.id=p.link_id WHERE p.consent_id=? AND p.fip_id=? AND l.state='LINKED' LIMIT 1",(ctx['consent']['id'],provider_id)).fetchone())
    from avenra.aa.crypto import ephemeral
    _,public=ephemeral(time.time()+120)
    req=message('fip',Consent={'id':grant['id'],'digitalSignature':json.loads(grant['artifact_json'])['signedConsent'].split('.')[2]},FIDataRange={'from':ctx['detail']['FIDataRange']['from'],'to':stamp()},KeyMaterial=public)
    # Admission needs current authoritative consent; an already accepted
    # provider job can finish preparing while release remains fail-closed.
    processes[gateway_index]=start(gateway,root);wait(lambda:provider.upstream.call('GET','/Heartbeat'))
    prepared=client.call('POST','/FI/request',req);stop(processes[gateway_index])
    ready=wait(lambda:client.call('POST','/control/status',{},control=True),lambda value:any(s['id']==prepared['sessionId'] and s['state']=='COMPLETED' for s in value['sessions']))
    check('Accepted provider work can prepare while notification delivery is offline',any(s['id']==prepared['sessionId'] and s['state']=='COMPLETED' for s in ready['sessions']))
    denied('Encrypted provider data cannot be released without current authoritative consent',lambda:client.call('POST','/FI/fetch',message('fip',sessionId=prepared['sessionId'])),'PeerOutcomeUnconfirmed')
    stop(processes[index]);processes[index]=start(spec,root);wait(lambda:client.call('POST','/control/status',{},control=True))
    check('Process restart preserves exhaustion and all failed delivery attempts',state(first)=='failed' and len(status(first)['attempts'])==3)
    processes[gateway_index]=start(gateway,root)
    wait(lambda:provider.upstream.call('GET','/Heartbeat'))
    retry_id=str(uuid.uuid4());resumed=api(base+'/'+first+'/retry',{'request_id':retry_id})
    wait(lambda:state(first),lambda x:x=='delivered')
    final=status(first)
    check('An operator resumes the original notice with fresh transport timestamp',resumed['notice_id']==first and inbox_count(first)==1 and final['notices'][0]['generation']==1)
    check('Retry retains failed observations beside the later successful attempt',len(final['attempts'])==4 and final['attempts'][-1]['outcome']=='delivered' and final['attempts'][-1]['generation']==1)
    check('An identical operator retry is idempotent after eventual delivery',api(base+'/'+first+'/retry',{'request_id':retry_id})==resumed and status(first)['notices'][0]['attempts']==4)
    denied('A retry identity cannot target another notification',lambda:api(base+'/'+second+'/retry',{'request_id':retry_id}),'CONFLICT')
    denied('A delivered notification cannot start another retry generation',lambda:api(base+'/'+first+'/retry',{'request_id':str(uuid.uuid4())}),'NOTICE_NOT_FAILED')
    denied('A participant outside this environment cannot expose its delivery journal',lambda:api(scope+'/peers/'+uid('foreign')+'/notices'),'InvalidPeer')
    credentials=ctx['credentials'];opener=ctx['opener'];origin=ctx['origin']
    def http_code(path,token=None,body=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        req=Request(origin+path,data=canonical(body).encode() if body is not None else None,headers=headers)
        try:response=opener.open(req,timeout=10)
        except HTTPError as error:response=error
        with response:response.read();return response.status
    check('Unauthenticated callers cannot inspect participant delivery journals',http_code(base)==401)
    reader=app.create_api_key(tenant,'Notification observation acceptance',permissions=['read'],expires_at=time.time()+120)
    check('Read-only credentials can observe but cannot trigger an operator retry',http_code(base,reader)==200 and http_code(base+'/'+second+'/retry',reader,{'request_id':str(uuid.uuid4())})==403)
    # A real role process exits immediately after receiving a successful AA
    # reply. Its accepted notice must recover from the retained lease.
    wait(lambda:status()['counts'].get('pending',0),lambda n:n==0)
    client.call('POST','/control/fault',{'request_id':str(uuid.uuid4()),'path':'/Notification/after_accept'},control=True)
    crash=enqueue();wait(lambda:processes[index].poll(),lambda code:code is not None)
    check('The role actually crashes after the recipient committed the notice',processes[index].returncode==77 and inbox_count(crash)==1)
    processes[index]=start(spec,root);wait(lambda:client.call('POST','/control/status',{},control=True));wait(lambda:state(crash),lambda x:x=='delivered',timeout=12)
    history=status(crash)['attempts']
    with app.store.reader() as db:events=db.execute("SELECT count(*) FROM aa_events WHERE environment_id=? AND kind='provider_observation' AND resource_id=?",(env,crash)).fetchone()[0]
    check('Expired worker lease and restart preserve original recipient acceptance',len(history)==2 and history[0]['error']=='WORKER_LEASE_EXPIRED' and history[1]['outcome']=='delivered' and inbox_count(crash)==1 and events==1)
    with app.store.reader() as db:actor_events=db.execute("SELECT count(*) FROM audit_events WHERE environment_id=? AND kind='aa.notification_retry' AND entity_id=?",(env,first)).fetchone()[0]
    check('Operator retry is attributable in the existing immutable audit trail',actor_events>=1)

    # Build a private legacy-layout fixture from the actual role's base table
    # definitions. Its files/keys are copies; no running journal is rewritten.
    legacy_dir=directory/'legacy-notice-fixture';legacy_dir.mkdir(mode=0o700)
    for source in Path(spec['directory']).rglob('*'):
        if source.is_file() and source.name not in {'role.db','role.db-wal','role.db-shm','service.log'}:
            target=legacy_dir/source.relative_to(spec['directory']);target.parent.mkdir(mode=0o700,parents=True,exist_ok=True);shutil.copyfile(source,target);target.chmod(0o600)
    names=['replay','links','challenges','consents','sessions','notices','keys','received','results','commands','faults']
    with provider.db() as source,sqlite3.connect(legacy_dir/'role.db') as target:
        for name in names:target.execute(source.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone()[0])
        body=message('aa',Notifier={'type':'FIP','id':provider_id},AccountLinkStatusNotification={'accRefNumber':'synthetic','customerAddress':'synthetic@avenra-aa','linkRefNumber':str(uuid.uuid4()),'linkStatus':'LINKED'})
        target.execute('INSERT INTO notices VALUES(?,?,?,?,?)',(body['txnid'],'/Account/link/Notification',canonical(body),'pending',20))
    (legacy_dir/'role.db').chmod(0o600);legacy=Peer(legacy_dir/'peer.json')
    with legacy.db() as db:legacy_row=dict(db.execute('SELECT * FROM notices').fetchone());schedule=dict(db.execute('SELECT * FROM notice_schedules').fetchone())
    check('Legacy exhausted work migrates visibly without erasing its attempt count',legacy_row['attempts']==20 and legacy_row['state']=='failed' and schedule['last_error']=='LEGACY_ATTEMPTS_EXHAUSTED')
    with legacy.db(True) as db:
        body['txnid']=str(uuid.uuid4());legacy.notify(db,'/Account/link/Notification',body)
    with ThreadPoolExecutor(2) as pool:claims=list(pool.map(lambda _:legacy.notices.claim(),range(2)))
    claimed=next(x for x in claims if x)
    check('Concurrent queue claimers obtain only one live lease',sum(c is not None for c in claims)==1)
    time.sleep(2.1);new=legacy.notices.claim()
    check('An expired lease is reclaimed under a different fencing identity',new and new['id']==claimed['id'] and new['token']!=claimed['token'])
    check('A late old worker cannot overwrite the new worker lease',legacy.notices.complete(claimed,'delivered',status=200) is False)
    legacy.notices.complete(new,'delivered',status=200)
    with legacy.db() as db:
        late=db.execute('SELECT count(*) FROM notice_observations WHERE attempt_id=?',(claimed['token'],)).fetchone()[0]
        states=[r['outcome'] for r in db.execute('SELECT outcome FROM notice_attempts ORDER BY started_at')]
    check('Late-result evidence is retained separately from closed immutable attempts',late==1 and states==['unconfirmed','delivered'])
    def immutable(sql,values):
        try:
            with legacy.db(True) as db:db.execute(sql,values)
        except sqlite3.IntegrityError:return True
        return False
    check('Accepted notice payload identity cannot be overwritten',immutable('UPDATE notices SET body=? WHERE id=?',('{}',body['txnid'])))
    check('Closed notification history cannot be changed or deleted',immutable('UPDATE notice_attempts SET outcome=? WHERE id=?',('forged',new['token'])) and immutable('DELETE FROM notice_attempts WHERE id=?',(new['token'],)))
    saved=directory/'notice-checkpoint';copied=snapshot(directory,saved);staged=directory/'inactive-notice-restore';restore(saved,staged)
    relative=Path(spec['directory']).relative_to(directory/'aa-scopes')
    with sqlite3.connect(staged/'aa-scopes'/relative/'role.db') as db:
        restored_count=db.execute('SELECT count(*) FROM notice_attempts WHERE notice_id=?',(crash,)).fetchone()[0];restored_retries=db.execute('SELECT count(*) FROM notice_retries WHERE id=?',(retry_id,)).fetchone()[0]
    check('Inactive private-role restoration preserves retry identities and complete attempt journals',copied['role_databases']==len(deployment['roles']) and restored_count==2 and restored_retries==1)

    # The opposite direction uses the existing application outbox and its
    # real worker. No counterparty queue or new financial intent replaces it.
    fiu=ctx['fiu'];recipient_spec=next(s for s in deployment['processes'] if s['name']==fiu)
    recipient_index=next(i for i,p in enumerate(processes) if str(Path(recipient_spec['directory'])/'peer.json') in p.args)
    stop(processes[recipient_index])
    body=message('fiu',Notifier={'id':ctx['config']['id'],'type':'AA'},ConsentStatusNotification={'consentId':ctx['consent']['id'],'consentStatus':'ACTIVE'});body['timestamp']=stamp(time.time()-900)
    with app.store.transaction() as db:job=app.aa.job(db,tenant,env,'notice',fiu,ctx['consent']['id'],'/Consent/Notification',body)
    def main_job():
        with app.store.reader() as db:return dict(db.execute('SELECT * FROM aa_jobs WHERE id=?',(job,)).fetchone())
    wait(main_job,lambda value:value['state']=='failed',timeout=12)
    with app.store.reader() as db:
        work=dict(db.execute("SELECT * FROM outbox WHERE aggregate_id=? AND kind='aa_delivery'",(job,)).fetchone());failures=db.execute("SELECT count(*) FROM audit_events WHERE kind='aa.delivery_unconfirmed' AND entity_id=?",(job,)).fetchone()[0]
    check('The existing application worker records and exhausts outward notification outages',work['attempts']==3 and failures==3 and main_job()['error']=='OUTCOME_UNKNOWN')
    processes[recipient_index]=start(recipient_spec,root);wait(lambda:app.aa.peer(tenant,env,fiu).call('POST','/control/status',{},control=True))
    request_id=str(uuid.uuid4());resumed=api(scope+'/deliveries/'+job+'/retry',{'request_id':request_id})
    wait(main_job,lambda value:value['state']=='done')
    recipient=Peer(Path(recipient_spec['directory'])/'peer.json')
    with recipient.db() as db:receipts=db.execute('SELECT count(*) FROM received WHERE id=?',(body['txnid'],)).fetchone()[0]
    check('Original outward delivery succeeds after recipient restart without replacing its payload',receipts==1 and json.loads(main_job()['body_json'])==body)
    check('Replaying the outward operator retry preserves its original receipt',api(scope+'/deliveries/'+job+'/retry',{'request_id':request_id})==resumed)
    denied('A completed outward delivery cannot be retried as new work',lambda:api(scope+'/deliveries/'+job+'/retry',{'request_id':str(uuid.uuid4())}),'AA_DELIVERY_NOT_FAILED')
    with app.store.reader() as db:
        failed_data=db.execute("SELECT id FROM aa_jobs WHERE environment_id=? AND state='failed' AND kind IN ('request','fetch') LIMIT 1",(env,)).fetchone()[0]
    denied('Data failures must use original-session recovery rather than the notice retry endpoint',lambda:api(scope+'/deliveries/'+failed_data+'/retry',{'request_id':str(uuid.uuid4())}),'AA_DELIVERY_NOT_FAILED')
    denied('An outward retry identity cannot be reassigned to a different job',lambda:api(scope+'/deliveries/'+failed_data+'/retry',{'request_id':request_id}),'CONFLICT')
    check('Read-only credentials cannot recover outward deliveries',http_code(scope+'/deliveries/'+job+'/retry',reader,{'request_id':str(uuid.uuid4())})==403)
    with app.store.reader() as db:
        old_attempts=json.loads(db.execute("SELECT payload_json FROM audit_events WHERE kind='aa.delivery_retried' AND entity_id=?",(job,)).fetchone()[0])['previous_attempts']
    check('Outward retry retains the previous budget and actor in immutable audit evidence',old_attempts==3 and app.store.verify_audit()['valid'])

    app.aa.peer(tenant,env,fiu).call('POST','/control/fault',{'request_id':str(uuid.uuid4()),'path':'/Consent/Notification'},control=True)
    lost_body=message('fiu',Notifier={'id':ctx['config']['id'],'type':'AA'},ConsentStatusNotification={'consentId':ctx['consent']['id'],'consentStatus':'ACTIVE'})
    with app.store.transaction() as db:lost_job=app.aa.job(db,tenant,env,'notice',fiu,ctx['consent']['id'],'/Consent/Notification',lost_body)
    def lost_state():
        with app.store.reader() as db:return db.execute('SELECT state FROM aa_jobs WHERE id=?',(lost_job,)).fetchone()[0]
    wait(lost_state,lambda value:value=='done')
    with recipient.db() as db:accepted=db.execute('SELECT count(*) FROM received WHERE id=?',(lost_body['txnid'],)).fetchone()[0]
    with app.store.reader() as db:
        retries=db.execute("SELECT attempts FROM outbox WHERE aggregate_id=? AND kind='aa_delivery'",(lost_job,)).fetchone()[0];uncertain=db.execute("SELECT count(*) FROM audit_events WHERE entity_id=? AND kind='aa.delivery_unconfirmed'",(lost_job,)).fetchone()[0]
    check('A physically truncated outward acknowledgement recovers the original accepted notice',accepted==1 and retries==2)
    check('The outward committed-but-unconfirmed attempt remains retained',uncertain==1)

    # A stale provider cache cannot bypass current customer intent even if
    # its local copy incorrectly still says ACTIVE after a pause.
    api(scope+'/consents/'+ctx['consent']['id']+'/state',{'state':'PAUSED'})
    with provider.db(True) as db:db.execute("UPDATE consents SET state='ACTIVE' WHERE id=?",(grant['id'],))
    denied('A stale provider state cannot release data after authoritative customer pause',lambda:client.call('POST','/FI/fetch',message('fip',sessionId=prepared['sessionId'])),'ConsentPaused')
    api(scope+'/consents/'+ctx['consent']['id']+'/state',{'state':'ACTIVE'})
    wait(lambda:provider.upstream.call('GET','/Heartbeat'))
    # Hold an actual provider offline while approval is followed by revoke.
    # Delivering its original approval later must observe the current revoke.
    stop(processes[index])
    late_detail={**ctx['detail'],'consentExpiry':stamp(time.time()+120)}
    requested=api(scope+'/consents',{'fiu_id':fiu,'ConsentDetail':late_detail,'request_id':str(uuid.uuid4())})
    late=next(c for c in api(scope)['consents'] if c['handle']==requested['ConsentHandle'])
    api(scope+'/consents/'+late['id']+'/approve',{'synthetic_customer_approved':True,'link_ids':[grant['link_id']]})
    api(scope+'/consents/'+late['id']+'/state',{'state':'REVOKED'})
    with app.store.reader() as db:
        child=dict(db.execute('SELECT * FROM aa_provider_consents WHERE consent_id=?',(late['id'],)).fetchone());late_job=db.execute("SELECT id FROM aa_jobs WHERE resource_id=? AND kind='consent'",(child['id'],)).fetchone()[0]
    def job_state(identity):
        with app.store.reader() as db:return db.execute('SELECT state FROM aa_jobs WHERE id=?',(identity,)).fetchone()[0]
    wait(lambda:job_state(late_job),lambda value:value=='failed',timeout=12)
    processes[index]=start(spec,root);wait(lambda:client.call('POST','/control/status',{},control=True))
    api(scope+'/deliveries/'+late_job+'/retry',{'request_id':str(uuid.uuid4())});wait(lambda:job_state(late_job),lambda value:value=='done')
    with provider.db() as db:late_state=db.execute('SELECT state FROM consents WHERE id=?',(child['id'],)).fetchone()[0]
    check('An original approval delivered after revocation cannot reactivate the provider grant',late_state=='REVOKED')
    denied('The late original approval cannot authorize a fresh provider data session',lambda:client.call('POST','/FI/request',message('fip',Consent={'id':child['id'],'digitalSignature':json.loads(child['artifact_json'])['signedConsent'].split('.')[2]},FIDataRange={'from':ctx['detail']['FIDataRange']['from'],'to':stamp()},KeyMaterial=public)),'ConsentRevoked')
    with app.store.reader() as db:
        revoke_job=db.execute("SELECT id FROM aa_jobs WHERE resource_id=? AND kind='notice' AND path='/Consent/Notification'",(child['id'],)).fetchone()[0]
    wait(lambda:job_state(revoke_job),lambda value:value=='failed')
    api(scope+'/deliveries/'+revoke_job+'/retry',{'request_id':str(uuid.uuid4())});wait(lambda:job_state(revoke_job),lambda value:value=='done')
    check('The original failed revocation notice completes after late grant recovery',job_state(revoke_job)=='done')
