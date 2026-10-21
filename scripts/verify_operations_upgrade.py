#!/usr/bin/env python3
"""Upgrade a real preserved predecessor release without rewinding its money."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback
import uuid
from urllib.error import HTTPError,URLError
from urllib.request import HTTPSHandler,ProxyHandler,Request,build_opener

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from avenra import release_bundle as bundle
from avenra import release_runtime as releases
from avenra.database_deployment import upgrade_operations,upgrade_work_queue
from avenra.deployment_identity import issue,client_context
from avenra.errors import DomainError
from avenra.lab_identity import private_read,private_write
from avenra.private_config import write_document
from avenra.runtime_rehearsal import free_port,wait
from avenra.util import application_fingerprint,canonical,digest,uid


def case_pack_hash():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def crash_child(path):
    import avenra.operations as operations
    instructions=json.loads(private_read(path));original=operations.install_schema
    def die(*args,**kwargs):
        original(*args,**kwargs);os._exit(76)
    operations.install_schema=die
    upgrade_operations(**instructions);return 9


def main():
    import psycopg
    folder=ROOT/'.local/qa'/uid('operations_upgrade');folder.mkdir(mode=0o700,parents=True)
    report={'passed':False,'checks':[],'directory':str(folder),'created_at':time.time(),'application_fingerprint':application_fingerprint(),'case_pack_sha256':case_pack_hash(),
            'production_ready':False,'scope':'Actual preserved signed predecessor, new restricted database, real native payment, drained additive migration, operator death during DDL, code activation/rollback and retained financial history. One closed migration recipe; not general fleet/schema evolution qualification.'}
    children=[]
    def check(name,condition):
        report['checks'].append({'name':name,'passed':bool(condition)});print(json.dumps({'check':name,'passed':bool(condition)}),flush=True)
        if not condition:raise AssertionError(name)
    def denied(name,callback,code):
        try:callback()
        except DomainError as error:return check(name,error.code==code)
        check(name,False)
    def start(command,name):
        path=folder/(name+'.log')
        with path.open('ab') as output:
            path.chmod(0o600);child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT)
        children.append(child);return child
    def stop(child):
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=20)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
    try:
        baseline=json.loads(private_read(ROOT/'.local/release-baselines/before-operations.json'))
        old=bundle.verify_installed(baseline['directory'],baseline['trust_file'])
        check('The predecessor is a real preserved signed artifact with the pre-operations schema inventory',old['artifact_id']==baseline['artifact_id'] and old['manifest']['schemas']==bundle.BASE_SCHEMAS and not (Path(old['directory'])/'avenra/operations.py').exists())
        key,trust=folder/'signing.raw',folder/'trust.raw';bundle.new_key(key,trust)
        archive=folder/'candidate.tar.gz';built=bundle.build(ROOT,ROOT/'.local/release-inputs/wheels',key,archive,'operations-upgrade-qualification')
        new=bundle.install(archive,trust,folder/'artifacts');runtime=folder/'runtime';bundle.prepare_runtime(new['directory'],trust,runtime)
        check('The candidate declares the reviewed additive operations schema',new['manifest']['schemas']==bundle.SCHEMAS and built['application_fingerprint']==report['application_fingerprint'])
        data=folder/'deployment'
        code='''import sys,json,hashlib,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from avenra.database_deployment import provision
from avenra.local_database import admin_dsn
from avenra.app import App
from avenra.production import ROLE_CAPABILITIES
p=Path(sys.argv[2]);r=provision(p,admin_dsn(sys.argv[3]))
key=json.loads((p/'credentials.json').read_text())['api_key']
app=App(p,control_dsn=(p/'control.dsn').read_text().strip())
try:
 with app.store.reader() as db:env=db.execute("SELECT id FROM environments WHERE tenant_id='tenant_avenra' AND purpose='interactive' ORDER BY created_at LIMIT 1").fetchone()[0]
 app.runtime.grant('tenant_avenra',env,{'principal':'service:'+hashlib.sha256(('Bearer '+key).encode()).hexdigest(),'capabilities':ROLE_CAPABILITIES['administrator'],'environment_ids':[env],'expires_at':time.time()+3600},'offline-rehearsal-installer')
finally:app.close()
print(json.dumps({'database':r['database']}))'''
        installer=start([str(Path(baseline['runtime_directory'])/'bin/python'),'-I','-B','-c',code,old['directory'],str(data),str(ROOT)],'predecessor-installer')
        wait(lambda:installer.poll() is not None,timeout=45);check('The predecessor itself installs the original database and runtime/migration identities',installer.returncode==0)
        dsn=private_read(data/'control.dsn').decode().strip()
        with psycopg.connect(dsn,autocommit=True) as db:missing=db.execute("SELECT to_regclass('ops_schema')").fetchone()[0] is None
        check('No operations schema is faked into the predecessor deployment',missing)
        api=issue(data,'migration-api');config=json.loads(private_read(data/'deployment.json'));config['http']={'tls_identity':api['reference'],'require_client_certificate':True};write_document(data/'deployment.json',config)
        releases.transition(data,old['directory'],baseline['trust_file'],baseline['runtime_directory'],None,str(uuid.uuid4()),'upgrade-verifier')
        port=free_port();origin='https://127.0.0.1:'+str(port);opener=build_opener(ProxyHandler({}),HTTPSHandler(context=client_context(data,api['reference'])))
        token=json.loads(private_read(data/'credentials.json'))['api_key']
        def call(path,body=None):
            request=Request(origin+path,data=canonical(body).encode() if body is not None else None,headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
            try:response=opener.open(request,timeout=10)
            except HTTPError as error:response=error
            with response:return response.status,json.load(response)
        server=start(releases.command(data,baseline['trust_file'],'combined',port),'predecessor-api');wait(lambda:call('/health/ready'),lambda r:r[0]==200)
        with psycopg.connect(dsn,autocommit=True) as db:env=db.execute("SELECT id FROM environments WHERE tenant_id='tenant_avenra' AND purpose='interactive' ORDER BY created_at LIMIT 1").fetchone()[0]
        scope='/api/tenants/tenant_avenra/environments/'+env
        accounts=wait(lambda:call(scope+'/accounts'),lambda r:r[0]==200)[1]['accounts'];payer=next(x for x in accounts if x['role']=='payer');payee=next(x for x in accounts if x['role']=='payee')
        status,payment=call(scope+'/payments',{'payer_id':payer['id'],'payee_id':payee['id'],'amount_paise':321,'fault':'none','request_key':uid('predecessor_payment')});check('The actual predecessor accepts a native financial intent',status==202)
        original=wait(lambda:call(scope+'/payments/'+payment['id']),lambda r:r[0]==200 and r[1]['state']=='succeeded')[1]
        check('The predecessor completes the intent before migration',original['ledger_transaction_id'] is not None)
        request_id=str(uuid.uuid4());arguments={'directory':str(data),'artifact_directory':new['directory'],'trusted_public_file':str(trust),'expected':old['artifact_id'],'request_id':request_id,'actor':'upgrade-verifier'}
        denied('A live predecessor runtime prevents migration through the shared database fence',lambda:upgrade_operations(**arguments),'RELEASE_NODES_ACTIVE')
        stop(server)
        def core_state():
            with psycopg.connect(dsn,autocommit=True) as db:
                values={}
                for table,key_column in [('tenants','id'),('environments','id'),('accounts','id'),('payments','id'),('bank_operations','id'),('upi_payments','id'),('audit_events','sequence'),('outbox','id')]:
                    values[table]=[list(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY '+key_column)]
                pointer=db.execute('SELECT artifact_id,application_fingerprint,generation FROM release_active').fetchone()
            return digest(values),pointer
        before,pointer=core_state()
        denied('New code cannot activate before its declared schema is installed',lambda:releases.transition(data,new['directory'],trust,runtime,old['artifact_id'],str(uuid.uuid4()),'upgrade-verifier'),'RELEASE_SCHEMA')
        instructions=folder/'crash-instructions.json';private_write(instructions,canonical(arguments))
        crashed=start([sys.executable,'-B',str(Path(__file__).resolve()),'--crash',str(instructions)],'operator-crashed-during-ddl')
        wait(lambda:crashed.poll() is not None,timeout=25)
        with psycopg.connect(dsn,autocommit=True) as db:absent=db.execute("SELECT to_regclass('ops_schema'),to_regclass('ops_alerts'),to_regclass('ops_queue_scope')").fetchone()==(None,None,None)
        check('Actual operator death during DDL rolls back every new table/index',crashed.returncode==76 and absent and core_state()==(before,pointer))
        migrated=upgrade_operations(**arguments);check('The reviewed migration commits without changing core rows or the release pointer',migrated['installed'] and not migrated['already_installed'] and core_state()==(before,pointer))
        check('Retrying the original migration identity does not repeat DDL',upgrade_operations(**arguments)['replayed'])
        denied('A changed migration actor cannot reuse the original identity',lambda:upgrade_operations(**{**arguments,'actor':'different-actor'}),'IDEMPOTENCY_CONFLICT')
        with psycopg.connect(dsn,autocommit=True) as db:
            for statement in ['UPDATE ops_schema SET version=99','DELETE FROM ops_alert_events','ALTER TABLE ops_alerts ADD COLUMN forbidden int']:
                refused=False
                try:db.execute(statement)
                except psycopg.errors.InsufficientPrivilege:refused=True
                check('Existing runtime identity refuses migrated-schema privilege escalation: '+statement.split()[0],refused)
        # Preserve actual additive-only rollback evidence with a genuine
        # control-v9 operations release before the separate v10 retry change.
        intermediate_baseline=json.loads(private_read(ROOT/'.local/release-baselines/before-work-queue.json'))
        intermediate=bundle.verify_installed(intermediate_baseline['directory'],intermediate_baseline['trust_file'])
        check('The intermediate operations release is a genuine preserved control-v9 artifact',intermediate['manifest']['schemas']==bundle.OPERATIONS_SCHEMAS)
        activated=releases.transition(data,intermediate['directory'],intermediate_baseline['trust_file'],intermediate_baseline['runtime_directory'],old['artifact_id'],str(uuid.uuid4()),'upgrade-verifier')
        server_new=start(releases.command(data,intermediate_baseline['trust_file'],'combined',port),'upgraded-operations-api');wait(lambda:call('/health/ready'),lambda r:r[0]==200)
        after=call(scope+'/payments/'+payment['id'])[1];balances={x['id']:x['balance_paise'] for x in call(scope+'/accounts')[1]['accounts']}
        check('Operations-upgraded application retains the original settled payment and native balances',activated['artifact_id']==intermediate['artifact_id'] and after['ledger_transaction_id']==original['ledger_transaction_id'] and balances[payer['id']]==payer['balance_paise']-321 and balances[payee['id']]==payee['balance_paise']+321)
        operations=call(scope+'/banking/runtime')[1]['operations'];check('New monitoring is integrated in the existing API without creating replacement financial state',operations['policy'] is None and operations['open_count']==0)
        stop(server_new);rolled=releases.transition(data,old['directory'],baseline['trust_file'],baseline['runtime_directory'],intermediate['artifact_id'],str(uuid.uuid4()),'upgrade-verifier','rollback')
        server_old=start(releases.command(data,baseline['trust_file'],'combined',port),'rolled-back-predecessor');wait(lambda:call('/health/ready'),lambda r:r[0]==200)
        check('Compatible predecessor code runs after rollback while additive operations tables remain retained',rolled['artifact_id']==old['artifact_id'] and call(scope+'/payments/'+payment['id'])[1]['ledger_transaction_id']==original['ledger_transaction_id'])
        with psycopg.connect(dsn,autocommit=True) as db:retained=db.execute('SELECT version FROM ops_schema').fetchone()[0]==1
        check('Code rollback never drops the installed operations schema',retained)
        stop(server_old)
        upgraded=upgrade_work_queue(data,new['directory'],trust,old['artifact_id'],str(uuid.uuid4()),'upgrade-verifier')
        releases.transition(data,new['directory'],trust,runtime,old['artifact_id'],str(uuid.uuid4()),'upgrade-verifier')
        candidate_api=start(releases.command(data,trust,'combined',port),'current-candidate-api');wait(lambda:call('/health/ready'),lambda r:r[0]==200)
        check('The current candidate serves the same original payment after the separate reviewed retry migration',upgraded['installed'] and call(scope+'/payments/'+payment['id'])[1]['ledger_transaction_id']==original['ledger_transaction_id'])
        stop(candidate_api)
        denied('Control-v9 rollback is refused once the separate control-v10 migration is installed',lambda:releases.transition(data,old['directory'],baseline['trust_file'],baseline['runtime_directory'],new['artifact_id'],str(uuid.uuid4()),'upgrade-verifier','rollback'),'RELEASE_SCHEMA')
        check('Migration qualification uses a fixed candidate source and case pack',report['application_fingerprint']==application_fingerprint() and report['case_pack_sha256']==case_pack_hash())
        report.update(passed=True,predecessor_artifact=old['artifact_id'],candidate_artifact=new['artifact_id'],payment_id=payment['id'])
    except Exception as error:report.update(error=str(error),traceback=traceback.format_exc())
    finally:
        for child in reversed(children):stop(child)
        report['completed_at']=time.time();write_document(folder/'verification.json',report);write_document(ROOT/'.local/operations-upgrade-verification.json',report)
        print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'report':str(ROOT/'.local/operations-upgrade-verification.json'),'error':report.get('error')}),flush=True)
        if not report['passed']:print(report.get('traceback',''),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(crash_child(sys.argv[2]) if len(sys.argv)==3 and sys.argv[1]=='--crash' else main())
