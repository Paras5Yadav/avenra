"""Observed access, operational and release controls; bounded local POC scope."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

from .poc_financial import denied
from .private_config import write_document
from .util import canonical,digest,uid


def security(app,tenant,env,run_id,check):
    from .production import POLICY_DEFAULT
    from .operations import DEFAULT
    from avenra_fidelity.archive import encrypt_snapshot,restore_snapshot,recovery_key
    runtime=app.runtime;ops=runtime.operations
    runtime.publish_policy(tenant,env,{**POLICY_DEFAULT,'burst':1000,'rate_per_minute':60000,'max_inflight':1,'request_key':uid('policy')},'synthetic-poc-operator')
    principal='service:'+hashlib.sha256((run_id+':reader').encode()).hexdigest()
    grant=runtime.grant(tenant,env,{'principal':principal,'capabilities':['workspace.read'],'environment_ids':[env],'expires_at':time.time()+300},'synthetic-poc-operator')
    reader=SimpleNamespace(command='GET',identity_context=runtime.context(principal,['read','write']))
    lease=runtime.guard(reader,tenant,env,'accounts');runtime.complete(lease,200)
    check('01-scoped-role','An explicitly granted observer can read its scoped environment',{'allowed':True},{'allowed':bool(lease)},bool(lease))
    reader.command='POST'
    denied(check,'02-granular-denial','Read permission cannot authorize a payment action',lambda:runtime.guard(reader,tenant,env,'banking/upi/payments'),'CAPABILITY_REQUIRED')
    runtime.revoke(tenant,env,grant['id'],'synthetic-poc-operator');reader.command='GET'
    denied(check,'03-grant-revocation','A revoked grant cannot fall back to broader token permissions',lambda:runtime.guard(reader,tenant,env,'accounts'),'GRANT_REVOKED')
    operator=SimpleNamespace(command='GET',identity_context=runtime.context('synthetic-poc-operator',['read','write'],operator=True))
    lease=runtime.guard(operator,tenant,env,'accounts')
    try:denied(check,'04-concurrency-budget','An actual occupied request lease enforces concurrency capacity',lambda:runtime.guard(operator,tenant,env,'accounts'),'TENANT_CAPACITY')
    finally:runtime.complete(lease,200)
    runtime.publish_policy(tenant,env,{**POLICY_DEFAULT,'burst':1,'rate_per_minute':1,'request_key':uid('policy')},'synthetic-poc-operator')
    lease=runtime.guard(operator,tenant,env,'accounts');runtime.complete(lease,200)
    denied(check,'05-rate-budget','The actual token bucket refuses traffic beyond its declared rate',lambda:runtime.guard(operator,tenant,env,'accounts'),'REQUEST_RATE')
    runtime.publish_policy(tenant,env,{**POLICY_DEFAULT,'burst':1000,'rate_per_minute':60000,'max_pending_jobs':1,'request_key':uid('policy')},'synthetic-poc-operator')
    # Publishing a new policy does not mint a new request bucket. Allow one
    # physical token to replenish before measuring the independent queue limit.
    time.sleep(.02);operator.command='POST'
    denied(check,'06-queue-budget','Already accepted work prevents unbounded admission of new financial work',lambda:runtime.guard(operator,tenant,env,'banking/upi/payments'),'QUEUE_CAPACITY')
    ops.publish(tenant,env,{**DEFAULT,'sample_interval_seconds':1,'rejections':1,'for_seconds':0,'recovery_seconds':0,'request_key':uid('monitor')},'synthetic-poc-operator')
    deadline=time.monotonic()+5;incident=None
    while time.monotonic()<deadline:
        ops.cycle();view=ops.overview(tenant,env)
        incident=next((r for r in view['incidents'] if r['signal']=='admission_rejections' and r['state']=='firing'),None)
        if incident:break
        time.sleep(.1)
    check('07-observed-incident','Committed refusal counters produce a real scoped incident',{'state':'firing'},
          {'state':incident['state'] if incident else None,'signal':incident['signal'] if incident else None},bool(incident))
    body={'request_key':uid('ack')};ops.acknowledge(tenant,env,incident['id'],body,'synthetic-poc-reviewer')
    again=ops.acknowledge(tenant,env,incident['id'],body,'synthetic-poc-reviewer')
    check('08-acknowledgement','Acknowledgement is idempotent and does not manufacture recovery',{'replayed':True,'state':'firing'},
          {'replayed':again['replayed'],'state':next(r for r in ops.overview(tenant,env)['incidents'] if r['id']==incident['id'])['state']},
          again['replayed'] and next(r for r in ops.overview(tenant,env)['incidents'] if r['id']==incident['id'])['state']=='firing')
    accounts={r['role']:r for r in app.accounts(tenant,env)}
    body={'payer_id':accounts['payer']['id'],'payee_id':accounts['payee']['id'],'amount_paise':25,'request_key':uid('burst')}
    with ThreadPoolExecutor(max_workers=8) as pool:accepted=list(pool.map(lambda _:app.submit_payment(tenant,env,body),range(8)))
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:app.process_payment(tenant,env,accepted[0]['id']),range(4)))
    ledger=app.environment(tenant,env)['ledger_name'];native=app.accounting.transactions(ledger,'payment:'+accepted[0]['id'])
    check('09-concurrent-native-effect','Eight concurrent replays and competing dispatchers produce one native financial effect',
          {'identities':1,'postings':1},{'identities':len({r['id'] for r in accepted}),'postings':len(native)},len({r['id'] for r in accepted})==1 and len(native)==1)
    directory=app.store.directory/'poc-artifacts'/run_id;directory.mkdir(parents=True,mode=0o700)
    source=directory/'source';source.mkdir(mode=0o700)
    document={'classification':'synthetic_poc_evidence_snapshot','tenant_id':tenant,'environment_id':env,
              'financial_identity':accepted[0]['id'],'native_postings':len(native),'incident_id':incident['id']}
    write_document(source/'evidence.json',document);key=recovery_key(directory/'recovery.key',create=True)
    encrypted=encrypt_snapshot(source,directory/'evidence.enc',key);restored=directory/'restored';restore_snapshot(directory/'evidence.enc',restored,key)
    check('10-authenticated-restore','An actual AES-GCM evidence archive restores exact scoped bytes',{'same_bytes':True},
          {'same_bytes':(source/'evidence.json').read_bytes()==(restored/'evidence.json').read_bytes(),'manifest_hash':encrypted['manifest_hash']},
          (source/'evidence.json').read_bytes()==(restored/'evidence.json').read_bytes())
    denied(check,'11-wrong-recovery-key','A wrong recovery key is refused before any restore is materialized',
           lambda:restore_snapshot(directory/'evidence.enc',directory/'refused',os.urandom(32)),'ARCHIVE_AUTHENTICATION')
    check('12-recovery-scope','This proof covers local evidence restoration and does not claim full off-host disaster recovery',
          {'off_host_disaster_recovery_qualified':False},{'off_host_disaster_recovery_qualified':False,'refused_output_absent':not (directory/'refused').exists()},not (directory/'refused').exists())


def release(app,tenant,env,run_id,check):
    from . import release_bundle as bundle
    root=Path(__file__).resolve().parents[1];directory=app.store.directory/'poc-artifacts'/run_id;directory.mkdir(parents=True,mode=0o700)
    private=directory/'signing.raw';trust=directory/'operator-trust.raw';bundle.new_key(private,trust)
    wheels=root/'.local/release-inputs/wheels'
    one=bundle.build(root,wheels,private,directory/'first.tar.gz','synthetic-poc-build')
    two=bundle.build(root,wheels,private,directory/'second.tar.gz','synthetic-poc-build')
    check('01-reproducible-package','Two independently generated release archives have identical signed content and bytes',
          {'same_artifact':True,'same_archive':True},{'same_artifact':one['artifact_id']==two['artifact_id'],'same_archive':one['archive_sha256']==two['archive_sha256'],'files':one['files']},
          one['artifact_id']==two['artifact_id'] and one['archive_sha256']==two['archive_sha256'])
    inspected=bundle.inspect(directory/'first.tar.gz',trust)
    check('02-source-binding','The externally supplied operator trust key verifies this exact running application build',
          {'fingerprint':app.source_fingerprint},{'fingerprint':inspected['manifest']['application_fingerprint'],'artifact_id':inspected['artifact_id']},
          inspected['manifest']['application_fingerprint']==app.source_fingerprint)
    wrong_key=directory/'wrong.raw';wrong_trust=directory/'wrong-trust.raw';bundle.new_key(wrong_key,wrong_trust)
    denied(check,'03-wrong-trust','An unrelated operator key cannot authorize the archive',lambda:bundle.inspect(directory/'first.tar.gz',wrong_trust),'RELEASE_SIGNATURE')
    installed=bundle.install(directory/'first.tar.gz',trust,directory/'installed')
    verified=bundle.verify_installed(installed['directory'],trust)
    check('04-installed-inventory','Installed content is independently rechecked against the signed inventory',
          {'artifact_id':one['artifact_id']},{'artifact_id':verified['artifact_id']},verified['artifact_id']==one['artifact_id'])
    names=[f['path'] for f in inspected['manifest']['files']]
    check('05-private-state-exclusion','Application releases contain no live installation state or private credentials',{'private_state_included':False},
          {'private_state_included':any('.local' in Path(n).parts or n.endswith(('credentials.json','evidence.key','worker.key')) for n in names)},
          not any('.local' in Path(n).parts or n.endswith(('credentials.json','evidence.key','worker.key')) for n in names))
    check('06-deployment-scope','Packaging does not claim that an upgrade or rollback was deployed',
          {'deployment_changed':False},{'deployment_changed':False,'migration_policy':inspected['manifest']['migration_policy']},inspected['manifest']['migration_policy']=='schema_preserving')
