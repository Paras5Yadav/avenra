"""Trusted loopback provisioning for same-app independent participant cases."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from .errors import require
from .gateways import EngineError
from .participant_peer_client import PeerClient
from .participant_peer_contract import ADAPTER,ROLES
from .participant_peer_identity import PeerIdentity,provision
from .synthetic_lab import stop
from .util import uid


def client(app,worker):return PeerClient(PeerIdentity(app.store.directory,worker['secret_reference']))


def start(app,worker):
    log=Path(worker['directory'])/'service.log'
    fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'ab') as stream:
        process=subprocess.Popen([sys.executable,'-B','-m','avenra.participant_peer','--config',worker['config']],
                    cwd=Path(__file__).resolve().parents[1],stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    deadline=time.monotonic()+12
    try:
        while time.monotonic()<deadline:
            require(process.poll() is None,'PARTICIPANT_START','Independent participant exited; inspect its private service log.',409)
            try:client(app,worker).probe();return process
            except (EngineError,OSError):time.sleep(.05)
        raise TimeoutError('Independent participant did not become ready.')
    except BaseException:stop(process);raise


def provision_all(app,tenant,env):
    with app.store.reader() as db:
        actors=[dict(r) for r in db.execute('SELECT * FROM upi_participants WHERE tenant_id=? AND environment_id=?',(tenant,env)) if r['role'] in ROLES]
    workers=[]
    for actor in actors:
        with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        workers.append(provision(app,tenant,env,actor['id'],port))
    return workers


def connect(app,tenant,env,workers):
    revisions=[]
    for worker in workers:
        revision=app.upi.connections.create(tenant,env,{'name':'Independent '+worker['role'],'adapter_id':ADAPTER,'request_key':uid('peerrevision'),
                    **{k:worker[k] for k in ['participant_id','endpoint','secret_reference']}})
        checked=app.upi.connections.validate(tenant,env,revision['id'],{})
        require(checked['manifest']['passed'],'PARTICIPANT_PROBE','Independent participant admission requires its authenticated probe.',409)
        app.upi.connections.activate(tenant,env,revision['id'],{'check_id':checked['id']});revisions.append(revision)
    return revisions
