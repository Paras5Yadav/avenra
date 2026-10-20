"""Operator-owned synthetic AA/FIP/FIU deployment for current-app rehearsals."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid

from ..deployment_identity import issue
from ..errors import require
from ..lab_identity import private_write
from ..util import canonical
from .contracts import install
from .crypto import signing_key, pem, public_pem


def free_port():
    with socket.socket() as server:server.bind(('127.0.0.1',0));return server.getsockname()[1]


def copy_client(source, reference, destination, name):
    original=Path(source)/'deployment-secrets'/reference[9:];target=Path(destination)/'deployment-secrets'/name;target.mkdir(parents=True,mode=0o700)
    for file in ['client-cert.pem','client-key.pem','ca-cert.pem']:private_write(target/file,(original/file).read_bytes())
    return 'secret://'+name


def provision(app, tenant, env, api_port, cache, recipients=2, notice_policy=None):
    environment=app.environment(tenant,env,True);directory=app.store.directory/'aa-scopes'/env
    require(not directory.exists(),'AA_DEPLOYMENT_EXISTS','Preserve existing financial-data peers; use a new environment or explicit recovery.',409)
    directory.mkdir(parents=True,mode=0o700);install(directory,cache)
    aa_key=signing_key();aa_id='aa_'+env;aa_kid='aa_'+str(uuid.uuid4());private_write(directory/'signing-key.pem',pem(aa_key))
    gateway_dir=directory/'ingress';gateway_dir.mkdir(mode=0o700);tls=issue(gateway_dir,'ingress');gateway_port=free_port();prefix='/aa/'+tenant+'/'+env
    gateway={'format':'avenra-service-gateway/1','service':'aa','upstream':'http://127.0.0.1:'+str(api_port),'port':gateway_port,'identity_reference':tls['reference'],'prefix':prefix}
    private_write(gateway_dir/'gateway.json',canonical(gateway));processes=[{'module':'avenra.service_gateway','arguments':['--data',str(gateway_dir),'--config',str(gateway_dir/'gateway.json')],'directory':str(gateway_dir),'name':'ingress'}]
    with app.store.reader() as db:
        accounts=[dict(r) for r in db.execute('SELECT ba.*,a.address,c.name holder FROM bank_accounts ba JOIN accounts a ON a.id=ba.account_id JOIN bank_customers c ON c.id=ba.customer_id WHERE ba.tenant_id=? AND ba.environment_id=? AND ba.status=? ORDER BY ba.bank_id,ba.account_id',(tenant,env,'active'))]
    require(accounts,'AA_ACCOUNTS','Configure active bank accounts before provisioning financial-data peers.')
    customers=[{'id':identity,'vua':identity+'@avenra-aa','identifier':str(9_000_000_000+i)} for i,identity in enumerate(sorted({a['customer_id'] for a in accounts}))]
    definitions=[('fip','fip_'+bank,[a for a in accounts if a['bank_id']==bank]) for bank in sorted({a['bank_id'] for a in accounts})]+[('fiu','fiu_'+str(uuid.uuid4()).replace('-',''),[]) for _ in range(recipients)]
    peers={};roles=[]
    for role,identity,owned in definitions:
        folder=directory/'counterparties'/identity;folder.mkdir(parents=True,mode=0o700);install(folder,cache)
        key=signing_key();kid=role+'_'+str(uuid.uuid4());private_write(folder/'signing-key.pem',pem(key));private_write(folder/'state.key',secrets.token_bytes(32))
        identity_tls=issue(folder,'listener');number=free_port();root_ref=copy_client(folder,identity_tls['reference'],directory,'peer_'+identity)
        upstream_ref=copy_client(gateway_dir,tls['reference'],folder,'aggregator')
        api_key,aa_api_key,control_secret=secrets.token_urlsafe(32),secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        cfg={'format':'avenra-aa-peer/1','classification':'synthetic','role':role,'id':identity,'aa_id':aa_id,'kid':kid,'port':number,'identity_reference':identity_tls['reference'],
             'peer_kid':aa_kid,'peer_public_key':public_pem(aa_key),'api_key':api_key,'control_secret':control_secret,
             'aa':{'origin':'https://127.0.0.1:'+str(gateway_port),'prefix':prefix,'identity_reference':upstream_ref,'kid':aa_kid,'public_key':public_pem(aa_key),'api_key':aa_api_key},'accounts':[]}
        if notice_policy is not None:cfg['notice_policy']=notice_policy
        for account in owned:
            customer=next(c for c in customers if c['id']==account['customer_id'])
            cfg['accounts'].append({'account_id':account['account_id'],'customer':customer['vua'],'identifier':customer['identifier'],'holder':account['holder'],
                                    'ledger':environment['ledger_name'],'address':account['address'],'opening_date':account['created_at'][:10],'masked':'XXXXXXXX'+str(int(hashlib.sha256(account['account_id'].encode()).hexdigest()[:8],16)%10000).zfill(4)})
        private_write(folder/'peer.json',canonical(cfg));peers[identity]={'role':role,'origin':'https://127.0.0.1:'+str(number),'identity_reference':root_ref,'kid':kid,'public_key':public_pem(key),'api_key':api_key,'aa_api_key':aa_api_key,'control_secret':control_secret}
        processes.append({'module':'avenra.aa.peer','arguments':['--config',str(folder/'peer.json')],'directory':str(folder),'name':identity});roles.append({'id':identity,'role':role,'directory':str(folder)})
    profile={'format':'avenra-aa-scope/1','classification':'synthetic','id':aa_id,'kid':aa_kid,'tenant_id':tenant,'environment_id':env,'customers':customers,'peers':peers}
    if notice_policy is not None:profile['delivery_policy']=notice_policy
    private_write(directory/'profile.json',canonical(profile));private_write(directory/'process-spec.json',canonical(processes))
    return {'tenant_id':tenant,'environment_id':env,'directory':str(directory),'processes':processes,'roles':roles,'classification':'synthetic'}


def start(spec, root):
    directory=Path(spec['directory']);log=directory/'service.log'
    with log.open('ab') as output:
        log.chmod(0o600);process=subprocess.Popen([sys.executable,'-B','-m',spec['module'],*spec['arguments']],cwd=root,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
    return process


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:process.wait(timeout=15)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
