"""Trusted-code lifecycle helpers for repeatable synthetic bank demonstrations."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from .errors import require
from .errors import DomainError
from .banking import BankingBusy
from .gateways import EngineError
from .lab_bank_client import LabBankClient
from .lab_identity import LabIdentity, TLS_LAB, provision_bank
from .util import uid


def fixture(app, tenant='tenant_avenra', environment=None):
    app.bootstrap()
    env = environment or app.create_environment(tenant, 'Synthetic bank services ' + uid('lab'), 'banking')['id']
    app.provision(tenant, env)
    def request(kind,index):return 'fixture:'+env+':'+kind+':'+str(index) if environment else uid(kind)
    b, u = app.banking, app.upi
    banks = [b.bank(tenant, env, {'name':'Synthetic Bank '+name, 'code':code, 'request_key':request('bank',index)}) for index,(name,code) in enumerate([('Alpha','lab_alpha'),('Beta','lab_beta')])]
    customers = [b.customer(tenant, env, {'name':name,'kind':kind,'request_key':request('customer',index)}) for index,(name,kind) in enumerate([('Synthetic Payer','customer'),('Synthetic Merchant','merchant')])]
    accounts = [b.account(tenant, env, {'bank_id':bank['id'],'customer_id':customer['id'],'vpa':name+'@'+bank['code'],'opening_paise':amount,'request_key':request('account',index)})['account_id'] for index,(bank,customer,name,amount) in enumerate(zip(banks,customers,['payer','merchant'],[1_000_000,100_000]))]
    for account in accounts:b.provision_account(tenant, env, account)
    topology = u.participants(tenant, env, {})
    devices, proofs = [], []
    for i,customer in enumerate(customers):
        device = u.device(tenant, env, {'customer_id':customer['id'],'device_reference':'Synthetic lab handset','request_key':request('device',i)})
        proof = device['enrolment_challenge']
        u.device_action(tenant, env, device['id'], 'activate', {'device_proof':proof,'synthetic_verification':True})
        psp = next(x for x in topology['participants'] if x['bank_id']==banks[i]['id'] and x['role']=='payer_psp')
        u.link(tenant, env, {'device_id':device['id'],'account_id':accounts[i],'psp_id':psp['id']})
        devices.append(device['id']);proofs.append(proof)
    return {'tenant_id':tenant,'environment_id':env,'banks':banks,'accounts':accounts,'devices':devices,'proofs':proofs}


def provision(app, value):
    result = []
    for bank in value['banks']:
        with socket.socket() as port:
            port.bind(('127.0.0.1',0));number=port.getsockname()[1]
        result.append(provision_bank(app,value['tenant_id'],value['environment_id'],bank['id'],number))
    return result


def start(app, worker):
    directory = Path(worker['directory'])
    log_path = directory/'service.log'
    fd=os.open(log_path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'ab') as log:
        process=subprocess.Popen([sys.executable,'-m','avenra.lab_bank','--config',worker['config']],cwd=Path(__file__).resolve().parents[1],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    deadline=time.monotonic()+15
    try:
        while time.monotonic()<deadline:
            require(process.poll() is None,'SYNTHETIC_BANK_START','Synthetic bank exited; inspect its private service log.',409)
            try:
                entry=worker['connections'][0]
                client=LabBankClient(LabIdentity(app.store.directory,entry['secret_reference']))
                client.probe()
                return process
            except (EngineError,OSError):time.sleep(.1)
        raise TimeoutError('Synthetic bank did not become ready.')
    except BaseException:
        stop(process)
        raise


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:process.wait(timeout=15)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


def connect(app, value, workers, adapter_id=TLS_LAB):
    from .upi_wire import WIRE_TLS
    require(adapter_id in {TLS_LAB,WIRE_TLS},'ADAPTER_UNSUPPORTED','Select a supported synthetic bank transport.')
    revisions=[]
    for worker in workers:
        for connection in worker['connections']:
            revision=app.upi.connections.create(value['tenant_id'],value['environment_id'],{'name':worker['worker_id']+' '+connection['role'],'adapter_id':adapter_id,'request_key':uid('revision'),**{k:connection[k] for k in ['participant_id','endpoint','secret_reference']}})
            checked=app.upi.connections.validate(value['tenant_id'],value['environment_id'],revision['id'],{})
            require(checked['manifest']['passed'],'SYNTHETIC_BANK_PROBE','Secure synthetic bank probe did not pass.',409)
            app.upi.connections.activate(value['tenant_id'],value['environment_id'],revision['id'],{'check_id':checked['id']})
            revisions.append(revision)
    return revisions


def prepare(app, value, amount=50000, **extra):
    return app.upi.submit(value['tenant_id'],value['environment_id'],{'payer_id':value['accounts'][0],'payee_id':value['accounts'][1],'device_id':value['devices'][0],'amount_paise':amount,'request_key':uid('payment'),**extra})


def authorize(app, value, payment, customer=0):
    deadline=time.monotonic()+8
    while True:
        try:return app.upi.authorize(value['tenant_id'],value['environment_id'],payment['id'],{'device_id':value['devices'][customer],'device_proof':value['proofs'][customer],'approved':True})
        except DomainError as error:
            if error.code!='PAYMENT_BUSY' or time.monotonic()>=deadline:raise
            time.sleep(.05)


def advance(app, value, payment, state=None, timeout=20, predicate=None):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:app.upi.process(value['tenant_id'],value['environment_id'],payment['id'])
        except BankingBusy:pass
        current=app.upi.read(value['tenant_id'],value['environment_id'],payment['id'])
        if predicate and predicate(current):return current
        if state and current['state']==state:return current
        if not state and not predicate and current['evidence']:return current
        time.sleep(.05)
    raise TimeoutError('Synthetic bank journey did not reach the expected state.')
