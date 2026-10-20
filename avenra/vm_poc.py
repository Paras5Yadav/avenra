"""Host-side scoped client for the actual ARM64/KVM proof worker."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import time

from .errors import DomainError,require
from .lab_identity import private_read
from .private_config import read_document
from .util import canonical,digest

ROOT=Path(__file__).resolve().parents[1]
FORMAT='avenra-arm64-kvm-poc/1'


class VmClient:
    def __init__(self):
        self.directory=ROOT/'.local/execution-poc';self.plan=read_document(self.directory/'worker-plan.json',{})
        require(self.plan.get('provisioned') and self.plan.get('instance')=='avenra-poc-worker' and self.plan.get('mounts')==[],
                'VM_NOT_CONFIGURED','Provision and qualify the private VM worker before this scenario.',409)
        self.key=private_read(self.directory/'worker.key',32);require(len(self.key)==32,'VM_IDENTITY','Original private worker key required.',409)
        require(hashlib.sha256((ROOT/'avenra/vm_poc_worker.py').read_bytes()).hexdigest()==self.plan['assets']['worker_source_sha256'],
                'VM_WORKER_VERSION','The worker must be updated to this exact reviewed executor before use.',409)

    def request(self,tenant,environment,identity,script,timeout=10,maximum_output=16384):
        issued=int(time.time())
        return {'format':FORMAT,'request_id':hashlib.sha256(identity.encode()).hexdigest()[:32],
                'tenant_id':tenant,'environment_id':environment,'script':script,'timeout_seconds':timeout,
                'maximum_output_bytes':maximum_output,'issued_at':issued,'expires_at':issued+60}

    def send(self,document,signature=None):
        signed={'document':document,'signature':signature or hmac.new(self.key,canonical(document).encode(),hashlib.sha256).hexdigest()}
        command=['/opt/homebrew/bin/limactl','shell','--workdir','/','avenra-poc-worker','--','sudo','python3','/var/lib/avenra-vm-poc/worker.py']
        try:
            result=subprocess.run(command,input=canonical(signed).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    env={**os.environ,'LIMA_HOME':str(self.directory/'lima')},timeout=45)
            require(len(result.stdout)<=200000,'VM_RESPONSE','Worker response exceeds its bounded receipt.',409)
            value=json.loads(result.stdout)
            require(result.returncode==0 and isinstance(value,dict) and set(value)=={'document','signature'},
                    'VM_UNCONFIRMED','Worker did not return an authenticated execution receipt. Preserve the original execution identity.',409)
            response=value['document'];require(hmac.compare_digest(value['signature'],hmac.new(self.key,canonical(response).encode(),hashlib.sha256).hexdigest()) and
                    response['format']==FORMAT and response['request_hash']==digest(document),
                    'VM_RESPONSE','Worker receipt authentication or request binding failed.',409)
            receipt=response['receipt'];payload={k:v for k,v in document.items() if k not in {'issued_at','expires_at'}}
            require(receipt['request_id']==document['request_id'] and receipt['payload_hash']==digest(payload) and
                    receipt['scope']=={k:document[k] for k in ['tenant_id','environment_id']},
                    'VM_RESPONSE','Execution receipt belongs to another request or scope.',409)
            return {**receipt,'authenticated_response':value,'worker_source_sha256':self.plan['assets']['worker_source_sha256']}
        except (OSError,subprocess.TimeoutExpired,ValueError,KeyError,TypeError) as error:
            raise DomainError('VM_UNCONFIRMED','Execution response is unconfirmed; do not replace or rerun its original identity.',409) from error

    def execute(self,tenant,environment,identity,script,timeout=10,maximum_output=16384):
        return self.send(self.request(tenant,environment,identity,script,timeout,maximum_output))


def isolation(client,tenant,environment,identity):
    marker='/tmp/avenra-isolation-'+hashlib.sha256(identity.encode()).hexdigest()[:16]
    first='set -eu\nprintf marker > '+marker+'\nprintf "GUEST_A_MARKER\\n"\nsleep 2\ntest -f '+marker+'\nid\nip link show\ntest ! -e /Users\ntest ! -e /var/lib/avenra-vm-poc/worker.key\nprintf "GUEST_A_ISOLATED\\n"'
    second='set -eu\ntest ! -e '+marker+'\ntest ! -e /Users\ntest ! -e /var/lib/avenra-vm-poc/worker.key\nid\nip link show\nprintf "GUEST_B_ISOLATED\\n"'
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(client.execute,tenant,environment,identity+':a',first)
        time.sleep(.2)
        b=pool.submit(client.execute,tenant,environment,identity+':b',second)
        left,right=a.result(),b.result()
    return left,right
