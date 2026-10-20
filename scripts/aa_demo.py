#!/usr/bin/env python3
"""Manage synthetic financial-data peers for the existing Avenra console."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from avenra.app import App
from avenra.aa.fixture import provision,start,stop
from avenra.errors import require
from avenra.gateways import EngineError
from avenra.lab_identity import private_read
from avenra.private_config import read_document,write_document
from avenra.synthetic_lab import fixture
from avenra.util import application_fingerprint,now,uid

DATA=ROOT/'.local/data';STATE=DATA/'aa-demo.json'


def owned(worker):
    result=subprocess.run(['ps','-p',str(worker['pid']),'-o','command='],capture_output=True,text=True)
    marker=worker['marker'];require(Path(marker).resolve().is_relative_to(DATA/'aa-scopes'),'AA_PROCESS_SCOPE','Preserve processes outside the financial-data fixture.',409)
    return result.returncode==0 and worker['module'] in result.stdout and marker in result.stdout


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['install','start','status','stop']);args=parser.parse_args()
    DATA.mkdir(mode=0o700,parents=True,exist_ok=True)
    lock=os.open(DATA/'aa-demo.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    state=read_document(STATE);app=None;started=[]
    try:
        if args.action in {'status','stop'}:
            if not state:print(json.dumps({'installed':False}));return 0
            for worker in state['workers']:
                worker['running']=owned(worker)
                if args.action=='stop' and worker['running']:
                    os.kill(worker['pid'],signal.SIGTERM);end=time.monotonic()+15
                    while owned(worker) and time.monotonic()<end:time.sleep(.1)
                    require(not owned(worker),'AA_PROCESS_DRAIN','An owned role is still draining; inspect its private log.',409);worker['running']=False
            if args.action=='stop':state['stopped_at']=now();write_document(STATE,state)
            print(json.dumps({k:state[k] for k in ['environment_id','environment_name','workers']}));return 0
        require(bool(state)==(args.action=='start'),'AA_DEMO_EXISTS','Use install for a new rehearsal, or start for the retained rehearsal. Existing environments are preserved.',409)
        for name in ['aa-verification.json','aa-postgres-verification.json']:
            receipt=read_document(ROOT/'.local'/name)
            from verify_aa import case_pack_hash
            require(receipt and receipt['passed'] and receipt['application_fingerprint']==application_fingerprint() and receipt['case_pack_sha256']==case_pack_hash(),'AA_QUALIFICATION','Run the current SQLite and PostgreSQL financial-data suites before starting the console rehearsal.',409)
        app=App(DATA,control_dsn=private_read(DATA/'control.dsn').decode().strip())
        if args.action=='install':
            value=fixture(app);tenant,env=value['tenant_id'],value['environment_id']
            with app.store.reader() as db:customer=db.execute('SELECT customer_id FROM bank_accounts WHERE account_id=?',(value['accounts'][0],)).fetchone()[0]
            for bank in value['banks']:
                account=app.banking.account(tenant,env,{'bank_id':bank['id'],'customer_id':customer,'vpa':uid('deposit')+'@'+bank['code'],'opening_paise':25000,'request_key':uid('account')});app.banking.provision_account(tenant,env,account['account_id'])
            name='Encrypted financial-data rehearsal'
            with app.store.transaction() as db:db.execute('UPDATE environments SET name=? WHERE id=?',(name,env));app.store.audit(db,tenant,env,'aa.rehearsal_installed',env,{'name':name})
            deployment=provision(app,tenant,env,4827,ROOT/'.local/aa-research')
            state={'classification':'synthetic','official_conformance':False,'environment_id':env,'tenant_id':tenant,'environment_name':name,'created_at':now(),'workers':[],'directory':deployment['directory']}
            write_document(STATE,state)
        tenant,env=state['tenant_id'],state['environment_id'];folder=DATA/'aa-scopes'/env
        specs=json.loads(private_read(folder/'process-spec.json'))
        for spec in specs:
            require(spec['module'] in {'avenra.service_gateway','avenra.aa.peer'} and Path(spec['directory']).resolve().is_relative_to(folder),'AA_PROCESS_SCOPE','Only this rehearsal’s trusted role processes may start.',409)
            existing=next((x for x in state['workers'] if x['name']==spec['name']),None)
            if existing and owned(existing):continue
            child=start(spec,ROOT);started.append(child)
            worker={'name':spec['name'],'module':spec['module'],'pid':child.pid,'marker':spec['arguments'][-1]}
            state['workers']=[x for x in state['workers'] if x['name']!=spec['name']]+[worker];write_document(STATE,state)
        config,_=app.aa.scope(tenant,env);deadline=time.monotonic()+30
        for identity in config['peers']:
            while True:
                require(all(child.poll() is None for child in started),'AA_PROCESS_START','A role failed to start; inspect its private service log.',409)
                try:app.aa.peer(tenant,env,identity).call('POST','/control/status',{},control=True);break
                except EngineError:
                    require(time.monotonic()<deadline,'AA_PROCESS_START','Financial-data roles did not become ready.',409);time.sleep(.1)
        state.update(started_at=now(),application_fingerprint=application_fingerprint());write_document(STATE,state)
        print(json.dumps({'installed':True,'application':'http://127.0.0.1:4827/','environment_id':env,'environment_name':state['environment_name'],'roles':len(config['peers']),'classification':'synthetic','official_conformance':False}));return 0
    except BaseException:
        for child in reversed(started):stop(child)
        raise
    finally:
        if app:app.close()
        os.close(lock)


if __name__=='__main__':raise SystemExit(main())
