#!/usr/bin/env python3
"""Cold restore of actual AA/FIP/FIU journals, identities and uncertain work."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from avenra import recovery_set,recovery_restore,recovery_native,recovery_aa,release_bundle,release_runtime
from avenra.aa import routing
from avenra.aa.contracts import stamp
from avenra.aa.crypto import signing_key,pem,b64u
from avenra.aa.fixture import provision as provision_aa,start as start_role,stop as stop_role,free_port
from avenra.aa.peer import Peer
from avenra.aa.runtime import nodes,launch_binding
from avenra.aa.wire import Client,message
from avenra.app import App
from avenra.database_deployment import provision
from avenra.errors import DomainError
from avenra.gateways import AccountingGateway,EngineError
from avenra.lab_identity import private_read,private_write
from avenra.local_database import admin_dsn
from avenra.private_config import read_document,write_document,deployment
from avenra.recovery_state import INACTIVE
from avenra.synthetic_lab import fixture
from avenra.util import application_fingerprint,canonical,digest,uid


def case_pack_hash():
    result=hashlib.sha256()
    for name in ['verify_aa_recovery.py','aa_review_oracle.cjs']:
        result.update(name.encode()+b'\0'+(ROOT/'scripts'/name).read_bytes())
    return result.hexdigest()


def role_rows(path):
    with closing(sqlite3.connect(Path(path).absolute().as_uri()+'?mode=ro',uri=True)) as db:
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        schema=db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        rows={name:sorted(canonical(row) for row in db.execute('SELECT * FROM "'+name.replace('"','""')+'"')) for name in tables}
    return {'schema':digest(schema),'tables':{name:{'rows':len(value),'sha256':digest(value)} for name,value in rows.items()}}


def crash_start(path):
    arguments=read_document(path);original=recovery_aa._registration
    def registration(directory,routes,node_id):
        record=original(directory,routes,node_id)
        if record and recovery_native.owns_listener(record['pid'],routing.origin(record['origin'])):os._exit(77)
        return record
    recovery_aa._registration=registration;recovery_aa.start(**arguments)
    return 9


def crash_activate(path):
    arguments=read_document(path);original=recovery_restore.write_document
    def write(path,value):
        original(path,value)
        if Path(path).name=='recovery-activation.json':os._exit(78)
    recovery_restore.write_document=write;recovery_restore.activate(**arguments)
    return 9


def main():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo,conninfo_to_dict
    folder=ROOT/'.local/qa'/uid('aa_coordinated_recovery');folder.mkdir(mode=0o700)
    report={'passed':False,'checks':[],'directory':str(folder),'created_at':time.time(),'application_fingerprint':application_fingerprint(),'case_pack_sha256':case_pack_hash(),
            'production_ready':False,'official_conformance':False,'scope':'Cold owned local AA/FIP/FIU topology recovery. Actual mTLS/JWS, native statements, independent Node decryption, process capture fences, original lost-response/notification recovery and expiry at replacement addresses. SSO/provider/broker/off-host and institutional qualification remain open.'}
    children=[];app=None;target=None;native_started=False;listeners=[];observers=[];contacts=[];observe_stop=threading.Event()
    def check(name,value):
        report['checks'].append({'name':name,'passed':bool(value)});print(json.dumps({'check':name,'passed':bool(value)}),flush=True)
        if not value:raise AssertionError(name)
    def denied(name,callback,expected):
        try:callback()
        except DomainError as error:check(name,error.code in expected if isinstance(expected,set) else error.code==expected)
        else:check(name,False)
    def start(command,name,environment=None):
        with (folder/(name+'.log')).open('ab') as output:
            (folder/(name+'.log')).chmod(0o600)
            child=subprocess.Popen(command,cwd=ROOT,env=environment,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        children.append(child);return child
    def wait(callback,predicate=lambda v:bool(v),timeout=30):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            try:
                value=callback()
                if predicate(value):return value
            except (OSError,URLError,DomainError,EngineError):pass
            time.sleep(.1)
        raise TimeoutError('AA recovery did not reach its required state; inspect retained private logs.')
    def stop(child):stop_role(child)
    def observe(listener):
        listener.settimeout(.2)
        while not observe_stop.is_set():
            try:
                connection,_=listener.accept();contacts.append(listener.getsockname()[1]);connection.close()
            except TimeoutError:pass
            except OSError:return
    try:
        release_key,release_trust,recovery_key,recovery_trust=[folder/name for name in ['release.key','release.trust','recovery.key','recovery.trust']]
        release_bundle.new_key(release_key,release_trust);release_bundle.new_key(recovery_key,recovery_trust)
        built=release_bundle.build(ROOT,ROOT/'.local/release-inputs/wheels',release_key,folder/'application.tar.gz','aa-topology-recovery-qualification')
        installed=release_bundle.install(folder/'application.tar.gz',release_trust,folder/'source-artifacts')
        runtime=folder/'source-runtime';release_bundle.prepare_runtime(installed['directory'],release_trust,runtime)
        admin=admin_dsn(ROOT);private_write(folder/'admin.dsn',admin);suffix=uuid.uuid4().hex[:16]
        name='avn_aa_source_'+suffix;owner='avn_aa_engine_'+suffix;password=uuid.uuid4().hex
        with psycopg.connect(admin,autocommit=True) as db:
            db.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Identifier(owner),sql.Literal(password)))
            db.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name),sql.Identifier(owner)))
            db.execute(sql.SQL('ALTER DATABASE {} SET timezone TO {}').format(sql.Identifier(name),sql.Literal('UTC')))
        native_dsn=make_conninfo(**{**conninfo_to_dict(admin),'dbname':name,'user':owner,'password':password});private_write(folder/'source-accounting.dsn',native_dsn)
        source_native=folder/'source-native';source_native.mkdir(mode=0o700)
        for role in ['accounting','fault']:
            shutil.copyfile(ROOT/'.local/bin'/role,source_native/role);(source_native/role).chmod(0o700)
        accounting_port,fault_port,source_api_port=free_port(),free_port(),free_port();accounting_origin='http://127.0.0.1:'+str(accounting_port)
        environment=os.environ.copy();environment['POSTGRES_URI']=native_dsn
        engine=start([str(source_native/'accounting'),'serve','--bind','127.0.0.1:'+str(accounting_port),'--auto-upgrade','--postgres-max-open-conns','3','--postgres-max-idle-conns','1'],'source-accounting',environment)
        fault=start([str(source_native/'fault'),'-host','127.0.0.1','-port',str(fault_port),'-seed','42'],'source-fault')
        native=AccountingGateway(accounting_origin);wait(native.health)
        data=folder/'source-deployment';provision(data,admin)
        write_document(data/'engine-endpoints.json',{'format':'avenra-loopback-engines/1','accounting':accounting_origin,'fault':'http://127.0.0.1:'+str(fault_port)})
        control_dsn=private_read(data/'control.dsn').decode().strip();app=App(data,control_dsn=control_dsn)
        value=fixture(app);tenant,env=value['tenant_id'],value['environment_id'];ledger=app.environment(tenant,env)['ledger_name']
        expected_balances=native.accounts(ledger);expected_journal=native.transactions(ledger,all_pages=True)
        topology=provision_aa(app,tenant,env,source_api_port,ROOT/'.local/aa-research',recipients=1,notice_policy={'base_seconds':1,'maximum_seconds':4,'maximum_attempts':30,'timeout_seconds':1,'lease_seconds':2})
        # The source providers use this rehearsal's dedicated native authority.
        for role in topology['roles']:
            path=Path(role['directory'])/'peer.json';config=read_document(path);config['accounting_origin']=accounting_origin;write_document(path,config)
        source_api=start([sys.executable,'-B','-m','avenra.asgi','--data',str(data),'--port',str(source_api_port),'--mode','api'],'source-api')
        role_processes={spec['name']:start_role(spec,ROOT) for spec in topology['processes']};children.extend(role_processes.values())
        credential=read_document(data/'credentials.json');opener=build_opener(ProxyHandler({}))
        def call(origin,path,body=None):
            request=Request(origin+path,data=canonical(body).encode() if body is not None else None,headers={'Content-Type':'application/json','Authorization':'Bearer '+credential['api_key']})
            try:response=opener.open(request,timeout=12)
            except HTTPError as error:response=error
            with response:result=json.load(response)
            if response.status>=400:
                error=result.get('error',{})
                raise DomainError(error.get('code','RUNTIME_NOT_READY'),error.get('message','The observed runtime is not ready.'),response.status)
            return result
        source_api_origin='http://127.0.0.1:'+str(source_api_port);wait(lambda:call(source_api_origin,'/health/live'))
        profile,loaded=app.aa.scope(tenant,env)
        for identity in profile['peers']:wait(lambda:app.aa.peer(tenant,env,identity).call('POST','/control/status',{},control=True))
        customer=profile['customers'][0];fiu=next(k for k,v in profile['peers'].items() if v['role']=='fiu')
        fip=next(k for k,v in profile['peers'].items() if v['role']=='fip' and app.aa.discover(tenant,env,{'fip_id':k,'customer':customer['vua']})['DiscoveredAccounts'])
        found=app.aa.discover(tenant,env,{'fip_id':fip,'customer':customer['vua']})
        app.aa.link(tenant,env,{'fip_id':fip,'customer':customer['vua'],'accounts':found['DiscoveredAccounts']})
        links=app.aa.overview(tenant,env)['links']
        detail={'consentStart':stamp(time.time()-5),'consentExpiry':stamp(time.time()+3600),'consentMode':'VIEW','fetchType':'PERIODIC',
                'consentTypes':['SUMMARY','TRANSACTIONS'],'fiTypes':['DEPOSIT'],'DataConsumer':{'id':fiu,'type':'FIU'},'Customer':{'id':customer['vua']},
                'Purpose':{'code':'101','text':'Synthetic recovery qualification'},'FIDataRange':{'from':stamp(time.time()-86400),'to':stamp(time.time()+300)},
                'DataLife':{'unit':'DAY','value':1},'Frequency':{'unit':'HOUR','value':20}}
        def consent(detail):
            created=app.aa.create_consent(tenant,env,{'fiu_id':fiu,'ConsentDetail':detail,'request_id':str(uuid.uuid4())})
            row=next(x for x in app.aa.overview(tenant,env)['consents'] if x['handle']==created['ConsentHandle'])
            app.aa.approve(tenant,env,row['id'],{'synthetic_customer_approved':True,'link_ids':[x['id'] for x in links]});return row['id']
        consent_id=consent(detail);expiry_at=time.time()+15;expiring=consent({**detail,'consentExpiry':stamp(expiry_at)})
        def pump():
            with app.store.reader() as db:jobs=[r['id'] for r in db.execute("SELECT * FROM aa_jobs WHERE state NOT IN ('done','failed','cancelled')")]
            for identity in jobs:app.aa.process(tenant,env,identity)
            return not jobs
        wait(pump)
        check('Source AA and actual mTLS FIP/FIU peers establish linked accounts and signed consents',len(links)==1 and all(p.poll() is None for p in role_processes.values()))
        request_id=str(uuid.uuid4());bounds={'from':detail['FIDataRange']['from'],'to':stamp()}
        session=app.aa.fetch_request(tenant,env,consent_id,{'request_id':request_id,'range':bounds})
        app.aa.peer(tenant,env,fip).call('POST','/control/fault',{'request_id':str(uuid.uuid4()),'path':'/FI/request'},control=True)
        with app.store.reader() as db:job=dict(db.execute("SELECT * FROM aa_jobs WHERE kind='request' AND state='pending'").fetchone())
        app.aa.process(tenant,env,job['id'])
        provider_path=Path(next(r['directory'] for r in topology['roles'] if r['id']==fip))/'peer.json';provider=Peer(provider_path)
        status=wait(lambda:app.aa.peer(tenant,env,fip).call('POST','/control/status',{},control=True),lambda r:any(s['state']=='COMPLETED' for s in r['sessions']))
        provider_session=next(s['id'] for s in status['sessions'] if s['state']=='COMPLETED')
        with app.store.reader() as db:unknown=dict(db.execute('SELECT * FROM aa_jobs WHERE id=?',(job['id'],)).fetchone())
        check('A physical lost FIP response leaves one original encrypted provider session and an uncertain AA job',unknown['state']=='unknown' and status['response_loss_faults']==1 and len(status['sessions'])==1)
        wait(lambda:app.aa.peer(tenant,env,fip).call('POST','/control/notifications',{},control=True),lambda r:r['counts'].get('pending',0)==0)
        app.aa.peer(tenant,env,fip).call('POST','/control/fault',{'request_id':str(uuid.uuid4()),'path':'/Notification/after_accept'},control=True)
        with provider.db(True) as db:
            previous=db.execute('SELECT path,body FROM notices ORDER BY id LIMIT 1').fetchone();body=json.loads(previous['body']);body.update(txnid=str(uuid.uuid4()),timestamp=stamp(time.time()-900));provider.notify(db,previous['path'],body)
        notice_id=body['txnid'];wait(lambda:role_processes[fip].poll(),lambda r:r is not None)
        with app.store.reader() as db:inbox_before=db.execute('SELECT count(*) FROM aa_inbox WHERE txnid=?',(notice_id,)).fetchone()[0]
        check('A real provider exits after the AA committed its original notice but before local acknowledgement',role_processes[fip].returncode==77 and inbox_before==1)
        queued_body=message('fiu',Notifier={'id':profile['id'],'type':'AA'},ConsentStatusNotification={'consentId':consent_id,'consentStatus':'ACTIVE'});queued_body['timestamp']=stamp(time.time()-900)
        with app.store.transaction() as db:outbound_id=app.aa.job(db,tenant,env,'notice',fiu,consent_id,'/Consent/Notification',queued_body)
        stop(source_api);app.close();app=None
        release_runtime.transition(data,installed['directory'],release_trust,runtime,None,str(uuid.uuid4()),'aa-recovery-verifier')
        stop(engine);stop(fault)
        pg=Path('/opt/homebrew/opt/postgresql@16/bin');capture_args=[data,folder/'checkpoint',installed['directory'],release_trust,recovery_key,folder/'source-accounting.dsn',source_native,pg,str(uuid.uuid4()),'aa-recovery-verifier']
        denied('Coordinated capture refuses a live AA ingress or participant after application/native drain',lambda:recovery_set.capture(*capture_args),'RECOVERY_AA_ACTIVE')
        check('Refused live-role capture creates no purported complete set',not (folder/'checkpoint').exists())
        for child in role_processes.values():stop(child)
        source_rows={node:role_rows(path.parent/'role.db') for node,path in nodes(data).items() if node.split('/')[1]!='ingress'}
        captured=recovery_set.capture(*capture_args);verified=recovery_set.verify(folder/'checkpoint',recovery_trust,release_trust)
        aa_manifest=read_document(folder/'checkpoint/financial-data/manifest.json')
        source_bytes={entry['path']:release_bundle.sha(data/'aa-scopes'/entry['path']) for entry in aa_manifest['files']}
        check('Cold capture retains all original participant journals under separate external recovery/application trust',sum(x['path'].endswith('/role.db') for x in aa_manifest['files'])==3 and set(verified['manifest']['databases'])=={'control','accounting'})
        check('Every captured participant has the complete original SQLite rows and schema',all(role_rows(folder/'checkpoint/financial-data/files'/path.parent.relative_to(data/'aa-scopes')/'role.db')==source_rows[node] for node,path in nodes(data).items() if node.split('/')[1]!='ingress'))
        from avenra_fidelity.archive import encrypt_snapshot,recovery_key as archive_key,restore_snapshot
        key=archive_key(folder/'archive.key',create=True);encrypted=encrypt_snapshot(folder/'checkpoint',folder/'checkpoint.enc',key)
        decrypted=restore_snapshot(folder/'checkpoint.enc',folder/'decrypted',key)
        check('The entire AA/native/application checkpoint restores through authenticated encryption',decrypted['manifest_hash']==encrypted['manifest_hash'] and recovery_set.verify(folder/'decrypted',recovery_trust,release_trust)['set_id']==captured['set_id'])
        target=folder/'recovered';recovery_set.stage(folder/'decrypted',target,recovery_trust,release_trust)
        first_config=next(iter(nodes(target/'data').values()))
        denied('An ordinary restored AA launcher cannot reuse source listeners',lambda:launch_binding(first_config),'RECOVERY_AA_REQUIRED')
        target_scope=target/'data/aa-scopes'/env
        denied('An inactive restored AA client refuses implicit source egress',lambda:Client(target_scope,profile['peers'][fip],loaded['key'],profile['kid'],'fip'),'RECOVERY_AA_REQUIRED')
        recovery_restore.restore_databases(target,recovery_trust,release_trust,folder/'admin.dsn',pg,str(uuid.uuid4()),'aa-recovery-verifier')
        new_accounting,new_fault,api_port=free_port(),free_port(),free_port();api_origin='http://127.0.0.1:'+str(api_port)
        recovery_native.start(target,new_accounting,new_fault,recovery_trust,release_trust);native_started=True
        accounting='http://127.0.0.1:'+str(new_accounting);fault_origin='http://127.0.0.1:'+str(new_fault);activation_id=str(uuid.uuid4())
        denied('Owned native services alone cannot activate unbound AA journals',lambda:recovery_restore.activate(target,recovery_trust,release_trust,accounting,fault_origin,activation_id,'aa-recovery-verifier'),'RECOVERY_AA_REQUIRED')
        ports={node:free_port() for node in nodes(target/'data')};start_id=str(uuid.uuid4())
        arguments={'directory':str(target),'ports':ports,'api_origin':api_origin,'recovery_trust':str(recovery_trust),'release_trust':str(release_trust),'request_id':start_id,'actor':'aa-recovery-verifier'}
        damaged=target_scope/'signing-key.pem';saved=damaged.read_bytes();damaged.write_bytes(b'changed');damaged.chmod(0o600)
        denied('AA startup refuses changed staged signing material before publishing routes',lambda:recovery_aa.start(**arguments),'RECOVERY_AA_BINDING')
        damaged.write_bytes(saved);damaged.chmod(0o600)
        state=next(path.parent/'role.db' for node,path in nodes(target/'data').items() if node.endswith('/'+fip));retained=state.with_name('role.saved');state.rename(retained)
        try:
            state.symlink_to(provider_path.parent/'role.db')
            denied('A linked AA journal cannot redirect restored writes into the source',lambda:recovery_aa.start(**arguments),'RECOVERY_AA_BINDING')
            state.unlink();os.link(provider_path.parent/'role.db',state)
            denied('A hard-linked AA journal is also refused before process startup',lambda:recovery_aa.start(**arguments),'RECOVERY_AA_BINDING')
        finally:state.unlink(missing_ok=True);retained.rename(state)
        instruction=folder/'start-instructions.json';write_document(instruction,arguments)
        crashed=start([sys.executable,'-B',str(Path(__file__).absolute()),'--start-crash',str(instruction)],'start-operator-crash');wait(lambda:crashed.poll(),lambda r:r is not None)
        routes=routing.load(target/'data');registered=[routing.signed_read(target/'data',path.parent/'recovery-process.json') for path in nodes(target/'data').values() if (path.parent/'recovery-process.json').exists()]
        check('Actual operator death preserves a self-registered owned AA listener and inactive routing intent',crashed.returncode==77 and len(registered)==1 and recovery_native.process_identity(registered[0]['pid'])==registered[0]['process_identity'] and (target/'data'/INACTIVE).exists())
        started=recovery_aa.start(**arguments)
        check('The exact retry reuses the surviving process and starts every captured listener',len(started['services'])==4 and started['services'][registered[0]['node_id']]['pid']==registered[0]['pid'] and len(recovery_aa.check(target)['services'])==4)
        denied('A changed AA upstream cannot reuse the original recovery intent',lambda:recovery_aa.start(**{**arguments,'api_origin':'http://127.0.0.1:'+str(free_port())}),'RECOVERY_CONFLICT')
        paused_before={node:role_rows(path.parent/'role.db') for node,path in nodes(target/'data').items() if not node.endswith('/ingress')}
        time.sleep(1.2)
        check('Restored participant processing, expiry cleanup and notice retries stay paused before activation',paused_before=={node:role_rows(path.parent/'role.db') for node,path in nodes(target/'data').items() if not node.endswith('/ingress')})
        client=Client(target_scope,profile['peers'][fip],loaded['key'],profile['kid'],'fip')
        denied('An inactive participant refuses domain/control mutations while permitting readiness probes',lambda:client.call('POST','/control/notifications',{},control=True),'RECOVERY_INACTIVE')
        route_path=target/'data'/routing.FILE;route_bytes=route_path.read_bytes();changed=read_document(route_path);changed['api_origin']='http://127.0.0.1:'+str(free_port());write_document(route_path,changed)
        denied('An unauthenticated AA route change is refused before network use',lambda:client.call('POST','/control/status',{},control=True),'RECOVERY_AA_AUTHENTICATION')
        route_path.write_bytes(route_bytes);route_path.chmod(0o600)
        changed=routing.load(target/'data');changed['nodes'][env+'/'+fip]['origin']='https://192.0.2.1:4444';routing.signed_write(target/'data',route_path,changed)
        denied('An attributed routing file still cannot enable public AA egress',lambda:client.call('POST','/control/status',{},control=True),'RECOVERY_BANK_ROUTE')
        route_path.write_bytes(route_bytes);route_path.chmod(0o600)
        changed=routing.load(target/'data');other=next(node for node in changed['nodes'] if changed['nodes'][node]['role']=='fiu');changed['nodes'][env+'/'+fip]['origin']=changed['nodes'][other]['origin'];routing.signed_write(target/'data',route_path,changed)
        try:client.call('POST','/control/status',{},control=True)
        except EngineError:check('Actual TLS refuses substituting another participant for a captured provider',True)
        else:check('Actual TLS refuses substituting another participant for a captured provider',False)
        route_path.write_bytes(route_bytes);route_path.chmod(0o600)
        for port in [source_api_port,accounting_port,fault_port,*[routing.origin(node['logical_origin']) for node in routes['nodes'].values()]]:
            listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(('127.0.0.1',port));listener.listen(8);listeners.append(listener)
            observer=threading.Thread(target=observe,args=(listener,),daemon=True);observer.start();observers.append(observer)
        for path in [data,Path(installed['directory']).parent,runtime,source_native]:path.rename(path.with_name(path.name+'-unavailable'))
        activation={'directory':str(target),'recovery_trust':str(recovery_trust),'release_trust':str(release_trust),'accounting_origin':accounting,'fault_origin':fault_origin,'request_id':activation_id,'actor':'aa-recovery-verifier'}
        instruction=folder/'activation-instructions.json';write_document(instruction,activation)
        crashed=start([sys.executable,'-B',str(Path(__file__).absolute()),'--activation-crash',str(instruction)],'activation-operator-crash');wait(lambda:crashed.poll(),lambda r:r is not None)
        check('Actual activation-operator death leaves AA workers paused behind the durable guard',crashed.returncode==78 and (target/'data'/INACTIVE).exists() and (target/'recovery-activation.json').exists())
        activated=recovery_restore.activate(**activation)
        check('Exact activation replay validates the complete AA topology and releases the original decision',activated['replayed'] and not (target/'data'/INACTIVE).exists() and 'aa-recovery.json' in activated['bound_file_hashes'])
        restored_observer=Peer(target_scope/'counterparties'/fip/'peer.json')
        try:restored_observer.upstream.call('GET','/Heartbeat',timeout=1)
        except EngineError as error:check('An unsigned ingress error is unconfirmed transport evidence rather than a definitive participant refusal',error.code=='OUTCOME_UNKNOWN')
        else:check('An unsigned ingress error is unconfirmed transport evidence rather than a definitive participant refusal',False)
        time.sleep(1.2)
        check('Activation alone cannot consume AA participant work before its authenticated aggregator is serving',paused_before=={node:role_rows(path.parent/'role.db') for node,path in nodes(target/'data').items() if not node.endswith('/ingress')})
        # No target API or worker existed before the recorded activation.
        with psycopg.connect(private_read(target/'data/control.dsn').decode().strip()) as db:
            from psycopg.rows import dict_row
            with db.cursor(row_factory=dict_row) as cursor:restored_job=dict(cursor.execute('SELECT * FROM aa_jobs WHERE id=%s',(job['id'],)).fetchone())
        check('Restoration leaves the original accepted AA job, binding and payload byte-identical',restored_job==unknown)
        local=App(target/'data',control_dsn=private_read(target/'data/control.dsn').decode().strip())
        try:
            route_path.unlink()
            pending=local.aa.process(tenant,env,job['id'])
            with local.store.reader() as db:held=dict(db.execute('SELECT * FROM aa_jobs WHERE id=?',(job['id'],)).fetchone())
            check('Missing local routing retains the original accepted data job as unconfirmed rather than a participant refusal',pending['pending'] and held['state']=='unknown' and held['error']=='RECOVERY_AA_REQUIRED' and all(held[k]==unknown[k] for k in ['id','body_json','binding_hash','resource_id','target_id']))
            route_path.write_bytes(route_bytes);route_path.chmod(0o600)
            changed=read_document(route_path);changed['api_origin']='http://127.0.0.1:'+str(free_port());write_document(route_path,changed)
            pending=local.aa.process(tenant,env,job['id'])
            with local.store.reader() as db:held=dict(db.execute('SELECT * FROM aa_jobs WHERE id=?',(job['id'],)).fetchone())
            check('An altered local route also preserves original job identity, binding and payload pending recovery',pending['pending'] and held['state']=='unknown' and held['error']=='RECOVERY_AA_AUTHENTICATION' and all(held[k]==unknown[k] for k in ['id','body_json','binding_hash','resource_id','target_id']))
        finally:
            route_path.write_bytes(route_bytes);route_path.chmod(0o600);local.close()
        api=lambda path,body=None:call(api_origin,path,body)
        api_process=start(release_runtime.command(target/'data',release_trust,'api',api_port),'recovered-api');wait(lambda:api('/health/live'))
        worker=start(release_runtime.command(target/'data',release_trust,'worker'),'recovered-worker');wait(lambda:api('/health/ready'))
        scope='/api/tenants/'+tenant+'/environments/'+env+'/banking/aa'
        overview=wait(lambda:api(scope),lambda r:any(s['id']==session['sessionId'] and s['state']=='COMPLETED' for s in r['sessions']))
        check('The same Avenra API and worker finish the original uncertain encrypted-data session',any(s['id']==session['sessionId'] and s['state']=='COMPLETED' for s in overview['sessions']))
        status=client.call('POST','/control/status',{},control=True)
        check('The relocated provider retains exactly its original session after lost-response replay',len(status['sessions'])==1 and status['sessions'][0]['id']==provider_session and status['sessions'][0]['state']=='COMPLETED')
        review=signing_key();numbers=review.public_key().public_numbers();jwk={'kty':'RSA','n':b64u(numbers.n.to_bytes((numbers.n.bit_length()+7)//8,'big')),'e':b64u(numbers.e.to_bytes(3,'big'))}
        result=api(scope+'/sessions/'+session['sessionId']+'/inspect',{'recipient_jwk':jwk})
        observed=subprocess.run(['node',str(ROOT/'scripts/aa_review_oracle.cjs')],input=canonical({'mode':'review','jwe':result['encryptedResult'],'private_key':pem(review).decode()}).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        documents=json.loads(observed.stdout) if observed.returncode==0 else {}
        check('Independent Node/OpenSSL decrypts the original FIU result after complete topology replacement',observed.returncode==0 and len(documents.get('documents',[]))==len(links) and not documents.get('retained'))
        with psycopg.connect(private_read(target/'data/control.dsn').decode().strip()) as db:
            inbox_after=db.execute('SELECT count(*) FROM aa_inbox WHERE txnid=%s',(notice_id,)).fetchone()[0]
            event_count=db.execute("SELECT count(*) FROM aa_events WHERE kind='provider_observation' AND resource_id=%s",(notice_id,)).fetchone()[0]
            outbound=db.execute('SELECT state,body_json,binding_hash FROM aa_jobs WHERE id=%s',(outbound_id,)).fetchone()
        delivery=wait(lambda:client.call('POST','/control/notifications',{'notice_id':notice_id},control=True),lambda r:r['notices'][0]['state']=='delivered')
        check('The original post-commit notice recovers once with retained failed and later successful observations',inbox_after==1 and event_count==1 and len(delivery['attempts'])>=2 and delivery['attempts'][-1]['outcome']=='delivered' and any(a['error']=='WORKER_LEASE_EXPIRED' for a in delivery['attempts']))
        check('The original AA-to-FIU notice finishes with unchanged accepted body and participant binding',outbound[0]=='done' and outbound[1]==canonical(queued_body) and outbound[2]==digest(profile))
        check('Recovered AA transport keeps logical origins while recording actual pinned new TLS addresses',client.last_channel['logical_origin']==profile['peers'][fip]['origin'] and client.last_channel['physical_origin']!=client.last_channel['logical_origin'] and client.last_channel['set_id']==captured['set_id'])
        wait(lambda:time.time()>expiry_at)
        denied('Recovery preserves original consent expiry rather than extending synthetic authorization',lambda:api(scope+'/consents/'+expiring+'/request',{'request_id':str(uuid.uuid4()),'range':bounds}),'ConsentExpired')
        saved_routes=route_path.read_bytes();route_path.unlink()
        denied('An activated AA client refuses a missing route instead of contacting its source',lambda:client.call('POST','/control/status',{},control=True),'RECOVERY_AA_REQUIRED')
        denied('Application startup also refuses missing activated AA routing',lambda:deployment(target/'data'),'RECOVERY_AA_BINDING')
        route_path.write_bytes(saved_routes);route_path.chmod(0o600)
        before=recovery_aa.check(target);node_id=env+'/'+fip;lost=before['services'][node_id];os.kill(lost['pid'],signal.SIGKILL)
        def reaped():
            try:os.waitpid(lost['pid'],os.WNOHANG)
            except ChildProcessError:pass
            return recovery_native.process_identity(lost['pid']) is None
        wait(reaped)
        restarted=recovery_aa.start(**arguments)
        check('An actual active provider crash restarts under the same trusted routing decision',restarted['reused_plan'] and restarted['services'][node_id]['pid']!=lost['pid'] and len(recovery_aa.check(target)['services'])==4)
        fresh=api(scope+'/consents/'+consent_id+'/request',{'request_id':str(uuid.uuid4()),'range':bounds})
        wait(lambda:api(scope),lambda r:any(s['id']==fresh['sessionId'] and s['state']=='COMPLETED' for s in r['sessions']))
        check('New accepted data work completes after relocation and provider restart',fresh['sessionId']!=session['sessionId'] and api(scope+'/sessions/'+fresh['sessionId']+'/inspect',{'recipient_jwk':jwk})['documents']==len(links))
        check('Data exchange retains exact authoritative native balances and journal',digest(AccountingGateway(accounting).accounts(ledger))==digest(expected_balances) and digest(AccountingGateway(accounting).transactions(ledger,all_pages=True))==digest(expected_journal))
        source=data.with_name(data.name+'-unavailable')
        check('Restored activity leaves every original private AA byte and complete SQLite row unchanged',all(release_bundle.sha(source/'aa-scopes'/name)==expected for name,expected in source_bytes.items()) and all(role_rows(source/'aa-scopes'/node.split('/')[0]/'counterparties'/node.split('/')[1]/'role.db')==expected for node,expected in source_rows.items()))
        with psycopg.connect(control_dsn) as db:
            db.execute("SET LOCAL timezone='UTC'");control_same=recovery_set.database_inventory(db)==verified['manifest']['databases']['control']
        with psycopg.connect(native_dsn) as db:
            db.execute("SET LOCAL timezone='UTC'");native_same=recovery_set.database_inventory(db)==verified['manifest']['databases']['accounting']
        check('The complete original control/accounting rows and sequences remain unchanged',control_same and native_same)
        check('No restored client or process contacts any original API, AA or native listener',not contacts)
        stop(worker);stop(api_process)
        previous=read_document(ROOT/'.local/release-baselines/before-aa-recovery.json')
        # This retained real predecessor understands bank bindings but lacks AA.
        prior=release_bundle.verify_installed(previous['directory'],previous['trust_file'])
        denied('A genuine earlier signed application without AA routing support cannot be selected',lambda:release_runtime.transition(target/'data',previous['directory'],previous['trust_file'],previous['runtime_directory'],built['artifact_id'],str(uuid.uuid4()),'aa-recovery-verifier'),'RELEASE_CAPABILITIES')
        check('Current signed application declares the configured AA route version',release_runtime.require_deployment_capabilities(target/'data',release_bundle.verify_installed(target/'artifacts'/built['artifact_id'],release_trust))=={'engine_bindings':'avenra-loopback-engines/1','aa_routes':routing.FORMAT})
        # A restored deployment must itself remain recoverable. Copied source
        # physical pins cannot prevent a new target from planning new routes.
        first_target=target;first_routes=routing.load(first_target/'data')
        recovery_aa.stop(first_target);recovery_native.stop(first_target);native_started=False
        second_capture=recovery_set.capture(first_target/'data',folder/'second-checkpoint',first_target/'artifacts'/built['artifact_id'],release_trust,recovery_key,
            first_target/'data/accounting.dsn',first_target/'native',pg,str(uuid.uuid4()),'aa-recovery-verifier')
        second_verified=recovery_set.verify(folder/'second-checkpoint',recovery_trust,release_trust)
        check('A fully restored deployment produces a second independently verified cold recovery set',second_capture['set_id']!=captured['set_id'] and second_verified['manifest']['artifact_id']==built['artifact_id'])
        for port in [api_port,new_accounting,new_fault,*[routing.origin(n['origin']) for n in first_routes['nodes'].values()]]:
            listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(('127.0.0.1',port));listener.listen(8);listeners.append(listener)
            observer=threading.Thread(target=observe,args=(listener,),daemon=True);observer.start();observers.append(observer)
        target=folder/'second-recovered';recovery_set.stage(folder/'second-checkpoint',target,recovery_trust,release_trust)
        recovery_restore.restore_databases(target,recovery_trust,release_trust,folder/'admin.dsn',pg,str(uuid.uuid4()),'aa-recovery-verifier')
        second_native,second_fault,second_api=free_port(),free_port(),free_port();second_origin='http://127.0.0.1:'+str(second_api)
        recovery_native.start(target,second_native,second_fault,recovery_trust,release_trust);native_started=True
        second_ports={node:free_port() for node in nodes(target/'data')}
        recovery_aa.start(target,second_ports,second_origin,recovery_trust,release_trust,str(uuid.uuid4()),'aa-recovery-verifier')
        before_second_api={node:role_rows(path.parent/'role.db') for node,path in nodes(target/'data').items() if not node.endswith('/ingress')}
        original_second={node:role_rows(path.parent/'role.db') for node,path in nodes(first_target/'data').items() if not node.endswith('/ingress')}
        check('The second restore retains complete first-restored participant journals before background work resumes',before_second_api==original_second)
        first_target.rename(first_target.with_name(first_target.name+'-unavailable'))
        recovery_restore.activate(target,recovery_trust,release_trust,'http://127.0.0.1:'+str(second_native),'http://127.0.0.1:'+str(second_fault),str(uuid.uuid4()),'aa-recovery-verifier')
        check('New physical routes replace copied prior-recovery pins without changing original logical profiles',routing.load(target/'data')['set_id']==second_capture['set_id'] and read_document(target/'data/aa-scopes'/env/'profile.json')==profile)
        second_api_process=start(release_runtime.command(target/'data',release_trust,'api',second_api),'second-recovered-api')
        second_worker=start(release_runtime.command(target/'data',release_trust,'worker'),'second-recovered-worker')
        wait(lambda:call(second_origin,'/health/ready'))
        second_result=call(second_origin,scope+'/sessions/'+session['sessionId']+'/inspect',{'recipient_jwk':jwk})
        second_observed=subprocess.run(['node',str(ROOT/'scripts/aa_review_oracle.cjs')],input=canonical({'mode':'review','jwe':second_result['encryptedResult'],'private_key':pem(review).decode()}).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        check('The original encrypted-data session remains independently decryptable through two complete relocations',second_observed.returncode==0 and json.loads(second_observed.stdout)['documents']==documents['documents'])
        with psycopg.connect(private_read(target/'data/control.dsn').decode().strip()) as db:
            original_second_notice=db.execute('SELECT count(*) FROM aa_inbox WHERE txnid=%s',(notice_id,)).fetchone()[0]
        check('The original acknowledged notice remains accepted exactly once across both recovery generations',original_second_notice==1)
        check('Neither restored generation contacts an earlier API, AA or native listener',not contacts)
        stop(second_worker);stop(second_api_process)
        check('Qualification observed one fixed application and case pack',report['application_fingerprint']==application_fingerprint() and report['case_pack_sha256']==case_pack_hash())
        report.update(passed=True,set_id=captured['set_id'],artifact_id=built['artifact_id'],tenant_id=tenant,environment_id=env,session_id=session['sessionId'],provider_session_id=provider_session,notice_id=notice_id,source_listener_contacts=len(contacts))
    except Exception as error:report.update(error=type(error).__name__+': '+str(error),traceback=traceback.format_exc())
    finally:
        for child in reversed(children):stop(child)
        if app:app.close()
        if target and (target/'data'/routing.FILE).exists():
            try:recovery_aa.stop(target)
            except Exception as error:report.update(passed=False,aa_cleanup_error=type(error).__name__+': '+str(error))
        if native_started:
            try:recovery_native.stop(target)
            except Exception as error:report.update(passed=False,native_cleanup_error=type(error).__name__+': '+str(error))
        observe_stop.set()
        for listener in listeners:listener.close()
        for observer in observers:observer.join(timeout=1)
        report['completed_at']=time.time();write_document(folder/'verification.json',report)
        write_document(ROOT/'.local/aa-coordinated-recovery-verification.json',report)
        print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'directory':str(folder),'error':report.get('error'),'aa_cleanup_error':report.get('aa_cleanup_error'),'native_cleanup_error':report.get('native_cleanup_error')}),flush=True)
        if not report['passed']:print(report.get('traceback',''),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--start-crash':raise SystemExit(crash_start(Path(sys.argv[2])))
    if len(sys.argv)==3 and sys.argv[1]=='--activation-crash':raise SystemExit(crash_activate(Path(sys.argv[2])))
    raise SystemExit(main())
