#!/usr/bin/env python3
"""Observe POC cases through the existing HTTP/run/evidence workflow."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback
from urllib.error import HTTPError,URLError
from urllib.request import ProxyHandler,Request,build_opener

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from avenra.app import App
from avenra.poc_scenarios import CATALOG,AREAS
from avenra.private_config import write_document
from avenra.synthetic_lab import stop
from avenra.util import application_fingerprint,uid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',action='append',choices=[c['key'] for c in CATALOG])
    args=parser.parse_args()
    cases=args.case or [c['key'] for c in CATALOG]
    directory=ROOT/'.local/qa'/uid('poc');directory.mkdir(parents=True,mode=0o700)
    report={'passed':False,'checks':[],'directory':str(directory),'application_fingerprint':application_fingerprint(),
            'case_pack_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'cases':cases,
            'scope':'Actual same-app HTTP scenarios, native synthetic-money accounting and configured ARM64/KVM worker. Local POC acceptance only.',
            'production_ready':False,'official_acceptance_received':False,'independent_assessment_completed':False}
    process=app=None
    def check(name,passed,detail=None):
        report['checks'].append({'name':name,'passed':bool(passed),'detail':detail})
        print(json.dumps({'check':name,'passed':bool(passed)}),flush=True)
        if not passed:raise AssertionError(name)
    try:
        app=App(directory);app.bootstrap()
        with app.store.reader() as db:
            env=db.execute("SELECT id FROM environments WHERE tenant_id='tenant_avenra' AND purpose='interactive'").fetchone()[0]
        tenant='tenant_avenra';app.provision(tenant,env);app.close();app=None
        credentials=json.loads((directory/'credentials.json').read_text())
        with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        log=directory/'http.log'
        with log.open('ab') as stream:
            log.chmod(0o600)
            process=subprocess.Popen([sys.executable,'-B','-m','avenra.asgi','--data',str(directory),'--port',str(port)],
                    cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT)
        opener=build_opener(ProxyHandler({}));origin='http://127.0.0.1:'+str(port)
        scope='/api/tenants/'+tenant+'/environments/'+env
        def api(path,body=None,expected=200,authenticated=True):
            headers={'Content-Type':'application/json'}
            if authenticated:headers['Authorization']='Bearer '+credentials['api_key']
            request=Request(origin+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            try:
                with opener.open(request,timeout=15) as response:status,value=response.status,json.load(response)
            except HTTPError as error:
                with error:status,value=error.code,json.load(error)
            if status!=expected:raise AssertionError('HTTP '+str(status)+' at '+path+': '+str(value)[:200])
            return value
        deadline=time.monotonic()+20
        while True:
            try:
                if api('/api/health')['state']=='ready':break
            except (OSError,URLError):pass
            if process.poll() is not None or time.monotonic()>deadline:raise TimeoutError('POC HTTP runtime did not become ready.')
            time.sleep(.1)
        check('Existing application starts through the production HTTP stack',process.poll() is None)
        overview=api(scope+'/overview');before={a['id']:a['balance_paise'] for a in overview['accounts']}
        check('Full original POC scope remains visible before running cases',overview['poc']['total_areas']==len(AREAS) and not overview['poc']['poc_complete'])
        runs={}
        for case in cases:
            body={'scenario_key':case,'request_key':uid('poc_request')}
            run=api(scope+'/runs',body,202);identity=run['id']
            check(case+' request replay preserves its original run and fixture',api(scope+'/runs',body,202)['id']==identity)
            other=next(c['key'] for c in CATALOG if c['key']!=case)
            conflict=api(scope+'/runs',{**body,'scenario_key':other},409)
            check(case+' changed payload is refused',conflict['error']['code']=='IDEMPOTENCY_CONFLICT')
            deadline=time.monotonic()+180
            while True:
                result=api(scope+'/runs/'+identity)
                if result['state'] in {'passed','failed'}:break
                if process.poll() is not None or time.monotonic()>deadline:raise TimeoutError(case+' did not reach a retained outcome.')
                time.sleep(.2)
            runs[case]={'run_id':identity,'state':result['state'],'steps':len(result['steps']),'error':result.get('error')}
            report['runs']=runs
            check(case+' executes successfully in the existing durable worker',result['state']=='passed',runs[case])
            evidence=api(scope+'/runs/'+identity+'/evidence')
            check(case+' retains locally authenticated current-build evidence',evidence['valid'] and evidence['manifest']['state']=='passed' and
                  any(s['key']=='98-source' and s['passed'] and s['observed']['fingerprint']==report['application_fingerprint'] for s in evidence['manifest']['steps']))
            api(scope+'/runs/'+identity,expected=401,authenticated=False)
            foreign=api('/api/tenants/tenant_integration/environments/'+env+'/runs/'+identity,expected=403)
            check(case+' evidence is protected by workspace authentication',foreign['error']['code']=='FORBIDDEN')
        overview=api(scope+'/overview')
        check('POC runs leave the original interactive balances unchanged',before=={a['id']:a['balance_paise'] for a in overview['accounts']})
        expected_areas=sum(case in cases for _,_,case,_ in AREAS)
        check('Coverage counts only the demonstrated current-build areas',overview['poc']['passed_areas']==expected_areas,
              {'passed_areas':overview['poc']['passed_areas'],'total_areas':overview['poc']['total_areas']})
        check('POC evidence cannot manufacture production or external approval',not overview['poc']['production_ready'] and not overview['poc']['official_acceptance_received'] and not overview['poc']['independent_assessment_completed'])
        check('Qualification retained one application source build',application_fingerprint()==report['application_fingerprint'])
        report.update(passed=True,coverage=overview['poc'])
    except Exception as error:report.update(error=type(error).__name__+': '+str(error),traceback=traceback.format_exc())
    finally:
        if process:stop(process)
        if app:app.close()
        write_document(directory/'verification.json',report)
        write_document(ROOT/'.local/poc-verification.json',report)
        print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'directory':str(directory),'error':report.get('error')}),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
