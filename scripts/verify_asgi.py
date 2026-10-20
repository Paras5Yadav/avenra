#!/usr/bin/env python3
"""Observe API/worker separation, competing workers and API process restart."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import signal
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from avenra.app import App
from avenra.gateways import AccountingGateway
from avenra.local_database import admin_dsn
from avenra.util import application_fingerprint,uid


def main():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    report={'passed':False,'checks':[],'scope':'Isolated actual ASGI API and two worker processes, PostgreSQL, native accounting; local deployment exercise only.'}
    directory=ROOT/'.local/qa'/uid('asgi');directory.mkdir(parents=True,mode=0o700)
    processes=[]
    def check(name,value):
        report['checks'].append({'name':name,'passed':bool(value)})
        if not value:raise AssertionError(name)
    def start(module,arguments,name):
        log=(directory/(name+'.log')).open('ab');os.chmod(log.name,0o600)
        p=subprocess.Popen([sys.executable,'-B','-m',module,'--data',str(directory),*arguments],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
        log.close();processes.append(p);return p
    def stop(p):
        if p.poll() is None:
            p.terminate()
            try:p.wait(timeout=15)
            except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
    def wait(callback,predicate,timeout=15):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:
                value=callback()
                if predicate(value):return value
            except (OSError,urllib.error.URLError):pass
            time.sleep(.05)
        raise TimeoutError('Isolated process did not produce the required HTTP observation.')
    try:
        dbname='avenra_asgi_'+uid('pg').split('_')[1]
        with psycopg.connect(admin_dsn(ROOT),autocommit=True) as db:db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(dbname)))
        dsn=make_conninfo(**{**conninfo_to_dict(admin_dsn(ROOT)),'dbname':dbname})
        (directory/'control.dsn').write_text(dsn);(directory/'control.dsn').chmod(0o600)
        app=App(directory,control_dsn=dsn);app.bootstrap();app.close()
        credential=json.loads((directory/'credentials.json').read_text())
        tenant,token=credential['tenant_id'],credential['api_key']
        with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def api(path,body=None):
            request=urllib.request.Request('http://127.0.0.1:'+str(port)+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
            with opener.open(request,timeout=10) as response:return json.load(response)
        server=start('avenra.asgi',['--port',str(port),'--mode','api'],'api')
        wait(lambda:api('/api/health'),lambda x:x['state']=='ready')
        check('Independent ASGI API process starts',server.poll() is None)
        env=api('/api/tenants/'+tenant+'/environments',{'name':'Separate workers','fixture_profile':'legacy'})['id']
        scope='/api/tenants/'+tenant+'/environments/'+env
        time.sleep(.15)
        check('API accepts durably without an embedded worker',api(scope+'/overview')['environment']['status']=='provisioning')
        workers=[start('avenra.worker',[],'worker-'+str(i)) for i in range(2)]
        overview=wait(lambda:api(scope+'/overview'),lambda x:x['environment']['status']=='active')
        check('Separate workers provision accepted durable work',all(p.poll() is None for p in workers))
        accounts=api(scope+'/accounts')['accounts'];payer=next(a for a in accounts if a['role']=='payer');payee=next(a for a in accounts if a['role']=='payee')
        payload={'payer_id':payer['id'],'payee_id':payee['id'],'amount_paise':1000,'request_key':'two-worker-payment','fault':'none'}
        with ThreadPoolExecutor(8) as pool:accepted=list(pool.map(lambda _:api(scope+'/payments',payload),range(8)))
        check('Competing request handlers preserve one accepted identity',len({x['id'] for x in accepted})==1)
        identity=accepted[0]['id']
        wait(lambda:api(scope+'/payments/'+identity),lambda x:x['state']=='succeeded')
        ledger=overview['environment']['ledger_name'];gateway=AccountingGateway('http://127.0.0.1:4830')
        check('Competing durable workers produce one native financial effect',len(gateway.transactions(ledger,'payment:'+identity))==1)
        stop(workers[0])
        p=api(scope+'/payments',{**payload,'request_key':'remaining-worker','amount_paise':2000})
        wait(lambda:api(scope+'/payments/'+p['id']),lambda x:x['state']=='succeeded')
        check('Remaining worker continues after a worker process stops',workers[1].poll() is None and len(gateway.transactions(ledger,'payment:'+p['id']))==1)
        expected={a['id']:a['balance_paise'] for a in api(scope+'/accounts')['accounts']}
        stop(server)
        shutdown_log=(directory/'api.log').read_text()
        check('ASGI process completes lifespan shutdown before terminating',server.returncode in {0,-signal.SIGTERM} and 'Application shutdown complete.' in shutdown_log and 'Traceback' not in shutdown_log)
        server=start('avenra.asgi',['--port',str(port),'--mode','api'],'api-restarted')
        wait(lambda:api('/api/health'),lambda x:x['state']=='ready')
        observed={a['id']:a['balance_paise'] for a in api(scope+'/accounts')['accounts']}
        check('New API process reads the same accepted payment and financial balances',observed==expected and api(scope+'/payments/'+identity)['state']=='succeeded')
        check('Observed balances reflect exactly the declared two transfers',observed[payer['id']]==payer['balance_paise']-3000 and observed[payee['id']]==payee['balance_paise']+3000)
        report.update(passed=True,directory=str(directory),environment_id=env)
    except Exception as e:report.update(error=str(e),traceback=traceback.format_exc())
    finally:
        for p in reversed(processes):stop(p)
    report['application_fingerprint']=application_fingerprint();content=json.dumps(report,indent=2)+'\n'
    target=ROOT/'.local/asgi-verification.json';target.write_text(content);target.chmod(0o600)
    attempts=ROOT/'.local/verification-attempts';attempts.mkdir(exist_ok=True)
    (attempts/('asgi-'+str(time.time_ns())+'.json')).write_text(content)
    print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'report':str(target),'error':report.get('error')}))
    if not report['passed']:print(report.get('traceback',''))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
