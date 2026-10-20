"""Restart only a recovery attempt's authenticated synthetic participant processes."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from . import peer_recovery as routing
from .errors import require
from .participant_peer_client import PeerClient
from .lab_identity import private_read
from .participant_peer_identity import PeerIdentity
from .private_config import read_document
from .recovery_native import owns_listener, process_identity, process_image
from .recovery_state import INACTIVE
from .release_bundle import sha, verify_installed
from .release_runtime import installation_lock, runtime_receipt, validate_request, require_deployment_capabilities, recovery_configuration
from .util import digest
from .recovery_banks import _runtime_image

IDENTITY_FILES = routing.IDENTITY_FILES


def _registration(directory, routes, worker_id):
    data = directory / 'data'
    path = data / 'synthetic-participants' / worker_id / 'recovery-process.json'
    if not path.exists() and not path.is_symlink(): return None
    record = routing.signed_read(data, path)
    require(record.get('format') == 'avenra-peer-recovery-process/1' and
            record.get('worker_id') == worker_id and record.get('set_id') == routes['set_id'] and
            record.get('origin') == routes['workers'][worker_id]['origin'] and
            record.get('routes_sha256') == sha(data / routing.FILE) and
            type(record.get('pid')) is int and record['pid'] > 1 and
            isinstance(record.get('process_identity'), str) and
            record['process_identity'].split(' | started=')[0] == routing.expected_command(directory,routes,worker_id),
            'RECOVERY_PEER_PROCESS', 'The participant ownership receipt belongs to another process, application or target.', 409)
    current = process_identity(record['pid'])
    require(current is None or current == record['process_identity'], 'RECOVERY_PROCESS_CHANGED',
            'A recorded participant PID now belongs to another process; it was not used or stopped.', 409)
    if current:
        require(process_image(record['pid'])==routes['process_executable'] and sha(routes['process_executable'])==routes['process_executable_sha256'],
                'RECOVERY_PEER_PROCESS','A participant executable image differs from its verified runtime image.',409)
    return record if current else None


def _clients(data, routes, worker_id):
    clients = []
    for path in sorted((data / 'participant-peer-secrets').glob('*/bundle.json')):
        document = read_document(path)
        if document['worker_id'] != worker_id: continue
        identity = PeerIdentity(data, 'secret://' + path.parent.name)
        require(routes['authorities'].get(identity.binding) == worker_id,
                'RECOVERY_PEER_BINDING', 'A restored client does not match the captured original participant authority.', 409)
        clients.append(PeerClient(identity))
    require(len(clients) == 1,
            'RECOVERY_PEER_BINDING', 'Each restored participant needs its original scoped client authority.', 409)
    return clients


def check(directory):
    directory = Path(directory).resolve(); data = directory / 'data'
    routes = routing.load(data)
    record = routing.signed_read(data, directory / 'peer-services.json')
    require(record.get('format') == 'avenra-recovery-peers/1' and record.get('set_id') == routes['set_id'] and
            record.get('routes_sha256') == sha(data / routing.FILE) and isinstance(record.get('services'), dict) and
            set(record['services']) == set(routes['workers']), 'RECOVERY_PEER_REQUIRED',
            'Complete this attempt’s owned participant startup before activation.', 409)
    for worker_id, worker in routes['workers'].items():
        routing.worker_binding(data, data / 'synthetic-participants' / worker_id / 'worker.json')
        process = _registration(directory, routes, worker_id)
        require(process and process == record['services'][worker_id] and
                owns_listener(process['pid'], routing.origin(worker['origin'])),
                'RECOVERY_PEER_PROCESS', 'A restored participant listener is not owned by its authenticated process.', 409)
        for client in _clients(data, routes, worker_id): client.probe()
    return record


def _plan(directory, stage, checked, ports, request_id, actor):
    from .participant_peer_checkpoint import validate
    data = directory / 'data'
    captured = validate(directory / 'payload/independent-participants')
    workers, identities = captured['workers'], captured['identities']
    require(0 < len(workers) <= 32 and isinstance(ports, dict) and
            set(ports) == {item['worker_id'] for item in workers},
            'RECOVERY_PEER_PLAN', 'Supply exactly one explicit port per captured participant.', 409)
    imported = read_document(directory / 'recovery-import.json')
    require(imported and imported['set_id'] == checked['set_id'] and
            all(sha(data / name) == hashed for name, hashed in imported['credential_hashes'].items()),
            'RECOVERY_DATABASE_TARGET', 'Participant startup requires unchanged imported database identities.', 409)
    intent = {'set_id': checked['set_id'], 'artifact_id': stage['artifact_id'], 'ports': ports,
              'request_id': request_id, 'actor': actor}
    existing = data / routing.FILE
    if existing.exists() or existing.is_symlink():
        routes = routing.load(data)
        require(routes['intent_hash'] == digest(intent), 'RECOVERY_CONFLICT',
                'Participant ports, actor or request changed; preserve the original attempt.', 409)
        return routes
    for relative in captured['files']: routing.private_materialized(data, data / relative)
    require(all(sha(data / relative) == hashed for relative, hashed in captured['files'].items()),
            'RECOVERY_PEER_BINDING', 'Staged participant identities or receipts changed before first startup.', 409)
    image, prefix = _runtime_image(directory / 'runtime')
    routes = {'format': routing.FORMAT, 'data_directory': str(data), 'set_id': checked['set_id'],
              'artifact_id': stage['artifact_id'], 'request_id': request_id, 'actor': actor,
              'intent_hash': digest(intent), 'control_dsn_sha256': sha(data / 'control.dsn'),
              'workers': {}, 'authorities': {}, 'created_at': time.time(),
              'process_executable': image, 'process_executable_sha256': sha(image), 'process_command_prefix': prefix}
    original_ports = set()
    for item in workers:
        worker_id = item['worker_id']; worker = data / 'synthetic-participants' / worker_id
        config = read_document(worker / 'worker.json')
        require(config['worker_id'] == worker_id, 'RECOVERY_PEER_BINDING', 'Captured directory and worker identity differ.', 409)
        original_ports.update([routing.origin(config['endpoint']), routing.origin(item['source_origin'])])
        port = ports[worker_id]
        require(type(port) is int and 1024 <= port <= 65535,
                'RECOVERY_PEER_PLAN', 'Use unprivileged integer loopback participant ports.', 409)
        certificate = x509.load_pem_x509_certificate(private_read(worker / 'server.pem'))
        routes['workers'][worker_id] = {'origin': 'https://127.0.0.1:' + str(port), 'logical_endpoint': config['endpoint'],
                                      'authority_hash': routing.binding(config),
                                      'identity_file_hashes': {name: sha(worker / name) for name in sorted(IDENTITY_FILES)},
                                      'server_certificate_sha256': certificate.fingerprint(hashes.SHA256()).hex()}
    occupied = set()
    native = read_document(directory / 'native-services.json', {})
    for service in native.get('services', {}).values(): occupied.add(service['port'])
    for route_file in ['bank-recovery.json', 'aa-recovery.json']:
        if not (data / route_file).exists(): continue
        if route_file == 'bank-recovery.json':
            from .bank_recovery import load
            occupied.update(routing.origin(worker['origin']) for worker in load(data)['workers'].values())
        else:
            from .aa.routing import load
            occupied.update(routing.origin(node['origin']) for node in load(data)['nodes'].values())
    require(len(set(ports.values())) == len(ports) and not set(ports.values()) & (original_ports | occupied),
            'RECOVERY_PEER_PLAN', 'Participant ports must differ from source and restored service ports.', 409)
    for item in identities:
        identity = PeerIdentity(data, 'secret://' + item['name'])
        require(identity.binding == item['authority_binding'] and identity.document['worker_id'] in routes['workers'],
                'RECOVERY_PEER_BINDING', 'Captured participant authority differs from its restored client.', 409)
        routes['authorities'][identity.binding] = identity.document['worker_id']
    routing.signed_write(data, existing, routes)
    return routes



def start(directory, ports, recovery_trust, release_trust, request_id, actor):
    from .recovery_restore import checked_stage
    directory, stage, checked = checked_stage(directory, recovery_trust, release_trust)
    validate_request(request_id, None, actor, 'activate'); data = directory / 'data'
    with installation_lock(directory):
        inactive = (data / INACTIVE).exists()
        if not inactive:
            from .private_config import deployment
            active = routing.signed_read(data, directory/'recovery-activation.json')
            policy = deployment(data).get('recovery',{})
            selection = read_document(data/'release-selection.json',{})
            require(active.get('set_id')==checked['set_id']==policy.get('set_id') and
                    active.get('artifact_id')==stage['artifact_id']==selection.get('artifact_id') and
                    policy.get('peer_routes_sha256')==sha(data/routing.FILE),
                    'RECOVERY_PEER_BINDING','Active participant restart requires the same authenticated recovery and selected application.',409)
        artifact = verify_installed(directory / 'artifacts' / stage['artifact_id'], release_trust)
        runtime_receipt(artifact, directory / 'runtime')
        require(any(item['path'] == 'avenra/peer_recovery.py' for item in artifact['manifest']['files']),
                'RECOVERY_RELEASE', 'The recovered application must implement authenticated participant routing.', 409)
        reused_plan = (data / routing.FILE).exists()
        routes = _plan(directory, stage, checked, ports, request_id, actor)
        planned = recovery_configuration(data,routes['set_id'])
        require_deployment_capabilities(data,artifact,planned)
        children = []
        try:
            services = {}
            for worker_id, worker in routes['workers'].items():
                registration = _registration(directory, routes, worker_id)
                if not registration:
                    port = routing.origin(worker['origin'])
                    with socket.socket() as listener:
                        try: listener.bind(('127.0.0.1', port))
                        except OSError: require(False, 'RECOVERY_PORT_BUSY', 'A participant port is occupied; no unrelated process was replaced.', 409)
                    path = data / 'synthetic-participants' / worker_id / 'recovery-service.log'
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, 'ab') as log:
                        child = subprocess.Popen(routing.process_command(directory, stage['artifact_id'], worker_id),
                                                 cwd=artifact['directory'], stdin=subprocess.DEVNULL, stdout=log,
                                                 stderr=subprocess.STDOUT, start_new_session=True)
                    children.append(child)
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    registration = _registration(directory, routes, worker_id)
                    if registration and owns_listener(registration['pid'], routing.origin(worker['origin'])):
                        for client in _clients(data, routes, worker_id): client.probe()
                        break
                    if children and children[-1].poll() is not None:
                        require(False, 'RECOVERY_PEER_START', 'A restored participant exited; inspect its private service log.', 409)
                    time.sleep(.1)
                else: raise TimeoutError('Restored participant did not establish its authenticated owned listener.')
                services[worker_id] = registration
            record = {'format': 'avenra-recovery-peers/1', 'set_id': checked['set_id'],
                      'routes_sha256': sha(data / routing.FILE), 'services': services, 'started_at': time.time()}
            routing.signed_write(data, directory / 'peer-services.json', record)
            check(directory)
            return {'started': True, 'reused_plan': reused_plan,
                    'services': {key: {'pid': value['pid'], 'origin': value['origin']} for key, value in services.items()}}
        except BaseException:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                    try: child.wait(timeout=15)
                    except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
            raise


def stop(directory):
    directory = Path(directory).resolve(); data = directory / 'data'; routes = routing.load(data)
    with installation_lock(directory):
        for worker_id in reversed(list(routes['workers'])):
            record = _registration(directory, routes, worker_id)
            if not record: continue
            os.kill(record['pid'], signal.SIGTERM); deadline = time.monotonic() + 20
            while time.monotonic() < deadline and process_identity(record['pid']) == record['process_identity']:
                try:
                    done, _ = os.waitpid(record['pid'], os.WNOHANG)
                    if done: break
                except ChildProcessError: pass
                time.sleep(.1)
            require(process_identity(record['pid']) != record['process_identity'], 'RECOVERY_PROCESS_DRAIN', 'Owned participant process is still draining.', 409)
        if (directory / 'peer-services.json').exists():
            os.replace(directory / 'peer-services.json', directory / ('peer-services-stopped-' + str(time.time_ns()) + '.json'))
    return {'stopped': True}
