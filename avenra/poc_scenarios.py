"""Executable POC cases inside the existing scenario and evidence workflow."""
import hashlib
import json

from .errors import DomainError,require
from .util import application_fingerprint,digest,now

CATALOG=[
    {'key':'poc_participant_journey','name':'Independent payment network and recovery','group':'Production POC',
     'description':'Use separate bank, PSP and network services; lose a real response, stop a participant, recover once and refund.'},
    {'key':'poc_customer_lifecycle','name':'Credentials, mandates and back-office','group':'Production POC',
     'description':'Exercise proved devices, synthetic PIN rules, recurring payments, complaints and reconciled settlement.'},
    {'key':'poc_financial_information','name':'Consent and encrypted financial information','group':'Production POC',
     'description':'Link two information providers, approve signed consent, decrypt with an independent reviewer and revoke access.'},
    {'key':'poc_platform_controls','name':'Access, capacity and operational evidence','group':'Production POC',
     'description':'Enforce scoped roles and capacity; retain actual incidents, concurrent payment proof and authenticated evidence restoration.'},
    {'key':'poc_signed_release','name':'Reproducible signed application release','group':'Production POC',
     'description':'Build identical signed packages, reject an untrusted signer and verify installed content without changing the deployment.'},
    {'key':'poc_contract_acceptance','name':'Independent synthetic contract acceptance','group':'Production POC',
     'description':'Verify original payment XML with an independent Go implementation and reject hostile contract vectors.'},
    {'key':'poc_vm_isolation','name':'Real VM isolation and limits','group':'Production POC',
     'description':'Boot two actual KVM guests; check separate filesystems, bounded execution, signed receipts and cleanup.'},
    {'key':'poc_cryptography','name':'Cryptography and key lifecycle','group':'Production POC',
     'description':'Exercise real signatures/encryption, reject tampering and revoked keys, then recover the original payment.'},
]

# This is the original broad scope. A passing implemented case does not mark
# unrelated areas complete, and displayed entries are not executable stubs.
AREAS=[
    ('payments','Payments, refunds, reversals and uncertainty','poc_participant_journey','Demonstrate collect, uncertainty, original-identity recovery and partial refunds; full reversal qualification remains in the production matrix.'),
    ('participants','Independent banks, PSPs and network recovery','poc_participant_journey','Demonstrate independent inbox admission and process restart; cold recovery qualification remains separate.'),
    ('devices','Customer, device and credential journeys','poc_customer_lifecycle','Demonstrate proved account discovery, credential gates and payment authorization.'),
    ('backoffice','Mandates, disputes and settlement','poc_customer_lifecycle','Demonstrate due execution, revocation, dual approval and reconciled reports.'),
    ('variants','Stored value, delegated, credit and cross-border profiles',None,'Implement the selected synthetic specialized journeys.'),
    ('aa','Account Aggregator consent and encrypted information','poc_financial_information','Demonstrate deposit consent, independent provider roles and encrypted data; other schemas/inter-AA acceptance remain separately qualified.'),
    ('cryptography','Cryptography and key lifecycle','poc_cryptography','Run signatures, authenticated encryption and actual key revocation/recovery.'),
    ('identity','Identity, permissions and service trust','poc_platform_controls','Demonstrate role/capability enforcement and grant revocation; deployed institutional SSO/TLS qualification remains separate.'),
    ('vm','Actual VM execution and isolation','poc_vm_isolation','Run hardware-backed guests, isolation, limits and cleanup tests.'),
    ('operations','Capacity, failover and disaster recovery','poc_platform_controls','Demonstrate admission, concurrent financial effects and encrypted evidence restore; broad load, host failover and full DR remain separately qualified.'),
    ('monitoring','Monitoring, incidents and evidence','poc_platform_controls','Demonstrate committed refusal counters, incidents and idempotent acknowledgement.'),
    ('release','Reproducible releases, upgrades and rollback','poc_signed_release','Demonstrate deterministic signing and installed inventory checks; deployment upgrade/rollback qualification remains separate.'),
    ('acceptance','Contract tests and acceptance workflow','poc_contract_acceptance','Demonstrate owned positive/hostile vectors with an independent parser; external decisions remain unverified.'),
]


def coverage(app,tenant,environment):
    with app.store.reader() as db:
        rows=db.execute("SELECT r.scenario_key,e.* FROM runs r JOIN evidence e ON e.run_id=r.id WHERE r.tenant_id=? AND r.source_environment_id=? AND r.state='passed' ORDER BY r.completed_at DESC LIMIT 100",(tenant,environment)).fetchall()
    matched={}
    for row in rows:
        verified=app.verify_evidence(dict(row));manifest=verified['manifest']
        fixed=next((s for s in manifest.get('steps',[]) if s.get('key')=='98-source'),None)
        if verified['valid'] and fixed and fixed.get('passed') and fixed.get('observed',{}).get('fingerprint')==app.source_fingerprint:
            matched.setdefault(row['scenario_key'],manifest['run_id'])
    areas=[{'id':key,'name':name,'scenario_key':case,'state':'passed' if case in matched else 'ready_to_run' if case else 'remaining',
            'run_id':matched.get(case),'description':description} for key,name,case,description in AREAS]
    return {'scope':'Full original synthetic UPI/NPCI/RBI-oriented sandbox objective. Whole-product completion requires all supported demonstrations and their qualification.',
            'areas':areas,'passed_areas':sum(a['state']=='passed' for a in areas),'total_areas':len(areas),'poc_complete':False,
            'production_ready':False,'official_acceptance_received':False,'independent_assessment_completed':False}


def execute(app,tenant,run_id):
    from .scenarios import finalize,step
    run=app.run(tenant,run_id)
    if run['state'] in {'passed','failed'}:return
    env=run['environment_id'];failure=None
    def check(key,name,expected,observed,passed):step(app,run_id,key,name,expected,observed,passed)
    with app.store.transaction() as db:db.execute("UPDATE runs SET state='running',started_at=COALESCE(started_at,?) WHERE id=?",(now(),run_id))
    try:
        app.provision(tenant,env);environment=app.environment(tenant,env,True)
        check('00-scope','POC uses its own scoped run environment',{'purpose':'run'},
              {'purpose':environment['purpose'],'tenant_id':tenant,'environment_id':env},environment['purpose']=='run' and environment['tenant_id']==tenant)
        from .poc_financial import participants,lifecycle
        from .poc_information import execute as information
        from .poc_platform import security,release
        from .poc_contracts import execute as contracts
        functions={'poc_vm_isolation':vm,'poc_cryptography':cryptography,
                   'poc_participant_journey':participants,'poc_customer_lifecycle':lifecycle,'poc_financial_information':information,
                   'poc_platform_controls':security,'poc_signed_release':release,'poc_contract_acceptance':contracts}
        require(run['scenario_key'] in functions,'POC_SCENARIO','Unsupported POC case.')
        functions[run['scenario_key']](app,tenant,env,run_id,check)
        check('98-source','Execution retained one application build',{'fingerprint':app.source_fingerprint},
              {'fingerprint':application_fingerprint()},app.source_fingerprint==application_fingerprint())
    except Exception as error:
        failure=type(error).__name__+': '+str(error)[:250]
        with app.store.transaction() as db:
            from .util import canonical,uid
            db.execute('INSERT OR IGNORE INTO run_steps VALUES(?,?,?,?,?,?,?,?)',
                       (uid('step'),run_id,'99-completion','POC case completed',canonical({'completed':True}),canonical({'error':failure}),0,now()))
    finalize(app,tenant,run_id,failure)


def vm(app,tenant,env,run_id,check):
    from .vm_poc import VmClient,isolation
    client=VmClient();left,right=isolation(client,tenant,env,run_id)
    receipts=[left,right]
    check('01-actual-vms','Two actual hardware-accelerated guests complete their scripts',{'states':['completed','completed'],'exit_codes':[0,0]},
          {'states':[r['state'] for r in receipts],'exit_codes':[r.get('exit_code') for r in receipts],
           'vmm':[r.get('vmm_observations') for r in receipts],'errors':[r.get('error') for r in receipts]},all(r['state']=='completed' and r.get('exit_code')==0 and r['vmm_observations']['kvm']['enabled'] is True for r in receipts))
    check('02-filesystems','Guests have different kernel boot identities and separate temporary filesystems',{'different_boot_ids':True,'markers_isolated':True},
          {'boot_ids':[r['guest_boot_id'] for r in receipts],'guest_a':'GUEST_A_ISOLATED' in left['output'],'guest_b':'GUEST_B_ISOLATED' in right['output']},
          left['guest_boot_id']!=right['guest_boot_id'] and 'GUEST_A_ISOLATED' in left['output'] and 'GUEST_B_ISOLATED' in right['output'])
    check('03-privileges','Submitted scripts execute as the unprivileged guest user',{'uid':1000},
          {'outputs':[r['output'] for r in receipts]},all('uid=1000(avenra)' in r['output'] for r in receipts))
    check('04-resources','Observed VMM CPU/memory limits and absent network/mount devices match the profile',
          {'cpus':1,'memory_bytes':268435456,'network_devices':0,'host_mounts':0},
          {'resources':[r['resources'] for r in receipts]},all(r['resources']=={'cpus':1,'memory_bytes':268435456,'network_devices':0,'host_mounts':0} and
          r['vmm_observations']['cpus']==1 and r['vmm_observations']['memory']['base-memory']==268435456 for r in receipts))
    doc=client.request(tenant,env,run_id+':replay','set -eu\nprintf "REPLAY_ORIGINAL\\n"')
    original=client.send(doc);again=client.send(doc)
    check('05-replay','Authenticated request replay returns the original execution and boot identity',{'same_receipt':True},
          {'original_boot_id':original['guest_boot_id'],'replayed_boot_id':again['guest_boot_id']},
          original['guest_boot_id']==again['guest_boot_id'] and original['output_sha256']==again['output_sha256'])
    altered={**doc,'script':'printf changed'}
    try:client.send(altered);conflict=None
    except DomainError as error:conflict=error.code
    check('06-conflict','Changed script cannot reuse an accepted execution identity',{'refused':True},{'error_code':conflict},conflict=='VM_UNCONFIRMED')
    bad=client.request(tenant,env,run_id+':bad-signature','printf must-not-run')
    try:client.send(bad,'0'*64);denied=None
    except DomainError as error:denied=error.code
    check('07-authentication','Invalid execution credentials are refused before guest dispatch',{'refused':True},{'error_code':denied},denied=='VM_UNCONFIRMED')
    timeout=client.execute(tenant,env,run_id+':timeout','sleep 20',timeout=1)
    check('08-deadline','Actual guest deadline terminates and reaps its VMM process',{'state':'timed_out','process_reaped':True},
          {'state':timeout['state'],'seconds':timeout.get('seconds'),'process_reaped':timeout.get('process_reaped')},
          timeout['state']=='timed_out' and timeout['process_reaped'] and timeout['seconds']<5)
    output=client.execute(tenant,env,run_id+':output','while :; do printf "bounded-output\\n"; done',maximum_output=2048)
    check('09-output','Actual guest output is bounded and its VMM is reaped',{'state':'output_limit','maximum_bytes':2048},
          {'state':output['state'],'output_bytes':len(output.get('output','').encode()),'process_reaped':output.get('process_reaped')},
          output['state']=='output_limit' and len(output['output'].encode())<=2048 and output['process_reaped'])
    check('10-cleanup','All observed guest VMM processes are reaped',{'reaped':True},
          {'receipts':[{k:r.get(k) for k in ['request_id','state','guest_boot_id','process_reaped','worker_source_sha256']} for r in [*receipts,original,timeout,output]]},
          all(r['process_reaped'] for r in [*receipts,original,timeout,output]))
    check('11-qualification-scope','This observed ARM64 KVM proof does not claim the included x86 engine or official acceptance',
          {'production_ready':False,'official_conformance':False,'included_x86_engine_qualified':False},
          {k:left[k] for k in ['production_ready','official_conformance','included_x86_engine_qualified']},
          not left['production_ready'] and not left['official_conformance'] and not left['included_x86_engine_qualified'])


def cryptography(app,tenant,env,run_id,check):
    from cryptography.exceptions import InvalidSignature,InvalidTag
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from .synthetic_lab import fixture,prepare,authorize,advance
    from .util import canonical
    import os
    value=fixture(app,tenant,env);payment=prepare(app,value,amount=525);authorize(app,value,payment)
    with app.store.reader() as db:
        message=dict(db.execute('SELECT * FROM upi_envelopes WHERE payment_id=? AND step=1',(payment['id'],)).fetchone())
        actor=dict(db.execute('SELECT * FROM upi_participants WHERE id=?',(message['sender'],)).fetchone())
        old=dict(db.execute('SELECT * FROM upi_keys WHERE id=?',(message['key_id'],)).fetchone())
    from .upi_wire import authenticate,encode
    raw=encode(json.loads(message['document_json']),message['signature']);decoded,_=authenticate(raw,old['public_key'])
    check('01-signature','The actual accepted payment envelope authenticates with its registered Ed25519 key',{'same_document_hash':True},
          {'document_hash':digest(decoded),'key_id':old['id']},digest(decoded)==message['document_hash'])
    signer=Ed25519PrivateKey.generate();payload=canonical({'tenant_id':tenant,'environment_id':env,'run_id':run_id}).encode();signature=signer.sign(payload)
    try:signer.public_key().verify(signature,payload+b'!');tamper=False
    except InvalidSignature:tamper=True
    check('02-tamper','Changed signed bytes fail actual Ed25519 verification',{'refused':True},{'refused':tamper},tamper)
    left,right=X25519PrivateKey.generate(),X25519PrivateKey.generate()
    a,b=left.exchange(right.public_key()),right.exchange(left.public_key())
    key=HKDF(algorithm=hashes.SHA256(),length=32,salt=None,info=b'avenra-poc-encryption/1').derive(a)
    check('03-key-agreement','Separate X25519 keypairs derive the same shared secret',{'same_secret':True},{'same_secret':a==b},a==b)
    nonce=os.urandom(12);aad=canonical({'tenant_id':tenant,'environment_id':env}).encode();cipher=AESGCM(key).encrypt(nonce,payload,aad)
    check('04-encryption','AES-256-GCM decrypts only with the original key and scope binding',{'round_trip':True},
          {'round_trip':AESGCM(key).decrypt(nonce,cipher,aad)==payload,'ciphertext_sha256':hashlib.sha256(cipher).hexdigest()},AESGCM(key).decrypt(nonce,cipher,aad)==payload)
    try:AESGCM(key).decrypt(nonce,cipher[:-1]+bytes([cipher[-1]^1]),aad);altered=False
    except InvalidTag:altered=True
    check('05-ciphertext','Changed authenticated ciphertext is refused',{'refused':True},{'refused':altered},altered)
    try:AESGCM(key).decrypt(nonce,cipher,aad+b'foreign');foreign=False
    except InvalidTag:foreign=True
    check('06-scope','Ciphertext cannot be authenticated under another scope',{'refused':True},{'refused':foreign},foreign)
    rotated=app.upi.participant_action(tenant,env,actor['id'],'rotate-key',{})
    app.upi.participant_action(tenant,env,actor['id'],'revoke-key',{'key_id':old['id']})
    app.upi.process(tenant,env,payment['id'])
    refused=app.upi.read(tenant,env,payment['id'])
    check('07-revocation','Actual registered-key revocation prevents the first financial dispatch',{'state':'unknown','native_effects':0},
          {'state':refused['state'],'delivery_error':refused['messages'][0]['delivery_error'],'confirmed_legs':sum(l['state']=='confirmed' for l in refused['operation']['legs'])},
          refused['state']=='unknown' and refused['messages'][0]['delivery_error']=='KEY_REVOKED' and not any(l['state']=='confirmed' for l in refused['operation']['legs']))
    app.upi.reconcile(tenant,env,payment['id'],{});recovered=advance(app,value,payment)
    check('08-rotation-recovery','The rotated key recovers the original financial operation once',{'state':'succeeded','same_operation':True},
          {'state':recovered['state'],'same_operation':recovered['operation_id']==refused['operation_id'],'key_epoch':rotated['key_epoch'],'evidence_valid':bool(recovered['evidence'] and recovered['evidence']['valid'])},
          recovered['state']=='succeeded' and recovered['operation_id']==refused['operation_id'] and recovered['evidence']['valid'])
