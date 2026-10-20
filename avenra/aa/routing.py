"""Owner-authenticated physical AA routes; immutable logical profiles stay intact."""
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
import sys

from ..bank_recovery import origin
from ..errors import require
from ..lab_identity import private_read
from ..private_config import read_document, write_document
from ..util import canonical, digest
from .runtime import location

FILE = 'aa-recovery.json'
FORMAT = 'avenra-aa-recovery-routes/1'


def signed_write(data, path, value):
    raw = canonical(value).encode()
    require(len(raw) < 60_000, 'RECOVERY_AA_CAPACITY', 'The AA recovery plan exceeds its declared document budget.', 409)
    signature = hmac.new(private_read(Path(data) / 'evidence.key'), raw, hashlib.sha256).hexdigest()
    write_document(path, {**value, 'signature': signature})


def signed_read(data, path):
    value = read_document(path)
    require(value and isinstance(value.get('signature'), str), 'RECOVERY_AA_AUTHENTICATION', 'AA recovery state needs installation authentication.', 409)
    body = {key: item for key, item in value.items() if key != 'signature'}
    expected = hmac.new(private_read(Path(data) / 'evidence.key'), canonical(body).encode(), hashlib.sha256).hexdigest()
    require(hmac.compare_digest(value['signature'], expected), 'RECOVERY_AA_AUTHENTICATION', 'AA recovery state failed installation authentication.', 409)
    return body


def relative(value):
    path = Path(value) if isinstance(value, str) else Path('/')
    return isinstance(value, str) and bool(path.parts) and not path.is_absolute() and '..' not in path.parts and path.as_posix() == value


def private_file(data, path):
    data, path = Path(data).absolute(), Path(path).absolute()
    require(path.is_relative_to(data), 'RECOVERY_AA_BINDING', 'AA private files must remain under the recovered installation.', 409)
    current = data
    for part in [None, *path.relative_to(data).parts[:-1]]:
        if part is not None: current = current / part
        info = current.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
                'RECOVERY_AA_BINDING', 'AA recovery parents must be private ordinary directories.', 409)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077 and
            info.st_nlink == 1 and info.st_size <= 32_000_000,
            'RECOVERY_AA_BINDING', 'AA recovery inputs must be private single-link ordinary files.', 409)


def load(data):
    data = Path(data).absolute()
    value = signed_read(data, data / FILE)
    required = {'format','data_directory','set_id','artifact_id','request_id','actor','intent_hash','created_at',
                'accounting_origin','fault_origin','api_origin','accounting_dsn_sha256','nodes','clients','files',
                'process_executable','process_executable_sha256','process_command_prefix'}
    require(set(value) == required and value['format'] == FORMAT and value['data_directory'] == str(data) and
            value['accounting_dsn_sha256'] == hashlib.sha256(private_read(data / 'accounting.dsn')).hexdigest(),
            'RECOVERY_AA_BINDING', 'AA recovery routes belong to another target or database.', 409)
    for key in ['set_id','artifact_id','intent_hash','accounting_dsn_sha256','process_executable_sha256']:
        require(isinstance(value[key],str) and re.fullmatch('[0-9a-f]{64}',value[key]), 'RECOVERY_AA_BINDING', 'Invalid AA recovery identity pin.', 409)
    for key in ['process_executable','process_command_prefix']:
        require(isinstance(value[key],str) and Path(value[key]).is_absolute(), 'RECOVERY_AA_BINDING', 'Pin the verified runtime executable.', 409)
    for key in ['accounting_origin','fault_origin','api_origin']: origin(value[key], 'http')
    require(isinstance(value['nodes'],dict) and 0 < len(value['nodes']) <= 32 and
            isinstance(value['files'],dict) and 0 < len(value['files']) <= 2000 and
            isinstance(value['clients'],dict) and 0 < len(value['clients']) <= 64,
            'RECOVERY_AA_CAPACITY', 'AA route inventories are incomplete or exceed this profile.', 409)
    for node_id, node in value['nodes'].items():
        require(isinstance(node_id,str) and re.fullmatch('[A-Za-z0-9_]{1,100}/[A-Za-z0-9_]{1,100}',node_id) and
                isinstance(node,dict) and set(node)=={'config','role','origin','logical_origin','config_sha256','server_certificate_sha256'} and
                relative(node['config']) and node['config'].startswith('aa-scopes/' + node_id.split('/')[0] + '/') and
                node['role'] in {'aa','fip','fiu'} and
                all(isinstance(node[key],str) and re.fullmatch('[0-9a-f]{64}',node[key]) for key in ['config_sha256','server_certificate_sha256']),
                'RECOVERY_AA_BINDING', 'Invalid captured AA listener authority.', 409)
        origin(node['origin']); origin(node['logical_origin'])
    for path, hashed in value['files'].items():
        require(relative(path) and path.startswith('aa-scopes/') and isinstance(hashed,str) and re.fullmatch('[0-9a-f]{64}',hashed),
                'RECOVERY_AA_BINDING', 'Invalid AA immutable identity inventory.', 409)
    for path, clients in value['clients'].items():
        require(relative(path) and path.startswith('aa-scopes/') and isinstance(clients,dict) and clients and
                all(isinstance(key,str) and re.fullmatch('[0-9a-f]{64}',key) and node in value['nodes'] for key,node in clients.items()),
                'RECOVERY_AA_BINDING', 'Invalid AA client authority inventory.', 409)
    from ..recovery_state import INACTIVE
    if not (data / INACTIVE).exists():
        policy = read_document(data / 'deployment.json', {}).get('recovery', {})
        require(policy.get('set_id') == value['set_id'] and policy.get('aa_routes_sha256') == hashlib.sha256(private_read(data / FILE)).hexdigest(),
                'RECOVERY_AA_BINDING', 'Activated AA routes must match the deployment’s pinned recovery decision.', 409)
    return value


def verify_files(data, routes):
    data = Path(data).absolute()
    for name, expected in routes['files'].items():
        path = data / name; private_file(data, path)
        require(hashlib.sha256(private_read(path,32_000_000)).hexdigest()==expected,
                'RECOVERY_AA_BINDING', 'A captured AA profile, contract or cryptographic identity changed.', 409)
    for node in routes['nodes'].values():
        state = (data / node['config']).parent / 'role.db'
        if node['role'] != 'aa': private_file(data, state)


def resolve(directory, config):
    located = location(directory)
    if not located: return None
    data, _ = located
    from ..recovery_state import INACTIVE
    policy = read_document(data / 'deployment.json', {}).get('recovery', {})
    path = data / FILE
    if not path.exists() and not path.is_symlink():
        require(not (data / INACTIVE).exists() and not (data / 'aa-recovery-inactive.json').exists() and not policy.get('aa_routes_sha256'),
                'RECOVERY_AA_REQUIRED', 'Recovered AA clients require explicit routes; source fallback is refused.', 409)
        return None
    routes = load(data)
    directory = Path(directory).absolute()
    clients = routes['clients'].get(directory.relative_to(data).as_posix(), {})
    node_id = clients.get(digest(config));node = routes['nodes'].get(node_id)
    require(node and node['logical_origin']==config['origin'], 'RECOVERY_AA_BINDING',
            'The original AA client authority has no approved replacement route.', 409)
    return {**node, 'set_id':routes['set_id']}


def node_binding(data, config):
    data, config = Path(data).absolute(), Path(config).absolute();routes = load(data)
    matches = [(name,node) for name,node in routes['nodes'].items() if data/node['config']==config]
    require(len(matches)==1, 'RECOVERY_AA_BINDING', 'The listener is not in the captured AA topology.', 409)
    node_id,node = matches[0];private_file(data,config)
    require(hashlib.sha256(private_read(config)).hexdigest()==node['config_sha256'],
            'RECOVERY_AA_BINDING', 'The original AA listener configuration changed.', 409)
    return routes,node_id,node


def process_command(directory, routes, node_id):
    directory = Path(directory).absolute();node = routes['nodes'][node_id]
    module = 'avenra.service_gateway' if node['role']=='aa' else 'avenra.aa.peer'
    code = 'import sys;sys.path.insert(0,sys.argv.pop(1));from '+module+' import main;main()'
    command = [str(directory/'runtime/bin/python'),'-I','-B','-c',code,str(directory/'artifacts'/routes['artifact_id']),
               '--config',str(directory/'data'/node['config']),'--recovery-data',str(directory/'data')]
    if node['role']=='aa':command.extend(['--data',str((directory/'data'/node['config']).parent)])
    return command


def expected_command(directory, routes, node_id):
    return ' '.join([routes['process_command_prefix'],*process_command(directory,routes,node_id)[1:]])


def register_process(data, config):
    from ..recovery_native import process_identity,process_image
    data = Path(data).absolute();routes,node_id,node = node_binding(data,config)
    verify_files(data,routes)
    identity = process_identity(os.getpid())
    require(identity and identity.split(' | started=')[0]==expected_command(data.parent,routes,node_id) and
            Path(sys.prefix).resolve()==data.parent/'runtime' and process_image(os.getpid())==routes['process_executable'] and
            hashlib.sha256(Path(routes['process_executable']).read_bytes()).hexdigest()==routes['process_executable_sha256'],
            'RECOVERY_AA_PROCESS', 'AA recovery listeners must run from their verified isolated application runtime.', 409)
    record = {'format':'avenra-aa-recovery-process/1','node_id':node_id,'set_id':routes['set_id'],
              'routes_sha256':hashlib.sha256(private_read(data/FILE)).hexdigest(),'pid':os.getpid(),
              'process_identity':identity,'origin':node['origin']}
    signed_write(data,Path(config).parent/'recovery-process.json',record)
    return routes,node
