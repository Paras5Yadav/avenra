"""Restart a captured AA topology through pinned, owned replacement listeners."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from .aa import routing
from .aa.runtime import nodes
from .aa.wire import Client
from .bank_recovery import origin
from .deployment_identity import client_context, paths
from .errors import require
from .lab_identity import private_read
from .private_config import read_document
from .recovery_banks import _runtime_image
from .recovery_native import check as native_check, owns_listener, process_identity, process_image
from .recovery_state import INACTIVE
from .release_bundle import sha, verify_installed
from .release_runtime import installation_lock, runtime_receipt, validate_request, require_deployment_capabilities, recovery_configuration
from .util import digest


def _registration(directory, routes, node_id):
    data = directory / 'data';node = routes['nodes'][node_id]
    path = (data / node['config']).parent / 'recovery-process.json'
    if not path.exists() and not path.is_symlink():return None
    record = routing.signed_read(data,path)
    require(set(record)=={'format','node_id','set_id','routes_sha256','pid','process_identity','origin'} and
            record['format']=='avenra-aa-recovery-process/1' and record['node_id']==node_id and
            record['set_id']==routes['set_id'] and record['routes_sha256']==sha(data/routing.FILE) and
            record['origin']==node['origin'] and type(record['pid']) is int and record['pid']>1 and
            isinstance(record['process_identity'],str) and
            record['process_identity'].split(' | started=')[0]==routing.expected_command(directory,routes,node_id),
            'RECOVERY_AA_PROCESS','AA process ownership does not match the original startup intent.',409)
    current = process_identity(record['pid'])
    require(current is None or current==record['process_identity'],'RECOVERY_PROCESS_CHANGED','An AA PID was reused; the unrelated process was neither used nor stopped.',409)
    if current:
        require(process_image(record['pid'])==routes['process_executable'] and sha(routes['process_executable'])==routes['process_executable_sha256'],
                'RECOVERY_AA_PROCESS','The AA process executable differs from its verified offline runtime.',409)
    return record if current else None


def _probe(data, routes, node_id):
    node = routes['nodes'][node_id];env = node_id.split('/')[0];scope = data/'aa-scopes'/env
    profile = read_document(scope/'profile.json')
    if node['role']=='aa':
        first = next(name for name in routes['nodes'] if name.startswith(env+'/') and routes['nodes'][name]['role']!='aa')
        config = data/routes['nodes'][first]['config'];peer = read_document(config)
        context = client_context(config.parent,peer['aa']['identity_reference'])
        with socket.create_connection(('127.0.0.1',origin(node['origin'])),timeout=3) as transport:
            with context.wrap_socket(transport,server_hostname='127.0.0.1') as channel:
                import hashlib
                require(hashlib.sha256(channel.getpeercert(binary_form=True)).hexdigest()==node['server_certificate_sha256'],
                        'RECOVERY_AA_BINDING','AA ingress does not hold its captured TLS authority.',409)
    else:
        key = serialization.load_pem_private_key(private_read(scope/'signing-key.pem'),password=None)
        config = profile['peers'][node_id.split('/')[1]]
        response = Client(scope,config,key,profile['kid'],config['role']).call('POST','/control/status',{},control=True,timeout=3)
        require(response.get('role')==node['role'] and response.get('classification')=='synthetic',
                'RECOVERY_AA_BINDING','The original signed participant did not answer at its recovered address.',409)


def check(directory):
    directory = Path(directory).absolute();data = directory/'data';routes = routing.load(data)
    native_check(directory,routes['accounting_origin'],routes['fault_origin']);routing.verify_files(data,routes)
    record = routing.signed_read(data,directory/'aa-services.json')
    require(record.get('format')=='avenra-recovery-aa/1' and record.get('set_id')==routes['set_id'] and
            record.get('routes_sha256')==sha(data/routing.FILE) and isinstance(record.get('services'),dict) and
            set(record['services'])==set(routes['nodes']), 'RECOVERY_AA_REQUIRED','Start the complete captured AA topology before activation.',409)
    for node_id,node in routes['nodes'].items():
        process = _registration(directory,routes,node_id)
        require(process and process==record['services'][node_id] and owns_listener(process['pid'],origin(node['origin'])),
                'RECOVERY_AA_PROCESS','An AA replacement listener is not owned by its authenticated process.',409)
        _probe(data,routes,node_id)
    return record


def _plan(directory, stage, checked, ports, api_origin, request_id, actor):
    data = directory/'data';captured = read_document(directory/'payload/financial-data/manifest.json')
    configurations = nodes(data)
    require(captured and captured['files'] and configurations and isinstance(ports,dict) and set(ports)==set(configurations),
            'RECOVERY_AA_PLAN','Supply exactly one replacement port for every captured AA ingress and participant.',409)
    api_port = origin(api_origin,'http')
    native = read_document(directory/'native-services.json')
    require(native,'RECOVERY_NATIVE_REQUIRED','Start owned restored native services before AA participants.',409)
    accounting = native['services']['accounting']['origin'];fault = native['services']['fault']['origin']
    native_check(directory,accounting,fault)
    imported = read_document(directory/'recovery-import.json')
    require(imported and imported['set_id']==checked['set_id'] and all(sha(data/name)==expected for name,expected in imported['credential_hashes'].items()),
            'RECOVERY_DATABASE_TARGET','AA startup requires the unchanged newly imported database identities.',409)
    intent = {'set_id':checked['set_id'],'artifact_id':stage['artifact_id'],'ports':ports,'api_origin':api_origin,
              'accounting_origin':accounting,'fault_origin':fault,'request_id':request_id,'actor':actor}
    if (data/routing.FILE).exists() or (data/routing.FILE).is_symlink():
        routes = routing.load(data)
        require(routes['intent_hash']==digest(intent),'RECOVERY_CONFLICT','Keep the original AA ports, upstream, request and actor on retry.',409)
        routing.verify_files(data,routes)
        return routes
    inventory = {}
    for entry in captured['files']:
        relative = 'aa-scopes/'+entry['path'];path = data/relative
        routing.private_file(data,path)
        require(path.stat().st_size==entry['bytes'] and sha(path)==entry['sha256'],
                'RECOVERY_AA_BINDING','Captured AA state changed before its first coordinated start.',409)
        if not relative.endswith('/role.db'):inventory[relative]=entry['sha256']
    image,prefix = _runtime_image(directory/'runtime')
    routes = {'format':routing.FORMAT,'data_directory':str(data),'set_id':checked['set_id'],'artifact_id':stage['artifact_id'],
              'request_id':request_id,'actor':actor,'intent_hash':digest(intent),'created_at':time.time(),
              'accounting_origin':accounting,'fault_origin':fault,'api_origin':api_origin,'accounting_dsn_sha256':sha(data/'accounting.dsn'),
              'process_executable':image,'process_executable_sha256':sha(image),'process_command_prefix':prefix,'nodes':{},'clients':{},'files':inventory}
    original_ports = set()
    for node_id,path in configurations.items():
        env,name = node_id.split('/');config = read_document(path);scope = data/'aa-scopes'/env;profile = read_document(scope/'profile.json')
        role = 'aa' if name=='ingress' else profile['peers'][name]['role']
        logical_origin = 'https://127.0.0.1:'+str(config['port'])
        original_ports.add(origin(logical_origin))
        require(type(ports[node_id]) is int and 1024<=ports[node_id]<=65535,'RECOVERY_AA_PLAN','Use explicit unprivileged integer AA listener ports.',409)
        if role=='aa':
            require(config.get('service')=='aa' and config.get('prefix')=='/aa/'+profile['tenant_id']+'/'+env,
                    'RECOVERY_AA_BINDING','The captured ingress must retain its exact original tenant/environment prefix.',409)
            original_ports.add(origin(config['upstream'],'http'))
        else:
            peer = profile['peers'][name]
            require(config.get('id')==name and config.get('role')==role and config.get('aa_id')==profile['id'] and
                    peer['origin']==logical_origin and config['kid']==peer['kid'] and config['peer_kid']==profile['kid'] and
                    config['aa']['kid']==profile['kid'] and config['aa']['prefix']=='/aa/'+profile['tenant_id']+'/'+env,
                    'RECOVERY_AA_BINDING','The captured AA participant and profile authority disagree.',409)
            routes['clients'].setdefault(scope.relative_to(data).as_posix(),{})[digest(peer)]=node_id
            routes['clients'][path.parent.relative_to(data).as_posix()]={digest(config['aa']):env+'/ingress'}
        certificate = x509.load_pem_x509_certificate(private_read(paths(path.parent,config['identity_reference'])['cert']))
        routes['nodes'][node_id]={'config':path.relative_to(data).as_posix(),'role':role,'origin':'https://127.0.0.1:'+str(ports[node_id]),
                                 'logical_origin':logical_origin,'config_sha256':sha(path),'server_certificate_sha256':certificate.fingerprint(hashes.SHA256()).hex()}
    for path in configurations.values():
        config = read_document(path)
        if config.get('role') in {'fip','fiu'}:
            env = path.relative_to(data/'aa-scopes').parts[0]
            require(config['aa']['origin']==routes['nodes'][env+'/ingress']['logical_origin'],
                    'RECOVERY_AA_BINDING','The captured participant must retain its original AA ingress authority.',409)
    reserved = {origin(accounting,'http'),origin(fault,'http')}
    banks = read_document(directory/'bank-services.json')
    if banks:reserved.update(origin(item['origin']) for item in banks['services'].values())
    require(len(set(ports.values()))==len(ports) and not set(ports.values()) & (original_ports|reserved|{api_port}) and
            api_port not in original_ports|reserved,'RECOVERY_AA_PLAN','Use distinct new AA/API ports, separate from original and recovered native/bank listeners.',409)
    routing.signed_write(data,data/routing.FILE,routes)
    return routes


def start(directory, ports, api_origin, recovery_trust, release_trust, request_id, actor):
    from .recovery_restore import checked_stage
    directory,stage,checked = checked_stage(directory,recovery_trust,release_trust)
    validate_request(request_id,None,actor,'activate');data = directory/'data'
    with installation_lock(directory):
        if not (data/INACTIVE).exists():
            from .private_config import deployment
            activation = read_document(directory/'recovery-activation.json',{})
            policy = deployment(data).get('recovery',{});selection = read_document(data/'release-selection.json',{})
            require(activation.get('set_id')==checked['set_id']==policy.get('set_id') and
                    activation.get('artifact_id')==stage['artifact_id']==selection.get('artifact_id') and
                    policy.get('aa_routes_sha256')==sha(data/routing.FILE),
                    'RECOVERY_AA_BINDING','AA restart requires the same activated recovery and selected application.',409)
        artifact = verify_installed(directory/'artifacts'/stage['artifact_id'],release_trust);runtime_receipt(artifact,directory/'runtime')
        reused = (data/routing.FILE).exists()
        routes = _plan(directory,stage,checked,ports,api_origin,request_id,actor)
        planned = recovery_configuration(data,checked['set_id'])
        require_deployment_capabilities(data,artifact,planned)
        children = []
        try:
            services = {}
            for node_id,node in routes['nodes'].items():
                process = _registration(directory,routes,node_id);child = None
                if not process:
                    with socket.socket() as listener:
                        try:listener.bind(('127.0.0.1',origin(node['origin'])))
                        except OSError:require(False,'RECOVERY_PORT_BUSY','An AA replacement port is occupied; no unrelated process was changed.',409)
                    path = (data/node['config']).parent/'recovery-service.log'
                    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
                    with os.fdopen(fd,'ab') as log:
                        child = subprocess.Popen(routing.process_command(directory,routes,node_id),cwd=artifact['directory'],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    children.append(child)
                deadline = time.monotonic()+25
                while time.monotonic()<deadline:
                    process = _registration(directory,routes,node_id)
                    if process and owns_listener(process['pid'],origin(node['origin'])):
                        _probe(data,routes,node_id);break
                    require(child is None or child.poll() is None,'RECOVERY_AA_START','A recovered AA listener exited; inspect its private log.',409)
                    time.sleep(.1)
                else:raise TimeoutError('A recovered AA participant did not establish its authenticated owned listener.')
                services[node_id] = process
            routing.signed_write(data,directory/'aa-services.json',{'format':'avenra-recovery-aa/1','set_id':checked['set_id'],
                'routes_sha256':sha(data/routing.FILE),'services':services,'started_at':time.time()})
            check(directory)
            return {'started':True,'reused_plan':reused,'services':{name:{'pid':item['pid'],'origin':item['origin']} for name,item in services.items()}}
        except BaseException:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                    try:child.wait(timeout=15)
                    except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
            raise


def stop(directory):
    directory = Path(directory).absolute();data = directory/'data';routes = routing.load(data)
    with installation_lock(directory):
        for node_id in reversed(list(routes['nodes'])):
            record = _registration(directory,routes,node_id)
            if not record:continue
            os.kill(record['pid'],signal.SIGTERM);deadline = time.monotonic()+20
            while time.monotonic()<deadline and process_identity(record['pid'])==record['process_identity']:
                try:
                    done,_ = os.waitpid(record['pid'],os.WNOHANG)
                    if done:break
                except ChildProcessError:pass
                time.sleep(.1)
            require(process_identity(record['pid'])!=record['process_identity'],'RECOVERY_PROCESS_DRAIN','The owned AA listener is still draining.',409)
        if (directory/'aa-services.json').exists():os.replace(directory/'aa-services.json',directory/('aa-services-stopped-'+str(time.time_ns())+'.json'))
    return {'stopped':True}
