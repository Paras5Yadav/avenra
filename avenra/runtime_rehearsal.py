"""Owned-process HTTPS/mTLS rehearsal of the existing application.

Only trusted application code and synthetic participants are started. Native
accounting remains the financial authority. This is test infrastructure, not
another user app, guest-code runner or live bank connection.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from .app import App
from .database_deployment import provision
from .deployment_identity import issue, client_context
from .errors import require
from .gateways import NoRedirect
from .lab_identity import private_read
from .local_database import admin_dsn
from .operations import DEFAULT
from .private_config import write_document
from .production import POLICY_DEFAULT, ROLE_CAPABILITIES
from .synthetic_lab import fixture
from .util import application_fingerprint, canonical, uid


def free_port():
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));return listener.getsockname()[1]


def wait(callback, predicate=lambda value: bool(value), timeout=25):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            value=callback()
            if predicate(value):return value
        except (OSError,URLError):pass
        time.sleep(.05)
    raise TimeoutError('Rehearsal did not reach the required observation; inspect its retained private process logs.')


class Rehearsal:
    def __init__(self, root, directory, monitoring=True):
        self.processes=[]
        try:self.initialize(root,directory,monitoring)
        except BaseException:
            self.close();raise

    def initialize(self, root, directory, monitoring):
        self.root,self.directory=Path(root),Path(directory);self.directory.mkdir(mode=0o700,parents=True)
        self.data=self.directory/'deployment';self.processes=[];self.apis=[];self.workers=[];self.keys={};self.fixtures={}
        self.receipt=provision(self.data,admin_dsn(self.root));self.dsn=private_read(self.data/'control.dsn').decode().strip()
        self.api_ports=[free_port(),free_port()];self.gateway_port=free_port()
        self.api_identity,self.engine_identity=issue(self.data,'portal'),issue(self.data,'accounting')
        config=json.loads(private_read(self.data/'deployment.json'))
        config.update(role='api',http={'tls_identity':self.api_identity['reference'],'require_client_certificate':True,'max_concurrency':16,'dispatch_timeout_seconds':4},
                      identity={'granular':True,'require_session_subject':True,'max_session_seconds':3600},
                      release={'application_fingerprint':application_fingerprint()},transport={'accounting':{'origin':'https://127.0.0.1:'+str(self.gateway_port),'identity_reference':self.engine_identity['reference']}})
        write_document(self.data/'deployment.json',config)
        write_document(self.data/'gateway.json',{'format':'avenra-service-gateway/1','service':'accounting','upstream':'http://127.0.0.1:4830','port':self.gateway_port,'identity_reference':self.engine_identity['reference']})
        self.gateway=self.start('avenra.service_gateway',['--data',str(self.data),'--config',str(self.data/'gateway.json')],'accounting-gateway')
        engine=build_opener(ProxyHandler({}),NoRedirect(),HTTPSHandler(context=client_context(self.data,self.engine_identity['reference'])))
        wait(lambda:engine.open('https://127.0.0.1:'+str(self.gateway_port)+'/_/info',timeout=2).close() or True)
        app=None
        try:
            app=App(self.data,control_dsn=self.dsn)
            for tenant in ['tenant_avenra','tenant_integration']:
                value=fixture(app,tenant);self.fixtures[tenant]=value
                self.keys[tenant]={}
                for role in ['administrator','operator','observer']:
                    key=app.create_api_key(tenant,'Rehearsal '+role,['read','write'] if role!='observer' else ['read'])
                    self.keys[tenant][role]=key;principal='service:'+hashlib.sha256(('Bearer '+key).encode()).hexdigest()
                    app.runtime.grant(tenant,value['environment_id'],{'principal':principal,'capabilities':ROLE_CAPABILITIES[role],'environment_ids':[value['environment_id']],'expires_at':time.time()+3600},'rehearsal-installer')
                app.runtime.publish_policy(tenant,value['environment_id'],{**POLICY_DEFAULT,'burst':1000,'rate_per_minute':60000,'max_inflight':16,'max_pending_jobs':1000,'request_key':uid('policy')},'rehearsal-installer')
                if monitoring:
                    app.runtime.operations.publish(tenant,value['environment_id'],{**DEFAULT,'window_seconds':30,'sample_interval_seconds':1,'queue_age_seconds':2,'queue_depth':16,'unknown_age_seconds':1,'request_errors':1,'rejections':1,'worker_grace_seconds':6,'for_seconds':0,'recovery_seconds':1,'request_key':uid('monitor')},'rehearsal-installer')
        finally:
            if app:app.close()
        self.context=client_context(self.data,self.api_identity['reference'])
        self.origins=['https://127.0.0.1:'+str(p) for p in self.api_ports]

    def start(self, module, arguments, name):
        path=self.directory/(name+'.log')
        with path.open('ab') as output:
            path.chmod(0o600);child=subprocess.Popen([sys.executable,'-B','-m',module,*arguments],cwd=self.root,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT)
        self.processes.append(child);return child

    def stop(self, child, kill=False):
        if child.poll() is None:
            child.kill() if kill else child.terminate()
            try:child.wait(timeout=20)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)

    def start_api(self, index):
        child=self.start('avenra.asgi',['--data',str(self.data),'--port',str(self.api_ports[index]),'--mode','api'],'api-'+str(index)+'-'+str(time.time_ns()))
        wait(lambda:self.call('/health/live',node=index),lambda r:r['status']==200)
        self.apis.append(child);return child

    def start_worker(self):
        child=self.start('avenra.worker',['--data',str(self.data)],'worker-'+str(time.time_ns()));self.workers.append(child);return child

    def start_all(self):
        for index in range(2):self.start_api(index)
        for _ in range(2):self.start_worker()
        wait(lambda:self.call('/health/ready'),lambda r:r['status']==200 and r['body']['ready'])

    def call(self, path, body=None, tenant='tenant_avenra', role='operator', node=0, timeout=8):
        # Each request gets an independent client opener. Sharing a mutable
        # urllib opener between load threads would distort transport evidence.
        opener=build_opener(ProxyHandler({}),NoRedirect(),HTTPSHandler(context=self.context))
        headers={'Content-Type':'application/json','Authorization':'Bearer '+self.keys[tenant][role]}
        request=Request(self.origins[node]+path,data=canonical(body).encode() if body is not None else None,headers=headers)
        start=time.monotonic()
        try:
            try:response=opener.open(request,timeout=timeout)
            except HTTPError as error:response=error
            with response:
                raw=response.read(4_000_001);require(len(raw)<=4_000_000,'RESPONSE_LIMIT','Rehearsal response exceeds bounds.')
                value=json.loads(raw) if raw else None
            return {'status':response.status,'body':value,'milliseconds':(time.monotonic()-start)*1000}
        except (OSError,URLError) as error:
            return {'status':0,'body':{'error':{'code':'TRANSPORT_UNCONFIRMED','kind':type(error).__name__}},'milliseconds':(time.monotonic()-start)*1000}

    def scope(self, tenant='tenant_avenra'):
        return '/api/tenants/'+tenant+'/environments/'+self.fixtures[tenant]['environment_id']

    def endpoint(self, tail, tenant='tenant_avenra', **kwargs):
        return self.call(self.scope(tenant)+tail,tenant=tenant,**kwargs)

    def submit(self, tenant='tenant_avenra', amount=25, fault='none', key=None, node=0):
        value=self.fixtures[tenant];body={'payer_id':value['accounts'][0],'payee_id':value['accounts'][1],'device_id':value['devices'][0],
                                        'amount_paise':amount,'request_key':key or uid('capacity'),'financial_fault':fault,'expires_in_seconds':900}
        result=self.endpoint('/banking/upi/payments',tenant,body=body,node=node)
        return body,result

    def authorize(self, tenant, identity, node=0):
        value=self.fixtures[tenant]
        return self.endpoint('/banking/upi/payments/'+identity+'/authorize',tenant,body={'device_id':value['devices'][0],'device_proof':value['proofs'][0],'approved':True},node=node)

    def close(self):
        for child in reversed(self.processes):self.stop(child)
