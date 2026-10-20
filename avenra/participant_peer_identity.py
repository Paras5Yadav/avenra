"""Private loopback PKI for independent synthetic PSP/network inboxes."""
import hashlib
import json
from pathlib import Path
import re

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec,ed25519

from .errors import require
from .lab_identity import (_certificate,_private,_signing_public,identity_lock,
                           private_read,private_write,replace_private)
from .participant_peer_contract import CONTRACT,CONTRACT_HASH,ROLES,SCOPE_FIELDS
from .util import canonical,digest,uid


def binding(document):
    return digest({k:document[k] for k in SCOPE_FIELDS+['endpoint','server_signer_public','ca_sha256','transport_hash']})


class PeerIdentity:
    def __init__(self,directory,secret_reference):
        require(isinstance(secret_reference,str) and re.fullmatch('secret://peer_[a-z0-9_]{1,65}',secret_reference),
                'INVALID_SECRET_REFERENCE','Select a private participant identity reference.')
        self.installation=Path(directory).resolve();root=self.installation/'participant-peer-secrets'
        self.directory=root/secret_reference.removeprefix('secret://')
        import os,stat
        for path in [root,self.directory]:
            info=path.lstat()
            require(stat.S_ISDIR(info.st_mode) and not path.is_symlink() and info.st_uid==os.getuid() and not info.st_mode&0o077,
                    'PRIVATE_IDENTITY_REQUIRED','Participant identity directories require private ordinary ownership.',409)
        self.document=json.loads(private_read(self.directory/'bundle.json'))
        require(self.document['transport_hash']==CONTRACT_HASH and self.document['role'] in ROLES,
                'PROFILE_UNSUPPORTED','Participant identity uses an unsupported transport.',409)
        self.binding=binding(self.document);self.ca=self.directory/'ca.pem'
        require(hashlib.sha256(private_read(self.ca)).hexdigest()==self.document['ca_sha256'],
                'IDENTITY_CONFLICT','Participant trust root differs from its pinned identity.',409)
        for field in ['client_cert','client_key','signer_key']:
            require(isinstance(self.document[field],str) and re.fullmatch(r'[a-z]+_[0-9]+\.(pem|raw)',self.document[field]),
                    'INVALID_SECRET_REFERENCE','Use a bounded versioned participant identity filename.')
            private_read(self.directory/self.document[field])
        self.signer=ed25519.Ed25519PrivateKey.from_private_bytes(private_read(self.directory/self.document['signer_key']))
        require(_signing_public(self.signer)==self.document['client_signer_public'],
                'IDENTITY_CONFLICT','Participant client signing identity differs from its public key.',409)


def registry(app,tenant,env):
    with app.store.reader() as db:
        rows=db.execute('SELECT k.id,k.public_key,k.revoked,p.id AS participant_id,p.role FROM upi_keys k JOIN upi_participants p ON p.id=k.participant_id WHERE p.tenant_id=? AND p.environment_id=?',(tenant,env)).fetchall()
    require(0<len(rows)<=CONTRACT['maximum_registry_keys'],'PARTICIPANT_REGISTRY_CAPACITY',
            'This participant profile requires a bounded public-key registry; explicit registry lifecycle is required before further keys.',409)
    return {row['id']:{'public_key':row['public_key'],'revoked':bool(row['revoked']),
                       'participant_id':row['participant_id'],'role':row['role']} for row in rows}


def _issue_client(secret,worker,configuration,document,epoch):
    ca=x509.load_pem_x509_certificate(private_read(worker/'ca.pem'))
    ca_key=serialization.load_pem_private_key(private_read(worker/'ca-key.pem'),password=None)
    tls=ec.generate_private_key(ec.SECP256R1());cert=_certificate(document['participant_id']+' epoch '+str(epoch),tls,ca,ca_key,client=True)
    signer=ed25519.Ed25519PrivateKey.generate();key=uid('peerclient')
    document.update(identity_epoch=epoch,client_key_id=key,client_signer_public=_signing_public(signer),
                    client_cert=f'client_{epoch}.pem',client_key=f'tls_{epoch}.pem',signer_key=f'signer_{epoch}.raw')
    for name,value in [(document['client_cert'],cert.public_bytes(serialization.Encoding.PEM)),
                       (document['client_key'],_private(tls)),
                       (document['signer_key'],signer.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption()))]:
        private_write(secret/name,value)
    configuration['clients'][key]={'public_key':document['client_signer_public'],'revoked':False}


def provision(app,tenant,env,participant_id,port):
    require(type(port) is int and 1024<=port<=65535,'INVALID_PORT','Use a nonprivileged loopback port.')
    app.environment(tenant,env,True)
    with app.store.reader() as db:actor=app.upi.get(db,'upi_participants',tenant,env,participant_id)
    require(actor['role'] in ROLES,'ROLE_UNSUPPORTED','Independent inboxes support PSP and network actors.')
    pinned_registry=registry(app,tenant,env)
    worker_id=uid('peer');root=app.store.directory/'synthetic-participants';root.mkdir(mode=0o700,exist_ok=True)
    worker=root/worker_id;worker.mkdir(mode=0o700)
    secrets=app.store.directory/'participant-peer-secrets';secrets.mkdir(mode=0o700,exist_ok=True)
    secret=secrets/worker_id;secret.mkdir(mode=0o700)
    ca_key=ec.generate_private_key(ec.SECP256R1());ca=_certificate(worker_id+' CA',ca_key,None,None,ca=True)
    tls=ec.generate_private_key(ec.SECP256R1());cert=_certificate(worker_id,tls,ca,ca_key)
    signer=ed25519.Ed25519PrivateKey.generate();ca_pem=ca.public_bytes(serialization.Encoding.PEM)
    configuration={'worker_id':worker_id,'tenant_id':tenant,'environment_id':env,'participant_id':participant_id,
                   'role':actor['role'],'endpoint':f'https://127.0.0.1:{port}','transport_hash':CONTRACT_HASH,
                   'server_signer_public':_signing_public(signer),'ca_sha256':hashlib.sha256(ca_pem).hexdigest(),
                   'registry':pinned_registry,'clients':{}}
    for name,value in [('ca.pem',ca_pem),('ca-key.pem',_private(ca_key)),('server.pem',cert.public_bytes(serialization.Encoding.PEM)),
                       ('server-key.pem',_private(tls)),('server-signer.raw',signer.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption()))]:
        private_write(worker/name,value)
    document={k:configuration[k] for k in SCOPE_FIELDS+['endpoint','server_signer_public','ca_sha256','transport_hash']}
    private_write(secret/'ca.pem',ca_pem);_issue_client(secret,worker,configuration,document,1)
    private_write(worker/'worker.json',canonical(configuration));private_write(secret/'bundle.json',canonical(document))
    return {'worker_id':worker_id,'directory':str(worker),'config':str(worker/'worker.json'),
            'participant_id':participant_id,'role':actor['role'],'endpoint':configuration['endpoint'],'secret_reference':'secret://'+worker_id}


def _worker(identity):
    return identity.installation/'synthetic-participants'/identity.document['worker_id']


def rotate(directory,secret_reference):
    identity=PeerIdentity(directory,secret_reference);worker=_worker(identity)
    with identity_lock(worker):
        identity=PeerIdentity(directory,secret_reference);configuration=json.loads(private_read(worker/'worker.json'));document=dict(identity.document)
        _issue_client(identity.directory,worker,configuration,document,document['identity_epoch']+1)
        replace_private(worker/'worker.json',canonical(configuration));replace_private(identity.directory/'bundle.json',canonical(document))
    return {'previous_key_id':identity.document['client_key_id'],'current_key_id':document['client_key_id'],'identity_epoch':document['identity_epoch']}


def revoke(directory,secret_reference,key_id):
    identity=PeerIdentity(directory,secret_reference);worker=_worker(identity)
    with identity_lock(worker):
        configuration=json.loads(private_read(worker/'worker.json'))
        require(key_id in configuration['clients'],'NOT_FOUND','Unknown participant transport key.',404)
        configuration['clients'][key_id]['revoked']=True;replace_private(worker/'worker.json',canonical(configuration))


def synchronize_registry(app,worker):
    path=Path(worker['config'])
    with identity_lock(path.parent):
        configuration=json.loads(private_read(path));tenant,env=configuration['tenant_id'],configuration['environment_id']
        app.environment(tenant,env,True)
        with app.store.reader() as db:actor=app.upi.get(db,'upi_participants',tenant,env,configuration['participant_id'])
        require(actor['role']==configuration['role'],'PARTICIPANT_SCOPE','Participant role differs from its provisioned authority.',409)
        configuration['registry']=registry(app,tenant,env);replace_private(path,canonical(configuration))
    return {'keys':len(configuration['registry']),'registry_sha256':digest(configuration['registry'])}
