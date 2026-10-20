#!/usr/bin/env python3
"""Observe scoped monitoring, real outages and native financial recovery.

Synthetic metric vectors below qualify only incident-state/pagination semantics.
Separate real HTTPS processes, stopped workers/gateway and native postings prove
the deployed boundaries. No external notifications or bank conformance claims.
"""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import traceback
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from avenra.app import App
from avenra.database_deployment import provision
from avenra.errors import DomainError
from avenra.lab_identity import private_read
from avenra.local_database import admin_dsn
from avenra.operations import DEFAULT,TABLES
from avenra.private_config import write_document
from avenra.production import POLICY_DEFAULT
from avenra.runtime_rehearsal import Rehearsal,wait
from avenra.sqlite_checkpoint import checkpoint
from avenra.util import application_fingerprint,uid


def case_pack_hash():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def main():
    import psycopg
    folder=ROOT/'.local/qa'/uid('operations');folder.mkdir(mode=0o700,parents=True)
    report={'passed':False,'checks':[],'directory':str(folder),'created_at':time.time(),'application_fingerprint':application_fingerprint(),'case_pack_sha256':case_pack_hash(),
            'production_ready':False,'official_conformance':False,'scope':'SQLite/PostgreSQL operational policy, immutable incidents, bounded counters, scoped cursors, real HTTPS/mTLS processes, worker/gateway outages, leader death and native financial recovery. Local synthetic qualification only.'}
    app=other=rig=restored=None
    def check(name,value):
        report['checks'].append({'name':name,'passed':bool(value)});print(json.dumps({'check':name,'passed':bool(value)}),flush=True)
        if not value:raise AssertionError(name)
    def denied(name,callback,code):
        try:callback()
        except DomainError as error:return check(name,error.code==code)
        check(name,False)
    try:
        for mode in ['sqlite','postgres']:
            directory=folder/mode;dsn=None
            if mode=='postgres':provision(directory,admin_dsn(ROOT));dsn=private_read(directory/'control.dsn').decode().strip()
            app=App(directory,control_dsn=dsn);app.bootstrap();tenant='tenant_avenra';env=app.create_environment(tenant,'Incident contract '+mode)['id'];app.provision(tenant,env)
            ops=app.runtime.operations;body={**DEFAULT,'sample_interval_seconds':1,'queue_depth':1,'unknown_age_seconds':1,'rejections':1,'for_seconds':0,'recovery_seconds':0,'request_key':uid('monitor')}
            policy=ops.publish(tenant,env,body,'operator-a')
            replayed_policy=ops.publish(tenant,env,body,'operator-a')
            with app.store.reader() as db:published=ops.bound(db,tenant,env)
            check(mode+': policy publication is signed and exact replay retains one version',replayed_policy['id']==policy['id'] and published['document']=={k:body[k] for k in DEFAULT} and policy['version']==1)
            denied(mode+': a changed threshold cannot reuse the original policy key',lambda:ops.publish(tenant,env,{**body,'queue_depth':2},'operator-a'),'IDEMPOTENCY_CONFLICT')
            denied(mode+': a different actor cannot reuse the policy key',lambda:ops.publish(tenant,env,body,'operator-b'),'IDEMPOTENCY_CONFLICT')
            denied(mode+': boolean thresholds are not accepted as integers',lambda:ops.publish(tenant,env,{**body,'queue_depth':True,'request_key':uid('invalid')},'operator-a'),'MONITOR_POLICY')
            context=app.runtime.context('trusted-unit-operator',['read','write'],operator=True)
            handler=SimpleNamespace(identity_context=context,command='GET')
            app.runtime.publish_policy(tenant,env,{**POLICY_DEFAULT,'max_inflight':2,'burst':2,'rate_per_minute':1,'request_key':uid('admit')},'operator-a')
            leases=[app.runtime.guard(handler,tenant,env,'accounts') for _ in range(2)]
            denied(mode+': active-request refusals are real admission decisions',lambda:app.runtime.guard(handler,tenant,env,'accounts'),'TENANT_CAPACITY')
            for lease in leases:app.runtime.complete(lease,200)
            denied(mode+': token-budget refusals retain their own refusal code',lambda:app.runtime.guard(handler,tenant,env,'accounts'),'REQUEST_RATE')
            app.runtime.publish_policy(tenant,env,{**POLICY_DEFAULT,'burst':1000,'rate_per_minute':60000,'max_pending_jobs':1,'request_key':uid('admit')},'operator-a');time.sleep(.02)
            handler.command='POST'
            denied(mode+': pending-work admission refuses new intents',lambda:app.runtime.guard(handler,tenant,env,'banking/upi/payments'),'QUEUE_CAPACITY')
            token=ops.claim()
            with app.store.reader() as db:bound=ops.bound(db,tenant,env)
            dependency=app.runtime.readiness(include_operations=False);sample=ops.collect(tenant,env,bound['document'],dependency);ops.apply(tenant,env,bound,sample,token)
            check(mode+': all three refusal classes are observed from committed counters',set(sample['rejections'])=={'TENANT_CAPACITY','REQUEST_RATE','QUEUE_CAPACITY'} and all(x==1 for x in sample['rejections'].values()))
            view=ops.overview(tenant,env);incident=next(x for x in view['incidents'] if x['signal']=='worker_unavailable')
            check(mode+': stopped worker creates a scoped firing incident with retained evidence',incident['state']=='firing' and view['observation_fresh'] and ops.history(tenant,env,incident['id'])['events'][0]['kind']=='firing')
            ack={'request_key':uid('ack')};acknowledged=ops.acknowledge(tenant,env,incident['id'],ack,'operator-a')
            check(mode+': acknowledgement preserves the failure and its original request identity',acknowledged['acknowledged'] and ops.acknowledge(tenant,env,incident['id'],ack,'operator-a')['replayed'] and next(x for x in ops.overview(tenant,env)['incidents'] if x['id']==incident['id'])['state']=='firing')
            denied(mode+': acknowledgement replay cannot substitute an actor',lambda:ops.acknowledge(tenant,env,incident['id'],ack,'operator-b'),'IDEMPOTENCY_CONFLICT')
            foreign=app.create_environment(tenant,'Foreign incident environment')['id']
            denied(mode+': incident acknowledgement cannot cross environments',lambda:ops.acknowledge(tenant,foreign,incident['id'],{'request_key':uid('ack')},'operator-a'),'NOT_FOUND')
            refused=False
            try:
                with app.store.transaction() as db:db.execute("UPDATE ops_alert_events SET kind='rewritten'")
            except (sqlite3.DatabaseError,psycopg.Error):refused=True
            check(mode+': retained incident events cannot be rewritten',refused)
            # Bounded per-second slots are independently inspected. This input
            # fixture is not an artificial network failure or financial effect.
            with app.store.transaction() as db:
                stale_second=int(app.runtime.clock(db))-8192
                for slot in range(4096):db.execute("INSERT INTO ops_rejections VALUES(?,?,?,'REQUEST_RATE',?,7)",(tenant,foreign,slot,stale_second))
            ops.rejection(tenant,foreign,'REQUEST_RATE')
            with app.store.reader() as db:
                rows=db.execute('SELECT epoch_second,count FROM ops_rejections WHERE tenant_id=? AND environment_id=?',(tenant,foreign)).fetchall()
            check(mode+': expired counter slots recycle without growing the bounded ring',len(rows)==4096 and sum(1 for x in rows if x['count']==1 and x['epoch_second']>stale_second)==1)
            # Explicit synthetic monitor-input vectors test long incident/event
            # pagination without inventing corresponding financial failures.
            vector=copy.deepcopy(sample);vector['fixture']='synthetic_incident_state_vector';vector['requests']=10;vector['request_errors']=10;vector['request_observation_enabled']=True
            for i in range(60):
                token=ops.claim()
                vector['observed_at']=time.time();vector['request_errors']=10;ops.apply(tenant,env,bound,vector,token)
                vector['observed_at']=time.time();vector['request_errors']=0;ops.apply(tenant,env,bound,vector,token)
            page=ops.overview(tenant,env);first_ids={x['id'] for x in page['incidents']};cursor=page['next_cursor'];check(mode+': incident history provides a bounded signed continuation',len(page['incidents'])==50 and bool(cursor))
            check(mode+': older open incidents remain visible beyond the first history page',incident['id'] in {r['id'] for r in page['open_incidents']} and incident['id'] not in first_ids)
            remaining=ops.overview(tenant,env,cursor)
            with app.store.reader() as db:total=db.execute('SELECT count(*) FROM ops_alerts WHERE tenant_id=? AND environment_id=?',(tenant,env)).fetchone()[0]
            check(mode+': incident pagination retains older records without overlap',not first_ids.intersection(x['id'] for x in remaining['incidents']) and len(first_ids)+len(remaining['incidents'])==total)
            denied(mode+': incident cursors bind the environment',lambda:ops.overview(tenant,foreign,cursor),'INCIDENT_CURSOR')
            denied(mode+': altered incident cursors fail authentication',lambda:ops.overview(tenant,env,cursor[:10]+('A' if cursor[10]!='A' else 'B')+cursor[11:]),'INCIDENT_CURSOR')
            # The same persistent alert can have many attributed review events;
            # its cursor must not become a cursor for another resource.
            for _ in range(55):ops.acknowledge(tenant,env,incident['id'],{'request_key':uid('review')},'operator-a')
            history=ops.history(tenant,env,incident['id']);older=ops.history(tenant,env,incident['id'],history['next_cursor'])
            check(mode+': incident-event history paginates immutable sequence identities',len(history['events'])==50 and history['next_cursor'] and not set(x['id'] for x in history['events']).intersection(x['id'] for x in older['events']) and len(older['events'])>=7)
            denied(mode+': event cursors cannot be substituted for incident cursors',lambda:ops.overview(tenant,env,history['next_cursor']),'INCIDENT_CURSOR')
            # Prove lease exclusion and stale-result fencing across two actual
            # store clients. Expiry is a labelled metadata fixture; real process
            # death is exercised separately below.
            other=App(directory,control_dsn=dsn)
            check(mode+': a second store client cannot claim a live monitor lease',other.runtime.operations.claim() is None)
            with app.store.transaction() as db:db.execute('UPDATE ops_monitor SET lease_until=? WHERE singleton=1',(time.time()-1,))
            replacement=other.runtime.operations.claim();check(mode+': expired ownership obtains a new fencing token',replacement and replacement!=token)
            denied(mode+': stale owner cannot append an operational result',lambda:ops.apply(tenant,env,bound,{**sample,'observed_at':time.time()},token),'MONITOR_FENCED')
            other.close();other=None;token=ops.claim()
            # Lack of enough fresh latency observations must not resolve an
            # already firing latency problem.
            vector=copy.deepcopy(sample);vector.update(fixture='synthetic_latency_state_vector',observed_at=time.time(),request_p95_ms=120001,request_observation_enabled=True)
            ops.apply(tenant,env,bound,vector,token)
            vector.update(observed_at=time.time(),request_p95_ms=None);ops.apply(tenant,env,bound,vector,token)
            check(mode+': insufficient latency samples retain an existing firing incident',any(x['signal']=='request_latency' and x['state']=='firing' for x in ops.overview(tenant,env)['incidents']))
            # Explicit virtual-clock/metric vectors exercise continuity. They
            # do not claim that a real outage or financial effect took place.
            timing_env=app.create_environment(tenant,'Synthetic monitoring dwell vectors')['id']
            ops.publish(tenant,timing_env,{**DEFAULT,'for_seconds':10,'recovery_seconds':10,'request_key':uid('timing_policy')},'operator-a')
            with app.store.reader() as db:timing_policy=ops.bound(db,tenant,timing_env)
            original_clock=app.runtime.clock;tick=[1000.0];app.runtime.clock=lambda db:tick[0]
            timing=copy.deepcopy(sample);timing.update(fixture='synthetic_pending_unknown_dwell',worker_available=True,accounting_available=True,
                pending_jobs=0,oldest_due_seconds=0,rejections={},request_errors=0,request_p95_ms=120001,
                failed_by_system={k:0 for k in sample['failed_by_system']},unknown_by_layer={k:{'count':0,'oldest_seconds':0} for k in sample['unknown_by_layer']})
            def observe_at(stamp,latency):
                tick[0]=stamp;timing.update(observed_at=stamp,request_p95_ms=latency);owner=ops.claim();ops.apply(tenant,timing_env,timing_policy,timing,owner)
                with app.store.reader() as db:return dict(db.execute("SELECT * FROM ops_alerts WHERE tenant_id=? AND environment_id=? AND signal='request_latency' ORDER BY sequence DESC LIMIT 1",(tenant,timing_env)).fetchone())
            try:
                initial=observe_at(1000,120001);observe_at(1004,None);resumed=observe_at(1011,120001)
                check(mode+': unknown telemetry interrupts pending dwell without changing incident identity',initial['state']=='pending' and resumed['id']==initial['id'] and resumed['state']=='pending' and resumed['condition_since']==1011)
                before=observe_at(1020,120001);firing=observe_at(1021,120001)
                check(mode+': firing requires the entire confirmed post-gap dwell',before['state']=='pending' and firing['state']=='firing')
                unknown=observe_at(1022,None)
                check(mode+': unknown telemetry cannot resolve the continuously identified firing incident',unknown['id']==initial['id'] and unknown['state']=='firing')
                recovering=observe_at(1023,1);interrupted=observe_at(1024,None)
                check(mode+': unknown telemetry interrupts recovery rather than proving health',recovering['state']=='recovering' and interrupted['state']=='firing' and interrupted['recovery_since'] is None)
                observe_at(1025,1);before=observe_at(1034,1);resolved=observe_at(1035,1)
                check(mode+': resolution requires the full new healthy dwell after unknown telemetry',before['state']=='recovering' and resolved['state']=='resolved' and resolved['id']==initial['id'])
            finally:app.runtime.clock=original_clock
            app.start_worker()
            wait(lambda:ops.history(tenant,env,incident['id']),lambda x:any(r['kind']=='resolved' and r['payload']['metrics']['worker_available'] for r in x['events']))
            check(mode+': an actual worker restores health and resolves its original incident',not any(x['id']==incident['id'] for x in ops.overview(tenant,env)['open_incidents']))
            # A live process is not a healthy financial worker. Exercise a real
            # worker thread with a labelled synthetic receipt-store exception.
            original_claim=app.store.claim
            def unavailable(*args,**kwargs):raise OSError('Synthetic receipt-store claim outage')
            app.store.claim=unavailable
            wait(lambda:app.worker_available,lambda x:not x)
            wait(app.runtime.readiness,lambda x:any(c['component']=='worker' and not c['ready'] for c in x['checks']))
            check(mode+': receipt-store claim failures keep the thread alive but withdraw worker readiness',app.worker.is_alive() and not app.worker_available)
            accounts=app.accounts(tenant,env);payer=next(x for x in accounts if x['role']=='payer');payee=next(x for x in accounts if x['role']=='payee')
            pending=app.submit_payment(tenant,env,{'payer_id':payer['id'],'payee_id':payee['id'],'amount_paise':17,'request_key':uid('claim_recovery'),'fault':'none'})
            ledger=app.environment(tenant,env)['ledger_name']
            check(mode+': unclaimed accepted work has no invented financial effect',app.payment(tenant,env,pending['id'])['state']=='pending' and not app.accounting.transactions(ledger,'payment:'+pending['id']))
            wait(lambda:ops.overview(tenant,env),lambda x:any(r['signal']=='worker_unavailable' and r['state']=='firing' for r in x['open_incidents']))
            app.store.claim=original_claim
            wait(lambda:app.payment(tenant,env,pending['id']),lambda x:x['state']=='succeeded')
            wait(app.runtime.readiness,lambda x:any(c['component']=='worker' and c['ready'] for c in x['checks']))
            after={x['id']:x['balance_paise'] for x in app.accounts(tenant,env)}
            check(mode+': the same worker resumes the original accepted effect exactly once after store recovery',len(app.accounting.transactions(ledger,'payment:'+pending['id']))==1 and after[payer['id']]==payer['balance_paise']-17 and after[payee['id']]==payee['balance_paise']+17)
            wait(lambda:ops.overview(tenant,env),lambda x:not any(r['signal']=='worker_unavailable' for r in x['open_incidents']))
            scheduler=app.fidelity.lifecycle_worker
            def dead_thread(*args,**kwargs):raise SystemExit('Synthetic fatal worker-thread fixture')
            app.store.claim=dead_thread;wait(lambda:app.worker.is_alive(),lambda x:not x)
            wait(app.runtime.readiness,lambda x:any(c['component']=='worker' and not c['ready'] for c in x['checks']))
            check(mode+': a dead worker thread cannot retain a healthy process heartbeat',not app.worker_available and not app.worker.is_alive())
            app.store.claim=original_claim;app.start_worker();wait(app.runtime.readiness,lambda x:any(c['component']=='worker' and c['ready'] for c in x['checks']))
            check(mode+': an explicit thread restart reuses one lifecycle scheduler',app.worker.is_alive() and scheduler is app.fidelity.lifecycle_worker)
            denied(mode+': a second live worker thread cannot be started in the same application',app.start_worker,'WORKER_ACTIVE')
            app.close();app=None
            if mode=='sqlite':
                with sqlite3.connect(directory/'avenra.db') as original:original_counts={t:original.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in TABLES}
                saved=folder/'sqlite-restored';saved.mkdir(mode=0o700);checkpoint(directory/'avenra.db',saved/'avenra.db');shutil.copyfile(directory/'evidence.key',saved/'evidence.key');(saved/'evidence.key').chmod(0o600)
                restored=App(saved)
                with restored.store.reader() as db:restored_counts={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in TABLES}
                check('SQLite: inactive restored incidents retain policy signatures, original identities and history',restored.runtime.operations.overview(tenant,env)['policy']['id']==bound['id'] and restored_counts==original_counts and len(restored.runtime.operations.history(tenant,env,incident['id'])['events'])==50)
                restored.close();restored=None

        rig=Rehearsal(ROOT,folder/'https');api0=rig.start_api(0)
        def monitor():
            with psycopg.connect(rig.dsn,autocommit=True) as db:return db.execute('SELECT owner,token FROM ops_monitor WHERE singleton=1').fetchone()
        first_owner=wait(monitor);rig.start_api(1);rig.start_worker();rig.start_worker();wait(lambda:rig.call('/health/ready'),lambda r:r['status']==200)
        def current(tenant='tenant_avenra',node=0):
            result=rig.endpoint('/banking/runtime/operations',tenant,node=node)
            require_ok=result['status']==200
            if not require_ok:raise AssertionError('Operational HTTP observation returned '+str(result['status']))
            return result['body']
        check('HTTPS: both real API nodes expose fresh scoped operational observations',current()['observation_fresh'] and current(node=1)['observation_fresh'])
        check('HTTPS: another workspace key cannot read operational incidents',rig.call(rig.scope('tenant_integration')+'/banking/runtime/operations',tenant='tenant_avenra')['status']==403)
        check('HTTPS: observer credentials cannot publish monitoring policies',rig.endpoint('/banking/runtime/operations/policies',body={**DEFAULT,'request_key':uid('forbidden')},role='observer')['status']==403)
        # Preserve a real unknown native effect while checking review semantics.
        value=rig.fixtures['tenant_avenra']
        body,accepted=rig.submit(amount=123,fault='lost_credit_response');check('HTTPS: a real participant intent is accepted for physical response-loss recovery',accepted['status']==202)
        identity=accepted['body']['id'];authorized=rig.authorize('tenant_avenra',identity);check('HTTPS: original participant intent is authorized',authorized['status']==202)
        done=wait(lambda:rig.endpoint('/banking/upi/payments/'+identity),lambda r:r['status']==200 and r['body']['state']=='succeeded' and r['body']['evidence'],timeout=45)
        check('HTTPS: native response-loss recovery retains valid financial evidence',done['body']['evidence']['valid'])
        for child in list(rig.workers):rig.stop(child)
        outage=wait(current,lambda x:any(r['signal']=='worker_unavailable' and r['state']=='firing' for r in x['incidents']),timeout=25)
        failure=next(x for x in outage['incidents'] if x['signal']=='worker_unavailable' and x['state']=='firing')
        check('HTTPS: stopped real worker processes produce a retained firing incident',rig.call('/health/ready')['status']==503 and failure['title']=='Worker unavailable')
        ack_key=uid('http_ack');ack=rig.endpoint('/banking/runtime/operations/incidents/'+failure['id']+'/acknowledge',body={'request_key':ack_key},role='administrator')
        check('HTTPS: attributed acknowledgement does not restore a missing worker',ack['status']==200 and any(r['id']==failure['id'] and r['state']=='firing' and r['acknowledged_by'] for r in current()['incidents']))
        check('HTTPS: a service operator cannot acknowledge using admin-only controls',rig.endpoint('/banking/runtime/operations/incidents/'+failure['id']+'/acknowledge',body={'request_key':uid('denied')})['status']==403)
        rig.start_worker();wait(current,lambda x:any(r['id']==failure['id'] and r['state']=='resolved' for r in x['incidents']))
        check('HTTPS: a real replacement worker resolves the original incident',rig.call('/health/ready')['status']==200)
        rig.stop(rig.gateway)
        accounting=wait(current,lambda x:any(r['signal']=='accounting_unavailable' and r['state']=='firing' for r in x['incidents']))
        accounting_id=next(r['id'] for r in accounting['incidents'] if r['signal']=='accounting_unavailable' and r['state']=='firing')
        check('HTTPS: an actual private accounting gateway outage is named and remains unready',rig.call('/health/ready')['status']==503 and accounting['observation']['metrics']['accounting_available'] is False)
        second_body,second_accepted=rig.submit(amount=127);check('HTTPS: an accepted intent remains durable while its original accounting connection is unavailable',second_accepted['status']==202 and rig.authorize('tenant_avenra',second_accepted['body']['id'])['status']==202)
        second_id=second_accepted['body']['id']
        unknown=wait(lambda:rig.endpoint('/banking/upi/payments/'+second_id),lambda r:r['status']==200 and r['body']['state']=='unknown')
        unknown_view=wait(current,lambda x:any(r['signal']=='outcome_unknown' and r['state']=='firing' for r in x['incidents']))
        unknown_incident=next(r for r in unknown_view['incidents'] if r['signal']=='outcome_unknown' and r['state']=='firing')
        check('HTTPS: real unresolved bank dispatch produces an unconfirmed-outcome incident',unknown['body']['operation_id'] and unknown_incident['latest_metrics']['unknown_by_layer']['bank_operations']['count']>=1)
        ack=rig.endpoint('/banking/runtime/operations/incidents/'+unknown_incident['id']+'/acknowledge',body={'request_key':uid('ack_unknown')},role='administrator')
        check('HTTPS: reviewing uncertainty does not mark its payment succeeded',ack['status']==200 and rig.endpoint('/banking/upi/payments/'+second_id)['body']['state']=='unknown')
        rig.gateway=rig.start('avenra.service_gateway',['--data',str(rig.data),'--config',str(rig.data/'gateway.json')],'gateway-recovered')
        wait(current,lambda x:any(r['id']==accounting_id and r['state']=='resolved' for r in x['incidents']))
        check('HTTPS: restoring the original gateway resolves its original incident',rig.call('/health/ready')['status']==200)
        confirmed=wait(lambda:rig.endpoint('/banking/upi/payments/'+second_id),lambda r:r['status']==200 and r['body']['state']=='succeeded' and r['body']['evidence'],timeout=45)
        wait(current,lambda x:any(r['id']==unknown_incident['id'] and r['state']=='resolved' for r in x['incidents']))
        check('HTTPS: original uncertain intent and operation recover under the same identity',confirmed['body']['operation_id']==unknown['body']['operation_id'] and confirmed['body']['evidence']['valid'])
        old=monitor();rig.stop(api0,kill=True)
        replacement=wait(monitor,lambda r:r and r[0]!=old[0] and r[1]!=old[1],timeout=25)
        recovered=wait(lambda:current(node=1),lambda x:x['observation_fresh'],timeout=25)
        check('HTTPS: another actual node takes over after the monitor owner process is killed',replacement[0]!=first_owner[0] and recovered['observation_fresh'])
        check('HTTPS: leader recovery preserves the settled participant identity and evidence',rig.endpoint('/banking/upi/payments/'+identity,node=1)['body']['evidence']['valid'])
        check('Qualification source and case pack remain fixed',report['application_fingerprint']==application_fingerprint() and report['case_pack_sha256']==case_pack_hash())
        report['passed']=True
    except Exception as error:report.update(error=str(error),traceback=traceback.format_exc())
    finally:
        if rig:rig.close()
        if other:other.close()
        if app:app.close()
        if restored:restored.close()
        report['completed_at']=time.time();write_document(folder/'verification.json',report);write_document(ROOT/'.local/operations-verification.json',report)
        print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'report':str(ROOT/'.local/operations-verification.json'),'error':report.get('error')}),flush=True)
        if not report['passed']:print(report.get('traceback',''),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
