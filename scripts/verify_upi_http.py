#!/usr/bin/env python3
"""Qualify the real participant API, ASGI framing and background delivery."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import sys
import time
import traceback
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.util import application_fingerprint, uid
from avenra.participant_connections import LOCAL, EXTERNAL


def main():
    credentials = json.loads((ROOT / '.local/data/credentials.json').read_text())
    tenant, token = credentials['tenant_id'], credentials['api_key']
    report = {'passed': False, 'checks': [], 'scope': 'Actual ASGI HTTP, PostgreSQL, native accounting and durable participant worker. Synthetic profile only.'}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def check(name, condition, detail=None):
        report['checks'].append({'name': name, 'passed': bool(condition), 'detail': detail})
        if not condition:raise AssertionError(name)
    def api(path, body=None, expected=200, auth=True, raw=None):
        headers = {'Content-Type': 'application/json'}
        if auth:headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request('http://127.0.0.1:4827' + path, data=raw if raw is not None else json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with opener.open(request, timeout=15) as response:
                status, value = response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:status, value = error.code, json.load(error)
        if status != expected:raise AssertionError(f'Unexpected HTTP {status} at {path}: {value}')
        return value
    def until(callback, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = callback()
            if predicate(value):return value
            time.sleep(.05)
        raise TimeoutError('HTTP observation did not reach the expected state.')
    try:
        with opener.open('http://127.0.0.1:4827/api/health', timeout=5) as response:
            check('ASGI health responds with bounded security headers', response.status == 200 and response.headers['X-Content-Type-Options'] == 'nosniff' and 'server' not in response.headers)
        env = api(f'/api/tenants/{tenant}/environments', {'name': 'UPI HTTP ' + uid('qa'), 'fixture_profile': 'banking'}, 202)['id']
        scope = f'/api/tenants/{tenant}/environments/{env}'
        until(lambda: api(scope + '/overview'), lambda x: x['environment']['status'] == 'active')
        base, bank = scope + '/banking/upi', scope + '/banking'
        api(base, expected=401, auth=False)
        check('Participant API rejects unauthenticated reads', True)
        api(base.replace(tenant, 'tenant_integration'), expected=403)
        check('Workspace key cannot read another workspace participant lab', True)
        api(base + '/payments', expected=400, raw=b'{"amount_paise":100,"amount_paise":200}')
        check('ASGI/router rejects duplicate JSON financial fields', True)
        api(base + '/payments', expected=400, raw=b'{"amount_paise":1e9999}')
        check('ASGI/router rejects numeric overflow before financial dispatch', True)
        api(base + '/payments', expected=413, raw=b'{' + b'a' * 65536 + b'}')
        check('ASGI refuses oversized request bodies', True)
        connection = http.client.HTTPConnection('127.0.0.1', 4827, timeout=5)
        connection.putrequest('GET', base)
        connection.putheader('Authorization', 'Bearer ' + token)
        connection.putheader('Authorization', 'Bearer different')
        connection.endheaders();response = connection.getresponse();response.read()
        check('Duplicate authentication headers are rejected at ingress', response.status == 400);connection.close()
        tag = uid('http').split('_')[1][:10]
        banks = [api(bank + '/banks', {'name': 'HTTP Bank ' + s, 'code': s + '_' + tag, 'request_key': uid('bank')}, 202) for s in ['payer', 'merchant']]
        customers = [api(bank + '/customers', {'name': 'HTTP ' + s, 'kind': k, 'request_key': uid('customer')}, 202) for s,k in [('Payer','customer'),('Merchant','merchant')]]
        accounts = [api(bank + '/accounts', {'bank_id': bk['id'], 'customer_id': customer['id'], 'vpa': name + '@' + bk['code'], 'opening_paise': amount, 'request_key': uid('account')}, 202)['account_id'] for bk,customer,name,amount in zip(banks,customers,['payer','merchant'],[500000,100000])]
        until(lambda: api(bank), lambda x: all(any(a['account_id'] == identity and a['status'] == 'active' for a in x['accounts']) for identity in accounts))
        lab = api(base + '/participants', {}, 202)
        check('HTTP topology exposes nine real persisted actors', len(lab['participants']) == 9)
        actor = lambda i,role:next(x for x in lab['participants'] if x['bank_id'] == banks[i]['id'] and x['role'] == role)['id']
        registry=api(base+'/connections')
        check('HTTP registry separates executable synthetic and unimplemented institutional profiles',registry['profiles'][0]['implemented'] and not registry['profiles'][1]['implemented'])
        api(base+'/connections',expected=401,auth=False)
        check('Connection settings require authenticated workspace access',True)
        connection_body={'name':'HTTP remitter revision','participant_id':actor(0,'remitter'),'adapter_id':LOCAL,'request_key':uid('revision')}
        revision=api(base+'/connections',connection_body,202)
        check('Connection revisions persist and replay through scoped HTTP',api(base+'/connections',connection_body,202)['id']==revision['id'])
        api(base+'/connections',{**connection_body,'api_key':'synthetic-secret-test'},400)
        check('Connection API rejects raw secret fields',True)
        external=api(base+'/connections',{**connection_body,'name':'Future bank connection','adapter_id':EXTERNAL,'request_key':uid('external'),'endpoint':'https://bank.example/uat','secret_reference':'secret://future-uat'},202)
        inspection=api(base+'/connections/'+external['id']+'/validate',{},200)
        check('An endpoint and secret reference cannot masquerade as a working integration',not external['executable'] and not inspection['manifest']['passed'] and not inspection['manifest']['official_conformance'])
        api(base+'/connections/'+external['id']+'/activate',{'check_id':inspection['id']},409)
        check('HTTP activation refuses the unfinished external adapter',True)
        checked=api(base+'/connections/'+revision['id']+'/validate',{},200)
        api(base+'/connections/'+revision['id']+'/activate',{'check_id':inspection['id']},409)
        check('HTTP activation rejects a check for another revision',True)
        bound=api(base+'/connections/'+revision['id']+'/activate',{'check_id':checked['id']},200)
        check('Validated local connection becomes active through HTTP',bound['active'] and not bound['official_conformance'])
        api(base.replace(env,uid('env'))+'/connections',expected=404)
        check('Connection registry cannot cross environment boundaries',True)
        devices, proofs, links = [], [], []
        for i,customer in enumerate(customers):
            device = api(base + '/devices', {'customer_id':customer['id'], 'device_reference':'HTTP handset', 'request_key':uid('device')},202)
            proof = device['enrolment_challenge'];devices.append(device['id']);proofs.append(proof)
            api(base+'/devices/'+device['id']+'/activate', {'device_proof':proof, 'synthetic_verification':True},200)
            links.append(api(base+'/links', {'device_id':device['id'],'account_id':accounts[i],'psp_id':actor(i,'payer_psp')},202)['id'])
        check('Devices and links execute through actual scoped endpoints', len(api(base)['links']) == 2)
        def balance(i):return api(base+'/balance', {'link_id':links[i],'device_proof':proofs[i]},202)['balance_paise']
        before = [balance(0),balance(1)]
        payload = {'request_key':uid('pay'), 'payer_id':accounts[0], 'payee_id':accounts[1], 'amount_paise':5000, 'device_id':devices[0], 'financial_fault':'lost_credit_response'}
        p = api(base+'/payments',payload,202)
        check('HTTP payment intent pins the selected remitter revision',json.loads(p['adapter_bindings_json'])['remitter']['connection_id']==revision['id'])
        check('HTTP preparation cannot spend funds before authorization', p['operation_id'] is None and balance(0) == before[0] and balance(1) == before[1])
        check('HTTP request replay returns the original payment identity', api(base+'/payments',payload,202)['id'] == p['id'])
        api(base+'/payments',{**payload,'amount_paise':5001},409)
        check('HTTP changed payload replay conflicts', True)
        api(base+'/payments/'+p['id']+'/authorize',{'device_id':devices[0],'device_proof':proofs[0],'approved':True},202)
        unknown = until(lambda:api(base+'/payments/'+p['id']),lambda x:x['state']=='unknown')
        check('HTTP exposes real response loss as uncertain', unknown['operation']['legs'][1]['fault_applied'] == 1 and balance(1) == before[1]+5000)
        completed = until(lambda:api(base+'/payments/'+p['id']),lambda x:x['evidence'] is not None)
        check('Actual worker recovers bank result without another financial effect', completed['state']=='succeeded' and balance(0)==before[0]-5000 and balance(1)==before[1]+5000)
        check('Actual worker delivers all seven participant messages', len(completed['messages'])==7 and all(x['delivery_state']=='delivered' for x in completed['messages']))
        evidence = api(base+'/payments/'+p['id']+'/evidence')
        check('HTTP evidence is valid for the loaded build and bounded profile', evidence['valid'] and evidence['manifest']['application_fingerprint']==application_fingerprint() and evidence['manifest']['official_conformance'] is False)
        check('Retained financial evidence includes the immutable connection choice',json.loads(evidence['manifest']['payment']['adapter_bindings_json'])['remitter']['connection_id']==revision['id'])
        message = completed['messages'][1]
        receipt = api(base+'/messages',{'envelope':json.loads(message['document_json']),'signature':message['signature']},202)
        check('HTTP transport replay reuses original immutable recipient receipt', receipt['replayed'] and receipt['receipt']['message_id']==message['id'])
        api(base+'/messages',{'envelope':json.loads(message['document_json']),'signature':'0'*128},409)
        check('HTTP rejects altered message authentication',True)
        foreign = api(f'/api/tenants/{tenant}/environments',{'name':'UPI foreign '+uid('qa'),'fixture_profile':'banking'},202)['id']
        api(base.replace(env,foreign)+'/payments/'+p['id'],expected=404)
        check('Payment and evidence cannot cross environments',True)
        refund = api(base+'/payments/'+p['id']+'/refund',{'request_key':uid('refund'),'amount_paise':2000,'device_id':devices[1]},202)
        api(base+'/payments/'+refund['id']+'/authorize',{'device_id':devices[1],'device_proof':proofs[1],'approved':True},202)
        returned = until(lambda:api(base+'/payments/'+refund['id']),lambda x:x['evidence'] is not None)
        check('HTTP partial refund uses original payment and reversed participant roles',returned['state']=='succeeded' and returned['operation']['parent_id']==completed['operation_id'] and balance(0)==before[0]-3000 and balance(1)==before[1]+3000)
        api(base+'/participants/'+actor(1,'beneficiary')+'/pause',{},200)
        test = api(base+'/payments',{**payload,'request_key':uid('blocked-credit'),'financial_fault':'none','amount_paise':100},202)
        api(base+'/payments/'+test['id']+'/authorize',{'device_id':devices[0],'device_proof':proofs[0],'approved':True},202)
        debited = until(lambda:api(base+'/payments/'+test['id']),lambda x:x['operation'] and any(l['kind']=='debit' and l['state']=='confirmed' for l in x['operation']['legs']))
        check('Paused beneficiary has not received the confirmed remitter debit',not any(l['kind']=='credit' and l['state']=='confirmed' for l in debited['operation']['legs']))
        api(bank+'/accounts/'+accounts[1]+'/state',{'state':'blocked'},200)
        api(base+'/participants/'+actor(1,'beneficiary')+'/resume',{},200)
        reversed_payment = until(lambda:api(base+'/payments/'+test['id']),lambda x:x['evidence'] is not None)
        check('Beneficiary block after debit returns funds through a separate reversal',reversed_payment['state']=='reversed' and balance(0)==before[0]-3000 and balance(1)==before[1]+3000)
        api(bank+'/accounts/'+accounts[1]+'/state',{'state':'active'},200)
        api(base+'/participants/'+actor(0,'payer_psp')+'/pause',{},200)
        test = api(base+'/payments',{**payload,'request_key':uid('revoked-device'),'financial_fault':'none','amount_paise':100},202)
        api(base+'/payments/'+test['id']+'/authorize',{'device_id':devices[0],'device_proof':proofs[0],'approved':True},202)
        api(base+'/devices/'+devices[0]+'/revoke',{},200)
        api(base+'/participants/'+actor(0,'payer_psp')+'/resume',{},200)
        revoked = until(lambda:api(base+'/payments/'+test['id']),lambda x:x['evidence'] is not None)
        check('Revoking a device before first debit prevents financial dispatch',revoked['state']=='declined' and all(l['state']!='confirmed' for l in revoked['operation']['legs']))
        samples=[]
        def concurrent(_):
            start=time.monotonic();value=api(base);samples.append((time.monotonic()-start)*1000);return value['profile_hash']
        with ThreadPoolExecutor(8) as pool:hashes=list(pool.map(concurrent,range(20)))
        check('Declared 20-read / 8-client workload preserves scoped responses',len(set(hashes))==1,{'requests':20,'concurrency':8,'p95_ms':round(sorted(samples)[18],2),'scope':'Observed local read sample; not a general throughput/SLO claim.'})
        report.update(passed=True,environment_id=env,payment_id=p['id'])
    except Exception as failure:
        report.update(error=str(failure),traceback=traceback.format_exc())
    report['application_fingerprint']=application_fingerprint()
    content=json.dumps(report,indent=2)+'\n';target=ROOT/'.local/upi-http-verification.json';target.write_text(content);target.chmod(0o600)
    attempts=ROOT/'.local/verification-attempts';attempts.mkdir(exist_ok=True)
    (attempts/('upi-http-'+str(time.time_ns())+'.json')).write_text(content)
    print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'report':str(target),'error':report.get('error')}))
    if not report['passed']:print(report.get('traceback',''))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
