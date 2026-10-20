"""Same-app consent and encrypted-data demonstration with real role services."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
from urllib.error import URLError
from urllib.request import ProxyHandler,build_opener

from .errors import DomainError
from .gateways import EngineError
from .util import canonical,uid


def execute(app,tenant,env,run_id,check):
    from .aa.contracts import stamp
    from .aa.crypto import signing_key,pem,b64u,verify
    from .aa.fixture import provision,start,stop,free_port
    from .synthetic_lab import fixture
    from .poc_financial import denied
    root=Path(__file__).resolve().parents[1];value=fixture(app,tenant,env);processes=[]
    with app.store.reader() as db:customer=db.execute('SELECT customer_id FROM bank_accounts WHERE account_id=?',(value['accounts'][0],)).fetchone()[0]
    extra=app.banking.account(tenant,env,{'bank_id':value['banks'][1]['id'],'customer_id':customer,'vpa':uid('deposit')+'@lab_beta','opening_paise':20000,'request_key':uid('account')})
    app.banking.provision_account(tenant,env,extra['account_id'])
    expected={r['id']:r['balance_paise'] for r in app.accounts(tenant,env)}
    port=free_port();deployment=provision(app,tenant,env,port,root/'.local/aa-research',recipients=1)
    try:
        processes.append(start({'module':'avenra.asgi','arguments':['--data',str(app.store.directory),'--port',str(port),'--mode','api'],
                                'directory':deployment['directory'],'name':'api'},root))
        processes.extend(start(spec,root) for spec in deployment['processes'])
        opener=build_opener(ProxyHandler({}));deadline=time.monotonic()+15
        while True:
            try:
                with opener.open('http://127.0.0.1:'+str(port)+'/health/live',timeout=1) as response:
                    if response.status==200:break
            except (OSError,URLError):pass
            if any(p.poll() is not None for p in processes) or time.monotonic()>deadline:raise TimeoutError('Financial-data services did not become ready.')
            time.sleep(.05)
        config,loaded=app.aa.scope(tenant,env)
        def wait(callback,predicate=lambda v:bool(v)):
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                try:
                    result=callback()
                    if predicate(result):return result
                except (DomainError,EngineError,OSError):pass
                time.sleep(.05)
            raise TimeoutError('The financial-data journey did not reach the expected observation.')
        for identity in config['peers']:wait(lambda identity=identity:app.aa.peer(tenant,env,identity).call('POST','/control/status',{},control=True))
        customer_config=next(c for c in config['customers'] if c['id']==customer)
        fips=[k for k,p in config['peers'].items() if p['role']=='fip'];fiu=next(k for k,p in config['peers'].items() if p['role']=='fiu')
        for fip in fips:
            found=app.aa.discover(tenant,env,{'fip_id':fip,'customer':customer_config['vua']})
            if found['DiscoveredAccounts']:app.aa.link(tenant,env,{'fip_id':fip,'customer':customer_config['vua'],'accounts':found['DiscoveredAccounts']})
        links=[r for r in app.aa.overview(tenant,env)['links'] if r['customer']==customer_config['vua']]
        check('01-provider-links','A proved synthetic customer links accounts at two separate TLS information providers',{'accounts':2,'providers':2},
              {'accounts':len(links),'providers':len({r['fip_id'] for r in links})},len(links)==2 and len({r['fip_id'] for r in links})==2)
        detail={'consentStart':stamp(time.time()-5),'consentExpiry':stamp(time.time()+300),'consentMode':'VIEW','fetchType':'PERIODIC',
                'consentTypes':['SUMMARY','TRANSACTIONS'],'fiTypes':['DEPOSIT'],'DataConsumer':{'id':fiu,'type':'FIU'},
                'Customer':{'id':customer_config['vua']},'Purpose':{'code':'101','text':'Synthetic financial-data review'},
                'FIDataRange':{'from':stamp(time.time()-86400),'to':stamp(time.time()+60)},'DataLife':{'unit':'DAY','value':1},'Frequency':{'unit':'HOUR','value':20}}
        body={'fiu_id':fiu,'ConsentDetail':detail,'request_id':str(uuid.uuid4())}
        created=app.aa.create_consent(tenant,env,body);replayed=app.aa.create_consent(tenant,env,body)
        check('02-consent-replay','A signed FIU request replay retains the original consent handle',{'same_handle':True},
              {'same_handle':created['ConsentHandle']==replayed['ConsentHandle']},created['ConsentHandle']==replayed['ConsentHandle'])
        consent=next(c for c in app.aa.overview(tenant,env)['consents'] if c['handle']==created['ConsentHandle'])
        approved=app.aa.approve(tenant,env,consent['id'],{'synthetic_customer_approved':True,'link_ids':[r['id'] for r in links]})
        raw=verify(None,approved['artifact']['signedConsent'],{config['kid']:loaded['key'].public_key()},False)[0]
        check('03-consent-signature','Actual approved consent has a verifiable bound RSA signature',{'state':'ACTIVE','signature_valid':True},
              {'state':approved['artifact']['status'],'document_sha256':hashlib.sha256(raw).hexdigest()},approved['artifact']['status']=='ACTIVE' and bool(raw))
        session=app.aa.fetch_request(tenant,env,consent['id'],{'request_id':str(uuid.uuid4()),'range':{'from':detail['FIDataRange']['from'],'to':stamp()}})
        def drain():
            with app.store.reader() as db:jobs=[r['id'] for r in db.execute("SELECT * FROM aa_jobs WHERE tenant_id=? AND environment_id=? AND state NOT IN ('done','failed','cancelled')",(tenant,env))]
            for job in jobs:app.aa.process(tenant,env,job)
            return next(s for s in app.aa.overview(tenant,env)['sessions'] if s['id']==session['sessionId'])
        completed=wait(drain,lambda s:s['state']=='COMPLETED')
        check('04-encrypted-provider-flow','Both provider legs reach readiness before the aggregate session completes',{'state':'COMPLETED'},
              {'state':completed['state'],'session_id':session['sessionId']},completed['state']=='COMPLETED')
        review=signing_key();numbers=review.public_key().public_numbers();jwk={'kty':'RSA','n':b64u(numbers.n.to_bytes((numbers.n.bit_length()+7)//8,'big')),'e':b64u(numbers.e.to_bytes(3,'big'))}
        result=app.aa.inspect(tenant,env,session['sessionId'],{'recipient_jwk':jwk})
        oracle=subprocess.run(['node',str(root/'scripts/aa_review_oracle.cjs')],input=canonical({'mode':'review','jwe':result['encryptedResult'],'private_key':pem(review).decode()}).encode(),
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
        if oracle.returncode:raise RuntimeError('The independent information-review decryptor refused the response.')
        decoded=json.loads(oracle.stdout)
        check('05-independent-encryption','An independent Node decryptor opens the FIU-to-reviewer envelope',{'documents':2,'plaintext_in_api':False},
              {'documents':len(decoded['documents']),'plaintext_in_api':'<Account' in canonical(result)},len(decoded['documents'])==2 and '<Account' not in canonical(result))
        from decimal import Decimal
        observed={}
        for document in decoded['documents']:
            element=loaded['contracts'].document(document['xml'].encode());summary=element.find('{'+element.nsmap[None]+'}Summary')
            linked=next(r for r in links if r['id']==document['linkRefNumber']);observed[linked['account_ref']]=int(Decimal(summary.get('currentBalance'))*100)
        check('06-native-financial-oracle','Decrypted schema-valid deposit balances match native accounting exactly',
              {k:expected[k] for k in observed},observed,len(observed)==2 and all(expected[k]==v for k,v in observed.items()))
        app.aa.consent_action(tenant,env,consent['id'],{'state':'REVOKED'})
        denied(check,'07-consent-revocation','Revoked consent refuses further financial-information requests',
               lambda:app.aa.fetch_request(tenant,env,consent['id'],{'request_id':str(uuid.uuid4()),'range':{'from':detail['FIDataRange']['from'],'to':stamp()}}),'ConsentRevoked')
        with app.store.reader() as db:rows=[r[0] for r in db.execute('SELECT response_json FROM aa_inbox WHERE tenant_id=? AND environment_id=?',(tenant,env))]
        check('08-data-minimization','Aggregator inbox evidence retains no plaintext deposit balance',{'plaintext_retained':False},
              {'plaintext_retained':any('currentBalance' in r for r in rows)},all('currentBalance' not in r for r in rows))
    finally:
        for process in reversed(processes):stop(process)
