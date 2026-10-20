#!/usr/bin/env python3
"""Exercise real TLS, signatures, separate bank processes and financial recovery."""
from concurrent.futures import ThreadPoolExecutor
import copy
import http.client
import json
from pathlib import Path
import shutil
import sqlite3
import ssl
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from avenra.app import App
from avenra.errors import DomainError
from avenra.gateways import EngineError
from avenra.lab_bank_client import LabBankClient, verify_trace
from avenra.lab_identity import LabIdentity, rotate_client, revoke_client
from avenra.lab_identity import private_read, private_write
from avenra.synthetic_lab import fixture, provision, start, stop, connect, prepare, authorize, advance
from avenra.util import application_fingerprint, canonical, digest, uid


def main():
    keep='--keep-running' in sys.argv
    directory=ROOT/'.local/data' if keep else ROOT/'.local/qa'/uid('synthetic_banks')
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    connection=directory/'control.dsn'
    app=None;processes=[]
    report={'passed':False,'checks':[],'directory':str(directory),'scope':'Synthetic bank-leg contract, private test PKI and native synthetic-money accounting. No official UPI wire/certification, independent bank core, HSM custody or institutional deployment qualification.'}
    def check(name, condition, detail=None):
        report['checks'].append({'name':name,'passed':bool(condition),'detail':detail})
        if not condition:raise AssertionError(name)
    def denied(name, callback, code=None):
        try:callback()
        except (EngineError,DomainError) as error:check(name,code is None or error.code==code,error.code)
        else:check(name,False)
    try:
        if keep and (directory/'synthetic-bank-processes.json').exists():raise RuntimeError('Managed synthetic banks already exist. Stop them with scripts/synthetic_bank_services.py stop before installing another demonstration.')
        app=App(directory,control_dsn=connection.read_text().strip() if connection.exists() else None)
        value=fixture(app);tenant,env=value['tenant_id'],value['environment_id']
        workers=provision(app,value)
        for worker in workers:processes.append(start(app,worker))
        check('Two independently running bank service processes are healthy',len({p.pid for p in processes})==2 and all(p.poll() is None for p in processes))
        revisions=connect(app,value,workers)
        check('Four bank-role adapters activate only after observed TLS/signature probes',len(revisions)==4)
        entries={(i,c['role']):c for i,w in enumerate(workers) for c in w['connections']}
        def client(i,role):return LabBankClient(LabIdentity(directory,entries[i,role]['secret_reference']))
        payer=client(0,'remitter');merchant=client(1,'beneficiary')
        probe=payer.probe()
        check('TLS certificate verification and client authentication establish a real encrypted connection',probe['channel']['tls_version'] in {'TLSv1.2','TLSv1.3'},probe['channel'])
        check('The accepted transport profile explicitly excludes official conformance',probe['official_conformance'] is False)
        request=payer.envelope('probe')
        first=payer.send(request);second=payer.send(request)
        check('Authenticated transport replay returns its retained response',first==second)
        altered=copy.deepcopy(request);altered['expires_at']-=1
        denied('Changed signed content cannot reuse a transport identity',lambda:payer.send(altered),'COMMAND_CONFLICT')
        denied('Tampered signatures are rejected by the separate service',lambda:payer.send(payer.envelope('probe'),'0'*128),'SIGNATURE_INVALID')
        foreign=payer.envelope('probe');foreign['scope']['environment_id']='env_foreign'
        denied('A valid signature cannot cross an environment boundary',lambda:payer.send(foreign),'BANK_SCOPE')
        expired=payer.envelope('probe');expired.update(issued_at=int(time.time())-60,expires_at=int(time.time())-30)
        denied('Expired signed commands are rejected',lambda:payer.send(expired),'COMMAND_EXPIRED')
        unsupported=payer.envelope('probe');unsupported['action']='arbitrary_execution'
        denied('The bank accepts only bounded declared commands',lambda:payer.send(unsupported),'INVALID_COMMAND')
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        wrong_signer=client(0,'remitter');wrong_signer.public=Ed25519PrivateKey.generate().public_key()
        denied('An untrusted response signing key cannot authenticate the bank',wrong_signer.probe,'OUTCOME_UNKNOWN')
        identity=payer.identity
        def raw_tls(context,host='127.0.0.1',body=b'{}'):
            connection=http.client.HTTPSConnection(host,payer.port,context=context,timeout=3)
            try:
                connection.request('POST','/v1/commands',body,{'Content-Type':'application/json'});response=connection.getresponse();response.read();return response.status
            finally:connection.close()
        context=ssl.create_default_context(cafile=str(identity.ca))
        try:raw_tls(context)
        except (OSError,http.client.HTTPException):check('TLS refuses a client without a certificate',True)
        else:check('TLS refuses a client without a certificate',False)
        wrong_ca=ssl.create_default_context(cafile=str(merchant.identity.ca))
        wrong_ca.load_cert_chain(str(identity.directory/identity.document['client_cert']),str(identity.directory/identity.document['client_key']))
        try:raw_tls(wrong_ca)
        except (OSError,http.client.HTTPException):check('TLS refuses a server under another synthetic bank trust root',True)
        else:check('TLS refuses a server under another synthetic bank trust root',False)
        try:raw_tls(payer.context,'localhost')
        except (OSError,http.client.HTTPException):check('TLS enforces the server certificate hostname',True)
        else:check('TLS enforces the server certificate hostname',False)
        from datetime import datetime,timedelta,timezone
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        original=x509.load_pem_x509_certificate(private_read(identity.directory/identity.document['client_cert']))
        ca_key=serialization.load_pem_private_key(private_read(Path(workers[0]['directory'])/'ca-key.pem'),password=None)
        current=datetime.now(timezone.utc)
        builder=x509.CertificateBuilder().subject_name(original.subject).issuer_name(original.issuer).public_key(original.public_key()).serial_number(x509.random_serial_number()).not_valid_before(current-timedelta(hours=2)).not_valid_after(current-timedelta(hours=1))
        for extension in original.extensions:builder=builder.add_extension(extension.value,extension.critical)
        certificate=builder.sign(ca_key,hashes.SHA256())
        expired_path=directory/('expired-'+uid('client')+'.pem');private_write(expired_path,certificate.public_bytes(serialization.Encoding.PEM))
        expired_context=ssl.create_default_context(cafile=str(identity.ca));expired_context.load_cert_chain(str(expired_path),str(identity.directory/identity.document['client_key']))
        try:raw_tls(expired_context)
        except (OSError,http.client.HTTPException):check('TLS rejects an expired client certificate issued by the trusted lab CA',True)
        else:check('TLS rejects an expired client certificate issued by the trusted lab CA',False)
        check('Duplicate JSON fields are rejected before command dispatch',raw_tls(payer.context,body=b'{"document":{},"document":{}}')==400)
        def balances():return {x['id']:x['balance_paise'] for x in app.accounts(tenant,env)}
        before=balances()
        p=prepare(app,value,mode='collect',financial_fault='lost_credit_response')
        advance(app,value,p,predicate=lambda result:any(m['step']==0 and m['delivery_state']=='delivered' for m in result['messages']))
        check('Collect presentation leaves both native balances unchanged',balances()==before)
        authorize(app,value,p)
        unknown=advance(app,value,p,state='unknown')
        check('Credit commits in the native ledger before its actual TLS response is lost',balances()[value['accounts'][0]]==before[value['accounts'][0]]-50000 and balances()[value['accounts'][1]]==before[value['accounts'][1]]+50000 and unknown['operation']['legs'][1]['state']=='unknown')
        old=client(1,'beneficiary');original_binding=old.identity.binding
        rotated=rotate_client(directory,entries[1,'beneficiary']['secret_reference'])
        revoke_client(directory,entries[1,'beneficiary']['secret_reference'],rotated['previous_key_id'])
        denied('The revoked signing identity is refused even with a valid TLS certificate',old.probe,'SIGNING_KEY_REVOKED')
        renewed=client(1,'beneficiary')
        check('Credential rotation preserves the pinned bank authority',renewed.identity.binding==original_binding and renewed.probe()['credential_epoch']==2)
        completed=advance(app,value,p)
        check('The original uncertain credit recovers after credential rotation without another financial effect',completed['state']=='succeeded' and completed['operation_id']==unknown['operation_id'] and balances()[value['accounts'][1]]==before[value['accounts'][1]]+50000)
        receipts=completed['evidence']['manifest']['bank_transport_receipts']
        check('Financial evidence retains verifiable TLS bank request/response traces',len(receipts)>=5 and all(verify_trace(x) for x in receipts))
        check('The uncertain attempt and renewed identity both remain in evidence',any(x['outcome']=='unknown' for x in receipts) and any(x['identity_epoch']==2 for x in receipts))
        native=[x for leg in completed['operation']['legs'] for x in app.accounting.transactions(app.environment(tenant,env)['ledger_name'],leg['id'])]
        check('Independent native journal observations identify two distinct bank workers',len(native)==2 and {x['metadata']['synthetic_bank_worker'] for x in native}=={w['worker_id'] for w in workers})
        debit=completed['operation']['legs'][0]
        effect={'operation_id':completed['operation_id'],'leg_id':debit['id'],'leg_kind':'debit','body':{'postings':[{'source':debit['source'],'destination':debit['destination'],'amount':debit['amount_paise']+1,'asset':'INR/2'}],'reference':debit['id'],'metadata':{'operation_id':completed['operation_id'],'leg':'debit','profile':completed['operation']['profile']}}}
        denied('The separate bank refuses conflicting amounts under one financial identity',lambda:payer.request('post',effect),'FINANCIAL_CONFLICT')
        exact=copy.deepcopy(effect);exact['body']['postings'][0]['amount']=debit['amount_paise']
        with ThreadPoolExecutor(4) as pool:
            results=list(pool.map(lambda _:client(0,'remitter').request('post',exact)['transaction']['id'],range(8)))
        check('Concurrent authenticated financial replays return one native posting',len(set(map(str,results)))==1 and len(app.accounting.transactions(app.environment(tenant,env)['ledger_name'],debit['id']))==1)
        wrong_account=copy.deepcopy(effect);wrong_account['body']['postings'][0]['source']='bank:foreign:account'
        denied('A client cannot debit an account outside its bank scope',lambda:payer.request('post',wrong_account),'BANK_ACCOUNT_SCOPE')
        refund=app.upi.refund(tenant,env,p['id'],{'amount_paise':20000,'request_key':uid('refund'),'device_id':value['devices'][1]})
        authorize(app,value,refund,1);returned=advance(app,value,refund)
        check('Partial refund crosses the same services with reversed bank roles',returned['state']=='succeeded' and balances()[value['accounts'][0]]==before[value['accounts'][0]]-30000 and balances()[value['accounts'][1]]==before[value['accounts'][1]]+30000)
        over=prepare(app,value,amount=2_000_000);authorize(app,value,over);over=advance(app,value,over)
        check('A real native insufficient-funds result becomes a signed bank decline',over['state']=='declined' and over['evidence']['valid'])
        reversal=prepare(app,value,amount=1000,financial_fault='reject_credit');authorize(app,value,reversal);reversal=advance(app,value,reversal)
        check('Beneficiary rejection reverses the original debit through the remitter service',reversal['state']=='reversed' and any(x['kind']=='reversal' and x['state']=='confirmed' for x in reversal['operation']['legs']))
        prior=balances();stopped=processes[1];stop(stopped)
        outage=prepare(app,value,amount=1000);authorize(app,value,outage);outage=advance(app,value,outage,state='unknown')
        check('A stopped beneficiary service leaves uncertainty after confirmed debit',outage['operation']['legs'][0]['state']=='confirmed' and outage['operation']['legs'][1]['state']=='unknown' and balances()[value['accounts'][1]]==prior[value['accounts'][1]])
        checkpoint=directory/'synthetic-bank-restores'/uid('checkpoint');checkpoint.mkdir(parents=True,mode=0o700)
        worker_path=Path(workers[1]['directory'])
        shutil.copytree(worker_path,checkpoint/'worker',ignore=shutil.ignore_patterns('*.log','*-wal','*-shm'))
        bundle_paths=[directory/'participant-secrets'/c['secret_reference'].removeprefix('secret://') for c in workers[1]['connections']]
        for bundle_path in bundle_paths:shutil.copytree(bundle_path,checkpoint/bundle_path.name)
        archive=checkpoint/'originals';archive.mkdir(mode=0o700)
        shutil.move(str(worker_path),str(archive/'worker'));shutil.copytree(checkpoint/'worker',worker_path)
        for bundle_path in bundle_paths:
            shutil.move(str(bundle_path),str(archive/bundle_path.name));shutil.copytree(checkpoint/bundle_path.name,bundle_path)
        with sqlite3.connect(checkpoint/'worker/bank.sqlite') as saved,sqlite3.connect(worker_path/'bank.sqlite') as restored:
            check('The bank checkpoint preserves command, effect and receipt counts',all(saved.execute('SELECT count(*) FROM '+table).fetchone()[0]==restored.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ['requests','effects','events']))
        processes[1]=start(app,workers[1]);outage=advance(app,value,outage)
        check('Restart from the retained bank receipt database resumes the original payment',outage['state']=='succeeded' and balances()[value['accounts'][0]]==prior[value['accounts'][0]]-1000 and balances()[value['accounts'][1]]==prior[value['accounts'][1]]+1000)
        check('Restored bank receipts still authenticate under the rotated identity',client(1,'beneficiary').probe()['credential_epoch']==2)
        check('Restored private identity files retain the original bank authority and permissions',client(1,'beneficiary').identity.binding==original_binding and all(not p.stat().st_mode&0o077 for p in worker_path.iterdir() if p.is_file()))
        check('All completed evidence excludes official certification claims',all(x['evidence']['valid'] and not x['evidence']['manifest']['official_conformance'] for x in [completed,returned,over,reversal,outage]))
        check('The full workspace audit chain verifies',app.store.verify_audit()['valid'])
        report.update(passed=True,environment_id=env,payment_id=p['id'],refund_id=refund['id'],workers=[{'worker_id':w['worker_id'],'endpoint':w['endpoint']} for w in workers])
        if keep:
            state={'scope':'Owned synthetic loopback bank services','workers':[{'pid':process.pid,'config':w['config'],'worker_id':w['worker_id'],'endpoint':w['endpoint']} for process,w in zip(processes,workers)]}
            target=directory/'synthetic-bank-processes.json';target.write_text(json.dumps(state,indent=2)+'\n');target.chmod(0o600)
    except Exception as error:
        report.update(error=str(error),traceback=traceback.format_exc())
    finally:
        if not keep or not report['passed']:
            for process in processes:stop(process)
        if app:app.close()
    report.update(application_fingerprint=application_fingerprint(),created_at=time.time(),services_kept_running=keep and report['passed'])
    target=ROOT/'.local/synthetic-bank-verification.json';text=json.dumps(report,indent=2)+'\n';target.write_text(text);target.chmod(0o600)
    attempts=ROOT/'.local/verification-attempts';attempts.mkdir(exist_ok=True)
    attempt=attempts/('synthetic-banks-'+str(time.time_ns())+'.json');attempt.write_text(text);attempt.chmod(0o600)
    print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'report':str(target),'error':report.get('error'),'services_kept_running':report['services_kept_running']}))
    if not report['passed']:print(report.get('traceback',''))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
