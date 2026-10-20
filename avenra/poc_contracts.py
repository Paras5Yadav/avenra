"""Owned synthetic wire acceptance checked by an independent Go parser."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess

from .errors import DomainError
from .synthetic_lab import fixture,prepare,authorize,advance
from .upi_wire import contract,encode,authenticate,MAXIMUM,WIRE_LOCAL
from .upi import PROFILE_HASH
from .util import digest,uid


def execute(app,tenant,env,run_id,check):
    value=fixture(app,tenant,env)
    for actor in app.upi.overview(tenant,env)['participants']:
        revision=app.upi.connections.create(tenant,env,{'name':'POC XML '+actor['role'],'participant_id':actor['id'],'adapter_id':WIRE_LOCAL,'request_key':uid('wire')})
        admitted=app.upi.connections.validate(tenant,env,revision['id'],{})
        app.upi.connections.activate(tenant,env,revision['id'],{'check_id':admitted['id']})
    payment=prepare(app,value,525);authorize(app,value,payment);completed=advance(app,value,payment)
    positives=[]
    for message in completed['messages']:
        raw=encode(json.loads(message['document_json']),message['signature'])
        with app.store.reader() as db:key=db.execute('SELECT public_key FROM upi_keys WHERE id=?',(message['key_id'],)).fetchone()[0]
        positives.append({'xml_base64':base64.b64encode(raw).decode(),'public_key':key,'contract_hash':digest(contract()),'profile_hash':PROFILE_HASH})
    example=next(m for m in completed['messages'] if m['step']==2)
    raw=encode(json.loads(example['document_json']),example['signature'])
    with app.store.reader() as db:key=db.execute('SELECT public_key FROM upi_keys WHERE id=?',(example['key_id'],)).fetchone()[0]
    attacks={
        'changed financial amount':raw.replace(b'amountPaise="525"',b'amountPaise="526"'),
        'unsupported namespace':raw.replace(b'urn:avenra:upi:wire:1',b'urn:avenra:upi:wire:2'),
        'algorithm substitution':raw.replace(b'Ed25519-canonical-json',b'none'),
        'DTD and external entity':b'<!DOCTYPE Message [<!ENTITY x SYSTEM "file:///avenra-no-file">]>'+raw,
        'duplicate root':raw+raw,
        'trailing bytes':raw+b'altered',
        'oversize':b' '*(MAXIMUM+1),
    }
    for index,(name,attack) in enumerate(attacks.items()):
        try:authenticate(attack,key);refused=False
        except DomainError:refused=True
        check('01-negative-'+str(index),'Owned wire acceptance refuses '+name,{'refused':True},{'refused':refused},refused)
    root=Path(__file__).resolve().parents[1];directory=app.store.directory/'poc-artifacts'/run_id;directory.mkdir(mode=0o700,parents=True)
    oracle=directory/'wire-oracle';build=subprocess.run(['go','build','-o',str(oracle),str(root/'scripts/oracles/upi_wire.go')],
            cwd=root,env={**os.environ,'GOPROXY':'off','GOSUMDB':'off','GOTOOLCHAIN':'local','CGO_ENABLED':'0'},stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
    if build.returncode:raise RuntimeError('The independent synthetic wire oracle did not build.')
    vectors=positives+[{'xml_base64':base64.b64encode(attack).decode(),'public_key':key,'contract_hash':digest(contract()),'profile_hash':PROFILE_HASH} for attack in attacks.values()]
    result=subprocess.run([str(oracle)],input=json.dumps(vectors).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
    if result.returncode:raise RuntimeError('The independent synthetic wire oracle refused its case pack.')
    findings=json.loads(result.stdout)
    check('02-independent-positives','An independent Go parser authenticates every original payment phase',{'accepted':len(positives)},
          {'accepted':sum(r['accepted'] for r in findings[:len(positives)]),'oracle_sha256':hashlib.sha256(oracle.read_bytes()).hexdigest()},
          len(findings)==len(vectors) and all(r['accepted'] and r['document_hash']==m['document_hash'] for r,m in zip(findings[:len(positives)],completed['messages'])))
    check('03-independent-negatives','The independent parser rejects all hostile contract vectors',{'refused':len(attacks)},
          {'refused':sum(not r['accepted'] for r in findings[len(positives):])},all(not r['accepted'] for r in findings[len(positives):]))
    check('04-owned-acceptance','A passing owned case pack retains its authority and cannot grant official acceptance',
          {'authority':'owned_synthetic_contract','official_acceptance':False,'independent_security_assessment':False},
          {'authority':'owned_synthetic_contract','official_acceptance':False,'independent_security_assessment':False,'contract_hash':digest(contract()),'native_evidence_valid':completed['evidence']['valid']},
          contract()['official_conformance'] is False and completed['evidence']['valid'])
