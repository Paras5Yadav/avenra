#!/usr/bin/env python3
"""Qualify XML contracts, real HTTP and original-ID bank recovery end to end."""
import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avenra.app import App
from avenra.banking import BankingBusy
from avenra.errors import DomainError
from avenra.local_database import admin_dsn
from avenra.private_config import write_document
from avenra.synthetic_lab import fixture, provision, start, stop, prepare, authorize, advance
from avenra.upi import PROFILE_HASH
from avenra.upi_wire import WIRE_LOCAL, WIRE_TLS, MAXIMUM, MEDIA_TYPE, XSD, contract, schema, encode, decode, authenticate, receive
from avenra.util import application_fingerprint, canonical, digest, uid


def case_pack_hash():
    result = hashlib.sha256()
    for name in ['verify_upi_wire.py', 'oracles/upi_wire.go']:
        result.update(name.encode() + b'\0' + (ROOT/'scripts'/name).read_bytes())
    return result.hexdigest()


def open_app(directory):
    path = Path(directory)/'control.dsn'
    return App(directory, control_dsn=path.read_text().strip() if path.exists() else None)


def crash_child(directory, env, identity):
    app = open_app(Path(directory))
    original = app.banking.process
    def committed(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[3] == 'credit' and result['state'] == 'succeeded':os._exit(77)
        return result
    app.banking.process = committed
    for _ in range(15):app.upi.process('tenant_avenra', env, identity)
    raise RuntimeError('Real post-commit crash boundary was not reached.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres', action='store_true')
    args = parser.parse_args()
    directory = ROOT/'.local/qa'/uid('upi_wire')
    directory.mkdir(mode=0o700, parents=True)
    fingerprint = application_fingerprint()
    report = {'passed':False, 'checks':[], 'directory':str(directory), 'application_fingerprint':fingerprint,
              'case_pack_sha256':case_pack_hash(), 'storage':'postgres' if args.postgres else 'sqlite',
              'official_conformance':False, 'production_ready':False,
              'scope':'Owned versioned XML wire contract; independent Go parser/Ed25519 verifier; actual ASGI HTTP, separate mTLS bank processes, native synthetic-money postings and real post-commit process death. Not official UPI XML, XMLDSIG or external participant interoperability.'}
    app, processes, server = None, [], None
    def check(name, condition, detail=None):
        row = {'name':name, 'passed':bool(condition)}
        if detail is not None:row['detail'] = detail
        report['checks'].append(row)
        print(json.dumps({'check':name, 'passed':bool(condition)}), flush=True)
        if not condition:raise AssertionError(name)
    def denied(name, callback, codes=None):
        try:callback()
        except DomainError as error:check(name, codes is None or error.code in codes, error.code)
        else:check(name, False)
    def child(command, name, env=None):
        with (directory/(name+'.log')).open('ab') as output:
            (directory/(name+'.log')).chmod(0o600)
            return subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
    def public(message):
        with app.store.reader() as db:
            return db.execute('SELECT public_key FROM upi_keys WHERE id=?', (message['key_id'],)).fetchone()[0]
    try:
        if args.postgres:
            import psycopg
            from psycopg import sql
            from psycopg.conninfo import conninfo_to_dict, make_conninfo
            database = 'avenra_wire_' + uid('pg').split('_')[1]
            with psycopg.connect(admin_dsn(ROOT), autocommit=True) as connection:
                connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
            dsn = make_conninfo(**{**conninfo_to_dict(admin_dsn(ROOT)), 'dbname':database})
            path = directory/'control.dsn';path.write_text(dsn);path.chmod(0o600)
        app = open_app(directory)
        value = fixture(app);tenant, env = value['tenant_id'], value['environment_id']
        u = app.upi
        ledger = app.environment(tenant, env)['ledger_name']
        def balances():return {row['id']:row['balance_paise'] for row in app.accounts(tenant, env)}
        def observe(payment):
            return {leg['id']:app.accounting.transactions(ledger, leg['id']) for leg in payment['operation']['legs']}
        prior = prepare(app, value, amount=101)
        original_bindings = prior['adapter_bindings_json']
        topology = u.overview(tenant, env)['participants']
        revisions = []
        for actor in topology:
            revision = u.connections.create(tenant, env, {'name':'XML '+actor['role'], 'participant_id':actor['id'], 'adapter_id':WIRE_LOCAL, 'request_key':uid('wire')})
            checked = u.connections.validate(tenant, env, revision['id'], {})
            u.connections.activate(tenant, env, revision['id'], {'check_id':checked['id']})
            revisions.append(revision)
        check('All nine existing actors support opt-in immutable XML connection revisions', len(revisions) == 9)
        denied('Local XML configuration cannot smuggle a remote endpoint', lambda:u.connections.create(tenant, env, {'name':'bad', 'participant_id':topology[0]['id'], 'adapter_id':WIRE_LOCAL, 'request_key':uid('bad'), 'endpoint':'https://example.invalid'}), {'INVALID_CONNECTION'})
        workers = provision(app, value)
        for worker in workers:processes.append(start(app, worker))
        for worker in workers:
            for entry in worker['connections']:
                revision = u.connections.create(tenant, env, {'name':'XML secure bank '+entry['role'], 'adapter_id':WIRE_TLS, 'request_key':uid('wirebank'), **{k:entry[k] for k in ['participant_id','endpoint','secret_reference']}})
                checked = u.connections.validate(tenant, env, revision['id'], {})
                check('XML bank revision observes actual mTLS and signed probe for '+entry['role'], checked['manifest']['passed'] and checked['manifest']['probe']['channel']['tls_version'] in {'TLSv1.2','TLSv1.3'})
                u.connections.activate(tenant, env, revision['id'], {'check_id':checked['id']})
        authorize(app, value, prior);prior = advance(app, value, prior)
        check('An accepted pre-upgrade payment retains all original adapter bindings', prior['state'] == 'succeeded' and prior['adapter_bindings_json'] == original_bindings and not any(e['event']=='wire.validated' for e in prior['events']))
        check('Executable XSD and advertised profile are bound by the contract hash', schema() is not None and contract()['schema_sha256'] == hashlib.sha256(XSD.encode()).hexdigest() and not contract()['official_conformance'])
        before = balances()
        payment = prepare(app, value, amount=12345, mode='collect', financial_fault='lost_credit_response', delivery_fault={'step':5, 'type':'lose_ack_once'})
        payment = advance(app, value, payment, predicate=lambda p:any(m['step']==0 and m['delivery_state']=='delivered' for m in p['messages']))
        check('XML collect presentation preserves authorization before spending', balances()==before and payment['operation_id'] is None)
        authorize(app, value, payment)
        unknown = advance(app, value, payment, state='unknown')
        check('Real beneficiary response loss remains unknown with its original credit already posted', balances()[value['accounts'][1]] == before[value['accounts'][1]]+12345 and unknown['operation']['legs'][1]['fault_applied']==1)
        completed = advance(app, value, payment)
        check('XML, mTLS banks and the same durable worker recover the original payment', completed['state']=='succeeded' and completed['evidence']['valid'] and balances()[value['accounts'][0]]==before[value['accounts'][0]]-12345 and balances()[value['accounts'][1]]==before[value['accounts'][1]]+12345)
        check('Collect plus all seven payment phases traverse the versioned XML boundary', len(completed['messages'])==9 and len([e for e in completed['events'] if e['event']=='wire.validated'])==9)
        check('Lost acknowledgement cannot create a second validated wire identity', any(m['step']==5 and m['attempts']==2 for m in completed['messages']) and len({e['message_id'] for e in completed['events'] if e['event']=='wire.validated'})==9)
        check('Native journal independently contains exactly one posting for every confirmed leg', all(len(v)==1 for v in observe(completed).values()))
        positives = []
        for message in completed['messages']:
            document = json.loads(message['document_json']);raw = encode(document, message['signature'])
            decoded, signature = authenticate(raw, public(message))
            positives.append({'xml_base64':base64.b64encode(raw).decode(), 'public_key':public(message), 'contract_hash':digest(contract()), 'profile_hash':PROFILE_HASH})
            check('Wire phase '+str(message['step'])+' retains exact JSON numeric types, document hash and signature', canonical(decoded)==canonical(document) and signature==message['signature'] and digest(decoded)==message['document_hash'])
        example = next(m for m in completed['messages'] if m['step']==2)
        document = json.loads(example['document_json']);raw = encode(document, example['signature']);key = public(example)
        attacks = {
            'DTD and local entity':b'<!DOCTYPE Message [<!ENTITY x SYSTEM "file:///avenra-synthetic-no-such-file">]>'+raw,
            'network entity':b'<!DOCTYPE Message SYSTEM "http://127.0.0.1:9/synthetic-no-contact">'+raw,
            'processing instruction':b'<?test synthetic?>'+raw,
            'comment':b'<!-- synthetic -->'+raw,
            'UTF-16':raw.decode().encode('utf-16'),
            'malformed UTF-8':b'\xff'+raw,
            'oversize':b' '*(MAXIMUM+1),
            'duplicate root':raw+raw,
            'trailing garbage':raw+b'garbage',
            'unknown namespace':raw.replace(b'urn:avenra:upi:wire:1',b'urn:avenra:upi:wire:2'),
            'unsupported version':raw.replace(b'version="1"',b'version="2"'),
            'altered contract hash':raw.replace(digest(contract()).encode(),b'0'*64),
            'altered profile hash':raw.replace(PROFILE_HASH.encode(),b'0'*64),
            'unknown attribute':raw.replace(b'<a:Header ',b'<a:Header injected="1" '),
            'duplicate financial attribute':raw.replace(b'amountPaise="12345"',b'amountPaise="12345" amountPaise="1"'),
            'fractional paise':raw.replace(b'amountPaise="12345"',b'amountPaise="123.45"'),
            'exponent paise':raw.replace(b'amountPaise="12345"',b'amountPaise="1e2"'),
            'zero amount':raw.replace(b'amountPaise="12345"',b'amountPaise="0"'),
            'amount exceeds profile':raw.replace(b'amountPaise="12345"',b'amountPaise="100000001"'),
            'noncanonical numeric amount':raw.replace(b'amountPaise="12345"',b'amountPaise="012345"'),
            'nonfinite time':raw.replace(('issuedAt="'+canonical(document['issued_at'])+'"').encode(),b'issuedAt="NaN"'),
            'kind and phase mismatch':raw.replace(b'kind="debit.request"',b'kind="credit.request"'),
            'negative generation':raw.replace(b'generation="0"',b'generation="-1"'),
            'signature algorithm substitution':raw.replace(b'Ed25519-canonical-json',b'none'),
            'valid payload with altered amount':raw.replace(b'amountPaise="12345"',b'amountPaise="12346"'),
            'signature wrapping':raw.replace(b'<a:Payload ',b'<a:Wrapper><a:Payload ').replace(b'<a:Authentication ',b'</a:Wrapper><a:Authentication '),
            'duplicate authentication':raw.replace(b'</a:Message>',raw[raw.index(b'<a:Authentication'):raw.index(b'</a:Message>')]+b'</a:Message>'),
        }
        negative_inputs = []
        for name, attack in attacks.items():
            denied('Parser/authentication refuses '+name, lambda attack=attack:authenticate(attack, key))
            negative_inputs.append({**positives[0], 'xml_base64':base64.b64encode(attack).decode(), 'public_key':key})
        build_environment = dict(os.environ, GOPROXY='off', GOSUMDB='off', GOTOOLCHAIN='local', CGO_ENABLED='0')
        oracle = directory/'wire-oracle'
        built = subprocess.run(['go','build','-o',str(oracle),str(ROOT/'scripts/oracles/upi_wire.go')],cwd=ROOT,env=build_environment,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
        check('Independent standard-library oracle builds without network dependencies', built.returncode==0, built.stderr.decode()[-2000:] if built.returncode else None)
        independent = subprocess.run([str(oracle)],input=json.dumps(positives+negative_inputs).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10,check=True)
        findings = json.loads(independent.stdout)
        check('Independent Go XML parser authenticates every positive phase and exact document hash', all(row['accepted'] and row['document_hash']==message['document_hash'] for row,message in zip(findings[:9],completed['messages'])) and len(findings)==len(positives)+len(negative_inputs))
        check('Independent Go parser/authenticator rejects the entire hostile message pack', all(row['accepted'] is False for row in findings[9:]))
        write_document(directory/'independent-wire-results.json', {'contract':contract(), 'oracle_sha256':hashlib.sha256(oracle.read_bytes()).hexdigest(), 'results':findings})
        with socket.socket() as port:
            port.bind(('127.0.0.1',0));number = port.getsockname()[1]
        server = child([sys.executable,'-m','avenra.asgi','--data',str(directory),'--port',str(number),'--mode','api'], 'http')
        credentials = json.loads((directory/'credentials.json').read_text())
        opener = build_opener(ProxyHandler({}));origin = 'http://127.0.0.1:'+str(number)
        base = f'/api/tenants/{tenant}/environments/{env}/banking/upi'
        def http(path, body=None, expected=200, media=MEDIA_TYPE, auth=True, headers=None):
            request_headers = {'Content-Type':media}
            if auth:request_headers['Authorization'] = 'Bearer '+credentials['api_key']
            if headers:request_headers.update(headers)
            request = Request(origin+path, data=body, headers=request_headers)
            try:
                with opener.open(request,timeout=10) as response:status,data,typ=response.status,response.read(),response.headers.get('Content-Type','')
            except HTTPError as error:
                with error:status,data,typ=error.code,error.read(),error.headers.get('Content-Type','')
            if status!=expected:raise AssertionError('Unexpected HTTP '+str(status)+' at '+path)
            return json.loads(data) if 'json' in typ else data,typ
        deadline = time.monotonic()+15
        while True:
            try:
                with opener.open(origin+'/health/live',timeout=1) as response:
                    if response.status==200:break
            except (OSError,URLError):pass
            if server.poll() is not None or time.monotonic()>deadline:raise TimeoutError('The same-app HTTP runtime did not become ready.')
            time.sleep(.05)
        descriptor,_ = http(base+'/wire/contract')
        check('Actual ASGI advertises the source-bound versioned schema and honest authority', descriptor['contract_hash']==digest(contract()) and descriptor['schema']==XSD and not descriptor['official_conformance'])
        http(base+'/wire/contract',expected=401,auth=False)
        check('Wire discovery inherits workspace authentication', True)
        exported,media = http(base+'/wire/messages/'+example['id'])
        check('Actual wire export uses exact versioned media type and retained envelope bytes', exported==raw and media==MEDIA_TYPE+'; charset=utf-8')
        journal_before = observe(completed);balance_before = balances()
        accepted,_ = http(base+'/wire/messages',body=raw,expected=202)
        check('Actual XML ingress authenticates and returns the original durable receipt', accepted['replayed'] and accepted['message_id']==example['id'] and accepted['receipt']['document_hash']==example['document_hash'] and accepted['financial_dispatch'] is False)
        http(base+'/wire/messages',body=raw,expected=415,media='application/xml')
        http(base+'/wire/messages',body=b'{}',expected=415,media='application/json')
        check('Version negotiation refuses unsupported generic XML and JSON fallback', True)
        http(base+'/wire/messages',body=b' '*(MAXIMUM+1),expected=413)
        http(base+'/wire/messages',body=raw,expected=400,headers={'Content-Encoding':'gzip'})
        check('Wire ingress enforces body and content-encoding bounds', True)
        http(base+'/wire/messages',body=attacks['valid payload with altered amount'],expected=403)
        check('Actual HTTP refuses a modified financial payload before admission', True)
        foreign = app.create_environment(tenant,'Foreign XML '+uid('env'),'banking')['id'];app.provision(tenant,foreign)
        http(base.replace(env,foreign)+'/wire/messages',body=raw,expected=403)
        http(base.replace(env,foreign)+'/wire/messages/'+example['id'],expected=404)
        check('Valid signatures and message IDs cannot cross environment boundaries', True)
        forged = copy.deepcopy(document);forged['payload']['amount_paise']+=1
        with app.store.reader() as db:epoch = db.execute('SELECT epoch FROM upi_keys WHERE id=?',(forged['key_id'],)).fetchone()[0]
        forged_raw = encode(forged,u._private_key(forged['sender'],epoch).sign(canonical(forged).encode()).hex())
        http(base+'/wire/messages',body=forged_raw,expected=409)
        check('Even an authentic changed payload cannot replace an accepted financial identity', True)
        check('HTTP replays and attacks leave every authoritative posting and balance unchanged', observe(completed)==journal_before and balances()==balance_before)
        stop(server);server=None
        refund = u.refund(tenant,env,completed['id'],{'request_key':uid('refund'),'amount_paise':2345,'device_id':value['devices'][1]})
        authorize(app,value,refund,customer=1);refund=advance(app,value,refund)
        check('Partial refunds use XML with reversed participant roles and linked original operation', refund['state']=='succeeded' and refund['operation']['parent_id']==completed['operation_id'] and len([e for e in refund['events'] if e['event']=='wire.validated'])==7)
        failed_before=balances()
        reversal=prepare(app,value,amount=700,financial_fault='reject_credit');authorize(app,value,reversal);reversal=advance(app,value,reversal)
        check('A real beneficiary refusal yields XML reversal results and returns the original funds', reversal['state']=='reversed' and reversal['evidence']['valid'] and balances()==failed_before and {l['kind'] for l in reversal['operation']['legs'] if l['state']=='confirmed'}=={'debit','reversal'})
        decline=prepare(app,value,amount=100000000);authorize(app,value,decline);decline=advance(app,value,decline)
        check('Insufficient funds yield XML debit refusal without a beneficiary request or posting', decline['state']=='declined' and decline['evidence']['valid'] and balances()==failed_before and not any(m['step']==4 for m in decline['messages']) and not any(observe(decline).values()))
        financial_variants=[]
        for result in [refund,reversal,decline]:
            for m in result['messages']:
                financial_variants.append({'xml_base64':base64.b64encode(encode(json.loads(m['document_json']),m['signature'])).decode(),'public_key':public(m),'contract_hash':digest(contract()),'profile_hash':PROFILE_HASH})
        variant_result=subprocess.run([str(oracle)],input=json.dumps(financial_variants).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10,check=True)
        variant_findings=json.loads(variant_result.stdout)
        check('The independent parser authenticates linked refunds, reversals and debit-decline messages', len(variant_findings)==len(financial_variants) and all(row['accepted'] for row in variant_findings))
        write_document(directory/'independent-financial-variant-results.json',{'results':variant_findings})
        crashing = prepare(app,value,amount=2000);crashing=authorize(app,value,crashing)
        accepted_bindings = crashing['adapter_bindings_json'];accepted_id = crashing['operation_id']
        app.close();app=None
        crashed=child([sys.executable,str(Path(__file__).resolve()),'--crash-child',str(directory),env,crashing['id']],'committed-crash')
        try:code=crashed.wait(timeout=30)
        except subprocess.TimeoutExpired:stop(crashed);raise
        check('A real child process exits after native credit commit and before recipient receipt', code==77)
        app=open_app(directory);u=app.upi
        uncertain=u.read(tenant,env,crashing['id']);native_before=observe(uncertain)
        check('Post-crash native debit and credit already exist once under their original IDs', sum(len(v) for v in native_before.values())==2 and uncertain['operation_id']==accepted_id and not any(m['step']==4 and m['delivery_state']=='delivered' for m in uncertain['messages']))
        try:u.process(tenant,env,crashing['id'])
        except BankingBusy:check('A replacement process respects the unexpired original delivery lease',True)
        else:check('A replacement process respects the unexpired original delivery lease',False)
        # Let the actual 90-second lease elapse. Do not edit acceptance state
        # or reset the original financial identity to make recovery pass.
        resumed=advance(app,value,crashing,timeout=110)
        check('Restart recovers XML delivery using unchanged bindings and the same financial operation', resumed['state']=='succeeded' and resumed['operation_id']==accepted_id and resumed['adapter_bindings_json']==accepted_bindings and observe(resumed)==native_before)
        check('Post-crash retry retains exactly one wire validation and inbox per message', len([e for e in resumed['events'] if e['event']=='wire.validated'])==7 and len(resumed['messages'])==7 and all(m['delivery_state']=='delivered' for m in resumed['messages']))
        check('Original complete workspace audit chain remains valid', app.store.verify_audit()['valid'])
        check('No application source changed during this qualification', application_fingerprint()==fingerprint)
        check('The verifier and independent oracle stayed unchanged during execution',case_pack_hash()==report['case_pack_sha256'])
        report.update(passed=True,environment_id=env,payment_id=completed['id'],crash_payment_id=resumed['id'],independent_positive_cases=len(positives)+len(financial_variants),independent_negative_cases=len(negative_inputs))
    except Exception as error:
        report.update(error=str(error),traceback=traceback.format_exc())
    finally:
        if server:stop(server)
        for process in processes:stop(process)
        if app:app.close()
    report['created_at']=time.time()
    filename='upi-wire-postgres-verification.json' if args.postgres else 'upi-wire-verification.json'
    write_document(ROOT/'.local'/filename,report)
    attempts=ROOT/'.local/verification-attempts';attempts.mkdir(mode=0o700,exist_ok=True)
    write_document(attempts/('upi-wire-'+('postgres-' if args.postgres else '')+str(time.time_ns())+'.json'),report)
    print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'report':str(ROOT/'.local'/filename),'error':report.get('error')}),flush=True)
    if not report['passed']:print(report.get('traceback',''),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--crash-child':crash_child(*sys.argv[2:])
    else:raise SystemExit(main())
