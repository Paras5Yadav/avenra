#!/usr/bin/env python3
"""Open-loop mixed-tenant load, real worker loss and independent native oracle.

The reference profile is a local engineering workload, not an official UPI TPS
claim. Smoke mode is deliberately ineligible for the reference qualification.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
import threading
import time
import traceback

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from avenra.gateways import AccountingGateway
from avenra.lab_identity import private_read
from avenra.private_config import write_document
from avenra.production import POLICY_DEFAULT
from avenra.runtime_rehearsal import Rehearsal,wait
from avenra.util import application_fingerprint,digest,uid
from scripts.qualification_storage import QualificationStorage, StorageRefused, record_refusal, harness_case_pack_hash as guarded_case_pack_hash

REFERENCE={'id':'avenra-local-capacity/2','tenants':2,'api_nodes':2,'workers':2,'client_threads':32,'max_generator_inflight':96,
           'amount_paise':25,'hot_pending_limit':16,'normal_pending_limit':1000,'drain_seconds':120,
           'max_wall_monotonic_drift_seconds':5,
           'admission_p95_ms':2000,'completion_p95_ms':15000,'baseline_error_fraction':.01,
           'phases':[{'name':'steady','seconds':60,'rates':[1,1]}, {'name':'noisy_neighbour','seconds':60,'rates':[12,1]},
                     {'name':'workers_killed','seconds':20,'rates':[2,1]}, {'name':'recovery','seconds':40,'rates':[1,1]}]}
TENANTS=['tenant_avenra','tenant_integration']
REJECTION_CODES={'TENANT_CAPACITY','REQUEST_RATE','QUEUE_CAPACITY','SERVER_CAPACITY','CAPACITY'}


def case_pack_hash():return guarded_case_pack_hash(ROOT, 'verify_capacity.py')


def percentile(values,fraction=.95):
    values=sorted(values);return values[max(0,math.ceil(len(values)*fraction)-1)] if values else None


def run(storage,args):
    import psycopg
    folder=ROOT/'.local/qa'/uid('capacity');folder.mkdir(mode=0o700,parents=True)
    profile=json.loads(json.dumps(REFERENCE))
    if args.smoke:
        for phase,seconds in zip(profile['phases'],[5,5,3,7]):phase['seconds']=seconds
    report={'passed':False,'reference_profile_passed':False,'checks':[],'created_at':time.time(),'directory':str(folder),'application_fingerprint':application_fingerprint(),
            'case_pack_sha256':case_pack_hash(),'reference_profile_sha256':digest(REFERENCE),'profile':profile,'mode':'smoke' if args.smoke else 'reference',
            'machine':{'system':platform.system(),'machine':platform.machine(),'python':platform.python_version()},'production_ready':False,'official_conformance':False,
            'scope':'Actual local open-loop HTTPS/mTLS participant traffic, competing tenants, explicit overload, killed workers, original-identity recovery and independent native balances/journal comparison. Exact observed host/profile only; not institutional capacity or fleet availability qualification.'}
    rig=None;records=[];requests=[];futures=[];lock=threading.Lock();stopping=threading.Event();poller=None
    generator_misses=[];poll_errors=[];phase_name='setup';known_unknown_incidents=False;stopped_worker_incident=False
    clock_start={'wall':time.time(),'monotonic':time.monotonic()};clock_observations={'start':clock_start,'maximum_absolute_drift_seconds':0,'discontinuities':[]}
    def continuous_clock():
        sample={'wall':time.time(),'monotonic':time.monotonic()}
        drift=(sample['wall']-clock_start['wall'])-(sample['monotonic']-clock_start['monotonic'])
        clock_observations['maximum_absolute_drift_seconds']=max(clock_observations['maximum_absolute_drift_seconds'],abs(drift))
        if abs(drift)>REFERENCE['max_wall_monotonic_drift_seconds']:
            clock_observations['discontinuities'].append({**sample,'drift_seconds':drift,'phase':phase_name})
            raise RuntimeError('Qualification clock discontinuity: host suspension or wall-clock adjustment invalidates the continuous reference workload; retain this failed attempt.')
    def check(name,value):
        report['checks'].append({'name':name,'passed':bool(value)});print(json.dumps({'check':name,'passed':bool(value)}),flush=True)
        if not value:raise AssertionError(name)
    def record_request(record,kind,result):
        error=result['body'].get('error') if isinstance(result.get('body'),dict) else None;code=error.get('code') if isinstance(error,dict) else None
        with lock:requests.append({'tenant':record['tenant'],'phase':record['phase'],'kind':kind,'status':result['status'],'code':code,'milliseconds':result['milliseconds']})
        return code
    def post(record,retry=False):
        result=rig.endpoint('/banking/upi/payments',record['tenant'],body=record['body'],node=record['node']);code=record_request(record,'submit_retry' if retry else 'submit',result)
        with lock:
            record['busy']=False
            if result['status'] in {200,202}:
                identity=result['body']['id']
                if record.get('id') and record['id']!=identity:record['error']='REPLAY_IDENTITY_CHANGED'
                record['id']=identity;record.setdefault('admission_ms',(time.monotonic()-record['scheduled_at'])*1000)
            elif result['status'] in {429,503} and code in REJECTION_CODES:
                if not record.get('uncertain'):record['rejected']=code
            elif result['status'] in {0,504}:record['uncertain']=True
            else:record['error']='SUBMISSION_'+str(result['status'])+':'+str(code)
        if record.get('id'):authorize(record)
    def authorize(record):
        result=rig.authorize(record['tenant'],record['id'],node=record['node']);record_request(record,'authorize',result)
        with lock:
            if result['status'] in {200,202}:record['authorized']=True
            elif result['status'] not in {0,409,429,503,504}:record['error']='AUTHORIZATION_'+str(result['status'])
    def poll():
        while not stopping.is_set():
            with lock:pending=[r for r in records if not r.get('complete') and not r.get('rejected') and not r.get('error') and not r.get('busy')]
            for record in pending:
                if stopping.is_set():break
                try:
                    if not record.get('id'):
                        if record.get('uncertain') and time.monotonic()-record.get('last_retry',0)>2:
                            record['last_retry']=time.monotonic();post(record,True)
                        continue
                    if not record.get('authorized'):authorize(record)
                    result=rig.endpoint('/banking/upi/payments/'+record['id'],record['tenant'],node=record['node']);record_request(record,'status',result)
                    if result['status']!=200:
                        if result['status'] not in {0,429,503,504}:record['error']='STATUS_'+str(result['status'])
                        continue
                    value=result['body'];record['last_state']=value['state']
                    if value['state']=='succeeded' and value['evidence']:
                        with lock:
                            record.update(complete=True,completion_ms=(time.monotonic()-record['scheduled_at'])*1000,operation_id=value['operation_id'],
                                          legs=[{k:leg[k] for k in ['id','kind','source','destination','amount_paise','ledger_id','state']} for leg in value['operation']['legs']],evidence_valid=value['evidence']['valid'])
                    elif value['state'] in {'declined','expired','reversed','cancelled'}:record['error']='UNEXPECTED_TERMINAL_'+value['state']
                    elif value['state']=='unknown' and time.monotonic()-record.get('last_recovery',0)>2:
                        record['last_recovery']=time.monotonic();recovered=rig.endpoint('/banking/upi/payments/'+record['id']+'/reconcile',record['tenant'],body={},node=record['node']);record_request(record,'original_recovery',recovered)
                        if recovered['status'] not in {0,202,409,429,503,504}:record['error']='RECOVERY_'+str(recovered['status'])
                except Exception as error:
                    with lock:poll_errors.append(type(error).__name__+':'+str(error))
            stopping.wait(.3)
    try:
        rig=Rehearsal(ROOT,folder/'runtime');rig.start_all();native=AccountingGateway('http://127.0.0.1:4830')
        baseline={};ledgers={};initial_references={}
        for tenant in TENANTS:
            baseline[tenant]={a['id']:a['balance_paise'] for a in rig.endpoint('/accounts',tenant)['body']['accounts']}
            with psycopg.connect(rig.dsn,autocommit=True) as db:ledgers[tenant]=db.execute('SELECT ledger_name FROM environments WHERE id=%s AND tenant_id=%s',(rig.fixtures[tenant]['environment_id'],tenant)).fetchone()[0]
            initial_references[tenant]={x.get('reference') for x in native.transactions(ledgers[tenant],all_pages=True)}
        check('Both HTTPS API nodes and isolated tenant fixtures are ready',all(rig.call('/health/ready',node=i)['status']==200 for i in range(2)))
        poller=threading.Thread(target=poll,name='capacity-observer',daemon=True);poller.start()
        counter=0;executor=ThreadPoolExecutor(REFERENCE['client_threads'])
        try:
            for phase in profile['phases']:
                phase_name=phase['name']
                storage.checkpoint('capacity_'+phase_name)
                continuous_clock()
                if phase_name=='noisy_neighbour':
                    result=rig.endpoint('/banking/runtime/policies',body={**POLICY_DEFAULT,'burst':1000,'rate_per_minute':60000,'max_inflight':16,'max_pending_jobs':REFERENCE['hot_pending_limit'],'request_key':uid('pressure_policy')},role='administrator')
                    check('Only the aggressor receives the explicit low pending-work limit',result['status']==202)
                if phase_name=='workers_killed':
                    for worker in rig.workers:rig.stop(worker,kill=True)
                    check('Both actual financial worker processes are killed while the API remains available',all(w.poll() is not None for w in rig.workers) and rig.call('/health/live')['status']==200)
                if phase_name=='recovery':
                    for tenant in TENANTS:
                        result=rig.endpoint('/banking/runtime/policies',tenant,body={**POLICY_DEFAULT,'burst':1000,'rate_per_minute':60000,'max_inflight':16,'max_pending_jobs':1000,'request_key':uid('recovery_policy')},role='administrator')
                        check('Recovery restores bounded admission for '+tenant,result['status']==202)
                    rig.start_worker();rig.start_worker()
                print(json.dumps({'phase':phase_name,'seconds':phase['seconds'],'rates':phase['rates']}),flush=True)
                start=time.monotonic();end=start+phase['seconds'];due=[start,start];last_progress=start
                while time.monotonic()<end:
                    continuous_clock()
                    now=time.monotonic()
                    for index,tenant in enumerate(TENANTS):
                        if now<due[index]:continue
                        scheduled=due[index];due[index]+=1/phase['rates'][index]
                        if now-scheduled>1:
                            generator_misses.append({'tenant':tenant,'phase':phase_name,'late_seconds':now-scheduled});due[index]=now+1/phase['rates'][index]
                        futures[:]=[x for x in futures if not x.done()]
                        if len(futures)>=REFERENCE['max_generator_inflight']:
                            generator_misses.append({'tenant':tenant,'phase':phase_name,'reason':'client_inflight_bound'});continue
                        counter+=1;value=rig.fixtures[tenant];key=uid('capacity_request');fault='lost_credit_response' if phase_name in {'noisy_neighbour','recovery'} and counter%23==0 else 'none'
                        body={'payer_id':value['accounts'][0],'payee_id':value['accounts'][1],'device_id':value['devices'][0],'amount_paise':REFERENCE['amount_paise'],'request_key':key,'financial_fault':fault,'expires_in_seconds':900}
                        record={'tenant':tenant,'phase':phase_name,'body':body,'node':counter%2,'scheduled_at':scheduled,'busy':True}
                        with lock:records.append(record)
                        futures.append(executor.submit(post,record))
                    if now-last_progress>=10:
                        with lock:progress={'phase':phase_name,'offered':len(records),'accepted':sum(bool(r.get('id')) for r in records),'complete':sum(bool(r.get('complete')) for r in records),'refused':sum(bool(r.get('rejected')) for r in records)}
                        print(json.dumps(progress),flush=True);last_progress=now
                    time.sleep(.01)
                if phase_name=='workers_killed':
                    current=rig.endpoint('/banking/runtime/operations')['body'];stopped_worker_incident=any(r['signal']=='worker_unavailable' and r['state']=='firing' for r in current['open_incidents'])
                    if not args.smoke:check('Actual worker loss is retained as an incident while the monitoring API stays available',stopped_worker_incident and rig.call('/health/ready')['status']==503)
        finally:executor.shutdown(wait=True)
        end=time.monotonic()+REFERENCE['drain_seconds']
        while time.monotonic()<end:
            continuous_clock()
            with lock:pending=[r for r in records if not (r.get('complete') or r.get('rejected') or r.get('error'))]
            if not pending:break
            time.sleep(.2)
        stopping.set();poller.join(timeout=20);check('The observer drains without hiding an unconfirmed transport request',not poller.is_alive())
        continuous_clock();check('The declared continuous workload has no host/wall-clock discontinuity',not clock_observations['discontinuities'])
        check('All offered identities have an observed completion or a definitive admission refusal',not poll_errors and all(r.get('complete') or r.get('rejected') for r in records))
        accepted=[r for r in records if r.get('id')];refused=[r for r in records if r.get('rejected')]
        check('All accepted participant flows finish with valid financial evidence',accepted and all(r.get('complete') and r['evidence_valid'] for r in accepted))
        check('Confirmed overload refusals occur for the deliberately constrained aggressor',any(r['tenant']==TENANTS[0] and r['phase']=='noisy_neighbour' for r in refused))
        check('Victim and steady traffic have no client-side generator backlog',not any(x['tenant']==TENANTS[1] or x['phase']=='steady' for x in generator_misses))
        # Source admission keys and native journal references are independently
        # compared; control-store completion alone is insufficient evidence.
        for tenant in TENANTS:
            selected=[r for r in accepted if r['tenant']==tenant]
            with psycopg.connect(rig.dsn,autocommit=True) as db:actual={row[0]:row[1] for row in db.execute('SELECT request_key,id FROM upi_payments WHERE tenant_id=%s AND environment_id=%s',(tenant,rig.fixtures[tenant]['environment_id']))}
            check('Every persisted request matches one original client identity: '+tenant,actual=={r['body']['request_key']:r['id'] for r in selected})
            expected=dict(baseline[tenant]);amount=sum(r['body']['amount_paise'] for r in selected);value=rig.fixtures[tenant]
            expected[value['accounts'][0]]-=amount;expected[value['accounts'][1]]+=amount
            observed={a['id']:a['balance_paise'] for a in rig.endpoint('/accounts',tenant)['body']['accounts']}
            check('Authoritative native account balances equal the independent client book: '+tenant,observed==expected)
            transactions=native.transactions(ledgers[tenant],all_pages=True);by_reference={}
            for tx in transactions:by_reference.setdefault(tx.get('reference'),[]).append(tx)
            legs=[leg for r in selected for leg in r['legs'] if leg['state']=='confirmed'];expected_refs={x['id'] for x in legs}
            check('The native journal contains exactly one posting per original financial leg: '+tenant,
                  len(legs)==2*len(selected) and set(by_reference)-initial_references[tenant]==expected_refs and all(len(by_reference.get(leg['id'],[]))==1 and str(by_reference[leg['id']][0]['id'])==str(leg['ledger_id']) for leg in legs))
            for record in selected[:3]:
                replay=rig.endpoint('/banking/upi/payments',tenant,body=record['body'],node=1-record['node'])
                check('Cross-node replay retains original accepted identity: '+tenant,replay['status']==202 and replay['body']['id']==record['id'])
        cohorts={}
        for name,selection in [('steady',[r for r in records if r['phase']=='steady']),('victim_under_pressure',[r for r in records if r['phase']=='noisy_neighbour' and r['tenant']==TENANTS[1]])]:
            successful=[r for r in selection if r.get('complete')];admission=percentile([r['admission_ms'] for r in successful]);completion=percentile([r['completion_ms'] for r in successful]);error_fraction=1-len(successful)/len(selection) if selection else 1
            cohorts[name]={'offered':len(selection),'succeeded':len(successful),'admission_p95_ms':admission,'completion_p95_ms':completion,'unsuccessful_fraction':error_fraction}
            if not args.smoke:check('Declared local workload budgets hold for '+name,admission is not None and admission<=REFERENCE['admission_p95_ms'] and completion<=REFERENCE['completion_p95_ms'] and error_fraction<=REFERENCE['baseline_error_fraction'])
        check('Capacity qualification source and case pack stayed fixed',report['application_fingerprint']==application_fingerprint() and report['case_pack_sha256']==case_pack_hash())
        report.update(passed=True,reference_profile_passed=not args.smoke,cohorts=cohorts,offered=len(records),accepted=len(accepted),refused=len(refused),generator_misses=generator_misses,
                      request_observations=requests,records=[{k:v for k,v in r.items() if k!='busy'} for r in records],worker_outage_incident_observed=stopped_worker_incident)
    except Exception as error:report.update(error=str(error),traceback=traceback.format_exc(),records=records,poll_errors=poll_errors,request_observations=requests,generator_misses=generator_misses)
    finally:
        stopping.set()
        if poller:poller.join(timeout=20)
        if rig:rig.close()
        report['clock_observations']=clock_observations
        report['storage_admission']=storage.snapshot()
        report['completed_at']=time.time();full=folder/'verification.json';write_document(full,report)
        # Per-request observations can exceed the deliberately small private
        # configuration reader budget. Preserve the complete immutable attempt
        # separately and publish only bounded qualification metadata here.
        target=ROOT/'.local'/('capacity-smoke-verification.json' if args.smoke else 'capacity-verification.json')
        if target.exists() and target.stat().st_size>65536:
            private_read(target,maximum=32*1024**2)
            target.rename(folder/'previous-latest-report.json')
        summary={k:v for k,v in report.items() if k not in {'records','request_observations','generator_misses','poll_errors'}}
        summary['details']={'path':str(full),'sha256':hashlib.sha256(full.read_bytes()).hexdigest()}
        summary['diagnostic_counts']={'records':len(report.get('records',[])),'request_observations':len(report.get('request_observations',[])),
                                     'generator_misses':len(report.get('generator_misses',[])),'poll_errors':len(report.get('poll_errors',[]))}
        write_document(target,summary)
        print(json.dumps({'passed':report['passed'],'reference_profile_passed':report['reference_profile_passed'],'checks':len(report['checks']),'report':str(target),'error':report.get('error')}),flush=True)
        if not report['passed']:print(report.get('traceback',''),flush=True)
    return 0 if report['passed'] else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--smoke',action='store_true');args=parser.parse_args()
    try:
        with QualificationStorage(ROOT,'capacity') as storage:
            return run(storage,args)
    except StorageRefused as error:
        name='capacity-smoke-verification.json' if args.smoke else 'capacity-verification.json'
        return record_refusal(ROOT,name,error,application_fingerprint(),case_pack_hash())


if __name__=='__main__':raise SystemExit(main())
