#!/usr/bin/env python3
"""Exercise actual financial-data roles, contracts, cryptography and native data."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import secrets
import signal
import sqlite3
import subprocess
import sys
import time
import traceback
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from avenra.app import App
from avenra.aa.contracts import stamp, Contracts, VERSIONS
from avenra.aa.crypto import signing_key,pem,public_pem,sign,verify,ephemeral,derive,document,b64
from avenra.aa.fixture import provision,start,stop,free_port
from avenra.aa.wire import message
from avenra.errors import DomainError
from avenra.gateways import EngineError
from avenra.lab_identity import private_read
from avenra.private_config import write_document
from avenra.synthetic_lab import fixture
from avenra.util import application_fingerprint,canonical,uid


def main():
    postgres='--postgres' in sys.argv;directory=ROOT/'.local/qa'/uid('financial_data');directory.mkdir(mode=0o700,parents=True)
    report={'passed':False,'checks':[],'directory':str(directory),'official_conformance':False,'production_ready':False,'created_at':time.time(),'fingerprint_at_start':application_fingerprint(),'case_pack_at_start':case_pack_hash(),'scope':'Actual locally deployed synthetic AA/FIP/FIU base wire flows, deposit schema/native data, independent Node/OpenSSL cryptography and participant outage/crash recovery. Newer inter-AA contracts and institutional acceptance remain unqualified.'}
    app=None;processes=[];dsn=None
    def check(name,value):
        report['checks'].append({'name':name,'passed':bool(value)})
        if not value:raise AssertionError(name)
    def denied(name,callback,expected):
        try:callback()
        except DomainError as error:check(name,error.code in expected if isinstance(expected,set) else error.code==expected)
        else:check(name,False)
    def wait(callback,predicate=lambda x:bool(x),timeout=20):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            try:
                value=callback()
                if predicate(value):return value
            except (OSError,URLError,DomainError,EngineError):pass
            time.sleep(.1)
        raise TimeoutError('Financial-data processes did not reach the expected state; inspect private logs.')
    def oracle(payload,must_succeed=True):
        result=subprocess.run(['node',str(ROOT/'scripts/aa_review_oracle.cjs')],input=canonical(payload).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        require=result.returncode==0
        if not require:
            if not must_succeed:return None
            raise AssertionError('Independent cryptographic observer refused the payload.')
        return json.loads(result.stdout)
    try:
        if postgres:
            import psycopg
            from psycopg import sql
            from psycopg.conninfo import make_conninfo,conninfo_to_dict
            from avenra.local_database import admin_dsn
            name='avenra_aa_'+str(time.time_ns())
            with psycopg.connect(admin_dsn(ROOT),autocommit=True) as db:db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
            dsn=make_conninfo(**{**conninfo_to_dict(admin_dsn(ROOT)),'dbname':name});(directory/'control.dsn').write_text(dsn);(directory/'control.dsn').chmod(0o600)
        app=App(directory,control_dsn=dsn);value=fixture(app);tenant,env=value['tenant_id'],value['environment_id']
        with app.store.reader() as db:customer=db.execute('SELECT customer_id FROM bank_accounts WHERE account_id=?',(value['accounts'][0],)).fetchone()[0]
        for bank,amount in [(value['banks'][0],30000),(value['banks'][1],20000)]:
            account=app.banking.account(tenant,env,{'bank_id':bank['id'],'customer_id':customer,'vpa':uid('deposit')+'@'+bank['code'],'opening_paise':amount,'request_key':uid('account')})
            app.banking.provision_account(tenant,env,account['account_id'])
        payment=app.banking.submit(tenant,env,{'payer_id':value['accounts'][0],'payee_id':value['accounts'][1],'amount_paise':4500,'request_key':uid('payment')});app.banking.process(tenant,env,payment['id'])
        expected={a['id']:a['balance_paise'] for a in app.accounts(tenant,env)}
        port=free_port();deployment=provision(app,tenant,env,port,ROOT/'.local/aa-research',notice_policy={'base_seconds':.1,'maximum_seconds':.5,'maximum_attempts':3,'timeout_seconds':1,'lease_seconds':2});report.update(environment_id=env,tenant_id=tenant)
        origin='http://127.0.0.1:'+str(port)
        server_spec={'module':'avenra.asgi','arguments':['--data',str(directory),'--port',str(port)],'directory':str(directory),'name':'api'}
        processes.append(start(server_spec,ROOT));processes.extend(start(spec,ROOT) for spec in deployment['processes'])
        credentials=json.loads((directory/'credentials.json').read_text());opener=build_opener(ProxyHandler({}))
        def api(path,body=None):
            req=Request(origin+path,data=canonical(body).encode() if body is not None else None,headers={'Content-Type':'application/json','Authorization':'Bearer '+credentials['api_key']})
            try:response=opener.open(req,timeout=15)
            except HTTPError as error:response=error
            with response:
                value=json.load(response)
                if response.status>=400:
                    e=value['error'];raise DomainError(e['code'],e['message'],response.status)
                return value
        scope='/api/tenants/'+tenant+'/environments/'+env+'/banking/aa'
        wait(lambda:api('/api/health'));config,loaded=app.aa.scope(tenant,env)
        for identity in config['peers']:wait(lambda:app.aa.peer(tenant,env,identity).call('POST','/control/status',{},control=True))
        check('Current application serves the financial-data module with actual TLS role peers',api(scope)['configured'] and all(p.poll() is None for p in processes))
        check('Every selected public artifact is byte-bound and the FIP metadata discrepancy stays visible',any(x['role']=='fip' and not x['metadata_matches'] for x in api(scope)['contract_metadata']))
        customer_config=next(x for x in config['customers'] if x['id']==customer);fips=[k for k,p in config['peers'].items() if p['role']=='fip'];fiu=next(k for k,p in config['peers'].items() if p['role']=='fiu')
        for fip in fips:
            found=api(scope+'/discover',{'fip_id':fip,'customer':customer_config['vua']})
            check('Provider discovery returns only requested customer accounts '+fip,all(a['FIType']=='DEPOSIT' for a in found['DiscoveredAccounts']))
            if found['DiscoveredAccounts']:api(scope+'/links',{'fip_id':fip,'customer':customer_config['vua'],'accounts':found['DiscoveredAccounts']})
        overview=api(scope);links=[x for x in overview['links'] if x['customer']==customer_config['vua']]
        check('Three accounts across two independent providers link through real challenge verification',len(links)==3 and len({x['fip_id'] for x in links})==2)
        detail={'consentStart':stamp(time.time()-5),'consentExpiry':stamp(time.time()+300),'consentMode':'VIEW','fetchType':'PERIODIC','consentTypes':['SUMMARY','TRANSACTIONS'],'fiTypes':['DEPOSIT'],'DataConsumer':{'id':fiu,'type':'FIU'},'Customer':{'id':customer_config['vua']},'Purpose':{'code':'101','text':'Synthetic financial-data review'},'FIDataRange':{'from':stamp(time.time()-86400),'to':stamp(time.time()+60)},'DataLife':{'unit':'DAY','value':1},'Frequency':{'unit':'HOUR','value':20}}
        request_id=str(uuid.uuid4());body={'fiu_id':fiu,'ConsentDetail':detail,'request_id':request_id};created=api(scope+'/consents',body)
        check('FIU-signed wire request creates an AA consent handle',bool(created['ConsentHandle']))
        repeated=api(scope+'/consents',body);check('Managed consent request replay preserves its original handle',repeated['ConsentHandle']==created['ConsentHandle'])
        consent=next(x for x in api(scope)['consents'] if x['handle']==created['ConsentHandle']);report['consent_id']=consent['id']
        approved=api(scope+'/consents/'+consent['id']+'/approve',{'synthetic_customer_approved':True,'link_ids':[x['id'] for x in links]})
        check('Customer approval produces a signed master consent',approved['artifact']['status']=='ACTIVE')
        signed=approved['artifact']['signedConsent'];raw=verify(None,signed,{config['kid']:loaded['key'].public_key()},False)[0]
        check('Independent Node verifies the actual attached consent signature',oracle({'mode':'jws','body':raw.decode(),'signature':signed,'public_key':public_pem(loaded['key'])})['valid'])
        end=stamp(time.time());session=api(scope+'/consents/'+consent['id']+'/request',{'request_id':str(uuid.uuid4()),'range':{'from':detail['FIDataRange']['from'],'to':end}})
        report['session_id']=session['sessionId']
        current=wait(lambda:api(scope),lambda x:next(s for s in x['sessions'] if s['id']==session['sessionId'])['state']=='COMPLETED')
        check('Durable workers complete all provider legs before aggregate readiness',next(s for s in current['sessions'] if s['id']==session['sessionId'])['state']=='COMPLETED')
        review_key=signing_key();public=review_key.public_key().public_numbers()
        from avenra.aa.crypto import b64u
        jwk={'kty':'RSA','n':b64u(public.n.to_bytes((public.n.bit_length()+7)//8,'big')),'e':b64u(public.e.to_bytes(3,'big'))}
        inspection=api(scope+'/sessions/'+session['sessionId']+'/inspect',{'recipient_jwk':jwk})
        check('The aggregator returns an encrypted browser envelope without plaintext XML',set(inspection)=={'classification','encryptedResult','documents'} and inspection['documents']==3 and '<Account' not in canonical(inspection))
        decoded=oracle({'mode':'review','jwe':inspection['encryptedResult'],'private_key':pem(review_key).decode()})
        check('Independent Node decrypts all three FIU-to-browser documents',len(decoded['documents'])==3 and decoded['retained'] is False)
        from lxml import etree
        balances={}
        for doc in decoded['documents']:
            root=loaded['contracts'].document(doc['xml'].encode());summary=root.find('{'+root.nsmap[None]+'}Summary');account=next(x for x in links if x['id']==doc['linkRefNumber']);from decimal import Decimal
            balances[account['account_ref']]=int(Decimal(summary.get('currentBalance'))*100)
        check('Decrypted public-schema balances match the independent native financial authority',all(expected[a]==v for a,v in balances.items()))
        check('Statement-only consent excludes customer profile data',all('Holder' not in x['xml'] for x in decoded['documents']))
        with app.store.reader() as db:
            session_row=dict(db.execute('SELECT * FROM aa_sessions WHERE id=?',(session['sessionId'],)).fetchone());events=''.join(r[0] for r in db.execute('SELECT payload_json FROM audit_events'))
            no_plain=all('encryptedFI' not in r[0] and 'currentBalance' not in r[0] for r in db.execute('SELECT response_json FROM aa_inbox')) and 'currentBalance' not in events
        check('AA inbox and immutable audit records contain no FI plaintext or stored FI responses',no_plain)
        actual=app.aa.assemble(tenant,env,session['sessionId'],fiu,{'txnid':str(uuid.uuid4())})
        peer_directory=Path(next(x['directory'] for x in deployment['roles'] if x['id']==fiu))
        from avenra.aa.peer import Peer
        observer=Peer(peer_directory/'peer.json')
        with observer.db() as db:key_row=db.execute('SELECT * FROM keys WHERE id=?',(session_row['key_reference'],)).fetchone()
        independently_decrypted=oracle({'mode':'fi','private_key':observer.open(key_row['private']).hex(),'Nonce':json.loads(key_row['material'])['Nonce'],'FI':actual['FI']})
        check('Independent Node/OpenSSL decrypts the actual provider payloads',sorted(x['xml'] for x in independently_decrypted)==sorted(x['xml'] for x in decoded['documents']))
        check('Every account uses distinct provider material to prevent AES-GCM nonce reuse',len({(g['KeyMaterial']['DHPublicKey']['KeyValue'],g['KeyMaterial']['Nonce']) for g in actual['FI']})==3)
        changed={**body,'ConsentDetail':{**detail,'consentMode':'STORE'}}
        denied('Changed consent content cannot reuse a managed request identity',lambda:api(scope+'/consents',changed),'CONFLICT')
        from aa_acceptance_cases import run_cases
        run_cases({**locals(),'ROOT':ROOT,'pem':pem})
        from aa_notice_cases import run_notice_cases
        run_notice_cases({**locals(),'ROOT':ROOT})
        api(scope+'/consents/'+consent['id']+'/state',{'state':'PAUSED'})
        denied('Paused consent blocks an already prepared data fetch',lambda:api(scope+'/sessions/'+session['sessionId']+'/inspect',{'recipient_jwk':jwk}),'ConsentPaused')
        api(scope+'/consents/'+consent['id']+'/state',{'state':'ACTIVE'})
        api(scope+'/consents/'+consent['id']+'/state',{'state':'REVOKED'})
        denied('Revocation blocks an already prepared data fetch',lambda:api(scope+'/sessions/'+session['sessionId']+'/inspect',{'recipient_jwk':jwk}),'ConsentRevoked')
        denied('Revoked consent cannot be reactivated',lambda:api(scope+'/consents/'+consent['id']+'/state',{'state':'ACTIVE'}),'ConsentRevoked')
        check('Financial data exchange does not change native payment balances',{a['id']:a['balance_paise'] for a in app.accounts(tenant,env)}==expected)
        check('Financial-data and existing financial events preserve the audit chain',app.store.verify_audit()['valid'])
        check('Application source remains fixed during qualification',report['fingerprint_at_start']==application_fingerprint())
        check('Qualification cases remain fixed during execution',report['case_pack_at_start']==case_pack_hash())
        report['passed']=True
    except Exception as error:report.update(error=str(error),traceback=traceback.format_exc())
    finally:
        for child in reversed(processes):stop(child)
        if app:app.close()
    report.update(application_fingerprint=application_fingerprint(),case_pack_sha256=case_pack_hash(),store='postgres' if postgres else 'sqlite')
    target=ROOT/'.local'/('aa-postgres-verification.json' if postgres else 'aa-verification.json');write_document(target,report)
    write_document(ROOT/'.local/verification-attempts'/('aa-'+str(time.time_ns())+'.json'),report)
    print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'report':str(target),'error':report.get('error')}))
    if not report['passed']:print(report.get('traceback',''))
    return 0 if report['passed'] else 1


def case_pack_hash():
    value=hashlib.sha256()
    for name in ['verify_aa.py','aa_acceptance_cases.py','aa_notice_cases.py','aa_review_oracle.cjs']:
        value.update(name.encode()+b'\0'+(ROOT/'scripts'/name).read_bytes())
    return value.hexdigest()


if __name__=='__main__':raise SystemExit(main())
