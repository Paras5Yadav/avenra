"""Restart only a recovery attempt's authenticated synthetic bank processes."""
import hashlib
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from . import bank_recovery as routing
from .errors import require
from .lab_bank_client import LabBankClient
from .lab_identity import LabIdentity, private_read
from .private_config import read_document
from .recovery_native import check as native_check, owns_listener, process_identity, process_image
from .recovery_state import INACTIVE
from .release_bundle import sha, verify_installed
from .release_runtime import installation_lock, runtime_receipt, validate_request, require_deployment_capabilities, recovery_configuration
from .util import digest

IDENTITY_FILES = routing.IDENTITY_FILES


def _registration(directory, routes, worker_id):
    data = directory / 'data'
    path = data / 'synthetic-banks' / worker_id / 'recovery-process.json'
    if not path.exists() and not path.is_symlink(): return None
    record = routing.signed_read(data, path)
    require(record.get('format') == 'avenra-bank-recovery-process/1' and
            record.get('worker_id') == worker_id and record.get('set_id') == routes['set_id'] and
            record.get('origin') == routes['workers'][worker_id]['origin'] and
            record.get('routes_sha256') == sha(data / routing.FILE) and
            type(record.get('pid')) is int and record['pid'] > 1 and
            isinstance(record.get('process_identity'), str) and
            record['process_identity'].split(' | started=')[0] == routing.expected_command(directory,routes,worker_id),
            'RECOVERY_BANK_PROCESS', 'The bank ownership receipt belongs to another process, application or target.', 409)
    current = process_identity(record['pid'])
    require(current is None or current == record['process_identity'], 'RECOVERY_PROCESS_CHANGED',
            'A recorded bank PID now belongs to another process; it was not used or stopped.', 409)
    if current:
        require(process_image(record['pid'])==routes['process_executable'] and sha(routes['process_executable'])==routes['process_executable_sha256'],
                'RECOVERY_BANK_PROCESS','A bank executable image differs from its verified runtime image.',409)
    return record if current else None


def _clients(data, routes, worker_id):
    clients = []
    for path in sorted((data / 'participant-secrets').glob('*/bundle.json')):
        document = read_document(path)
        if document['worker_id'] != worker_id: continue
        identity = LabIdentity(data, 'secret://' + path.parent.name)
        require(routes['authorities'].get(identity.binding) == worker_id,
                'RECOVERY_BANK_BINDING', 'A restored client does not match the captured original bank authority.', 409)
        clients.append(LabBankClient(identity))
    require(len(clients) == 2 and {client.document['role'] for client in clients} == {'remitter', 'beneficiary'},
            'RECOVERY_BANK_BINDING', 'Each restored bank needs its original two scoped client authorities.', 409)
    return clients


def check(directory):
    directory = Path(directory).resolve(); data = directory / 'data'
    routes = routing.load(data)
    native_check(directory, routes['accounting_origin'], routes['fault_origin'])
    record = routing.signed_read(data, directory / 'bank-services.json')
    require(record.get('format') == 'avenra-recovery-banks/1' and record.get('set_id') == routes['set_id'] and
            record.get('routes_sha256') == sha(data / routing.FILE) and isinstance(record.get('services'), dict) and
            set(record['services']) == set(routes['workers']), 'RECOVERY_BANK_REQUIRED',
            'Complete this attempt’s owned bank startup before activation.', 409)
    for worker_id, worker in routes['workers'].items():
        routing.worker_binding(data, data / 'synthetic-banks' / worker_id / 'worker.json')
        process = _registration(directory, routes, worker_id)
        require(process and process == record['services'][worker_id] and
                owns_listener(process['pid'], routing.origin(worker['origin'])),
                'RECOVERY_BANK_PROCESS', 'A restored bank listener is not owned by its authenticated process.', 409)
        for client in _clients(data, routes, worker_id): client.probe()
    return record


def _plan(directory, stage, checked, ports, request_id, actor):
    data = directory / 'data'
    captured = read_document(directory / 'payload/synthetic-banks/manifest.json')
    workers, identities = captured['workers'], captured['identities']
    require(0 < len(workers) <= 32 and len(identities) == 2 * len(workers) and
            isinstance(ports, dict) and set(ports) == {item['worker_id'] for item in workers},
            'RECOVERY_BANK_PLAN', 'Supply exactly one explicit port for every captured synthetic bank, within the recovery bounds.', 409)
    native = read_document(directory / 'native-services.json')
    require(native, 'RECOVERY_NATIVE_REQUIRED', 'Start owned restored native services before bank services.', 409)
    accounting = native['services']['accounting']['origin']; fault = native['services']['fault']['origin']
    native_check(directory, accounting, fault)
    imported = read_document(directory / 'recovery-import.json')
    require(imported and imported['set_id'] == checked['set_id'] and
            all(sha(data / name) == hashed for name, hashed in imported['credential_hashes'].items()),
            'RECOVERY_DATABASE_TARGET', 'Bank startup requires the unchanged imported target identities.', 409)
    intent = {'set_id': checked['set_id'], 'artifact_id': stage['artifact_id'], 'ports': ports,
              'accounting_origin': accounting, 'fault_origin': fault, 'request_id': request_id, 'actor': actor}
    existing = data / routing.FILE
    if existing.exists() or existing.is_symlink():
        routes = routing.load(data)
        require(routes['intent_hash'] == digest(intent), 'RECOVERY_CONFLICT', 'Bank ports, actor, request or native bindings changed; preserve the original attempt.', 409)
        return routes
    # Before the first start, every copied private byte must still match the
    # externally signed source checkpoint. Subsequent receipt/rotation writes
    # are checked against stable authority and server identity pins instead.
    for relative in captured['files']: routing.private_materialized(data,data/relative)
    require(all(sha(data / relative) == expected for relative, expected in captured['files'].items()),
            'RECOVERY_BANK_BINDING', 'Staged bank identities or receipts changed before the first start.', 409)
    image,prefix=_runtime_image(directory/'runtime')
    routes = {'format': routing.FORMAT, 'data_directory': str(data), 'set_id': checked['set_id'],
              'artifact_id': stage['artifact_id'], 'request_id': request_id, 'actor': actor,
              'intent_hash': digest(intent), 'accounting_origin': accounting, 'fault_origin': fault,
              'accounting_dsn_sha256': sha(data / 'accounting.dsn'), 'workers': {}, 'authorities': {}, 'created_at': time.time(),
              'process_executable':image,'process_executable_sha256':sha(image),'process_command_prefix':prefix}
    original_ports = set()
    for item in workers:
        worker_id = item['worker_id']; worker = data / 'synthetic-banks' / item['directory']
        config = read_document(worker / 'worker.json')
        require(config['worker_id'] == worker_id == item['directory'], 'RECOVERY_BANK_BINDING', 'Captured bank directory and worker identity differ.', 409)
        original_ports.add(routing.origin(config['endpoint']))
        port = ports[worker_id]
        require(type(port) is int and 1024 <= port <= 65535, 'RECOVERY_BANK_PLAN', 'Use unprivileged integer loopback bank ports.', 409)
        certificate = x509.load_pem_x509_certificate(private_read(worker / 'server.pem'))
        routes['workers'][worker_id] = {'origin': 'https://127.0.0.1:' + str(port), 'logical_endpoint': config['endpoint'],
                                      'authority_hash': routing.authority(config),
                                      'identity_file_hashes': {name: sha(worker / name) for name in sorted(IDENTITY_FILES)},
                                      'server_certificate_sha256': certificate.fingerprint(hashes.SHA256()).hex()}
    require(len(set(ports.values())) == len(ports) and not set(ports.values()) & original_ports and
            not set(ports.values()) & {native['services'][role]['port'] for role in ['accounting', 'fault']},
            'RECOVERY_BANK_PLAN', 'Use distinct new bank ports, separate from original banks and restored native services.', 409)
    for item in identities:
        identity = LabIdentity(data, 'secret://' + item['name'])
        require(identity.binding == item['authority_binding'] and identity.document['worker_id'] in routes['workers'],
                'RECOVERY_BANK_BINDING', 'Captured bank role authority does not match its restored private identity.', 409)
        routes['authorities'][identity.binding] = identity.document['worker_id']
    routing.signed_write(data, existing, routes)
    return routes


def _runtime_image(runtime):
    """Observe the OS image of this already verified isolated Python runtime.

    macOS framework Python can exec its application image; Linux commonly keeps
    the venv argv[0]. Neither behavior is inferred from an arbitrary PID.
    """
    arguments=[str(runtime/'bin/python'),'-I','-B','-c','import time;time.sleep(10)']
    child=subprocess.Popen(arguments,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+3; previous=None
        while time.monotonic()<deadline:
            identity=process_identity(child.pid);image=process_image(child.pid)
            suffix=' '+' '.join(arguments[1:])
            if identity and image:
                command=identity.split(' | started=')[0]
                if command.endswith(suffix):
                    prefix=command[:-len(suffix)]
                    require(prefix==arguments[0] or Path(prefix).resolve()==Path(image).resolve(),
                            'RECOVERY_BANK_PROCESS','Verified Python probe returned an unrelated command image.',409)
                    if previous==(image,prefix):return image,prefix
                    previous=(image,prefix)
            require(child.poll() is None,'RECOVERY_BANK_PROCESS','Verified Python probe exited before image inspection.',409)
            time.sleep(.1)
        raise TimeoutError('Verified Python runtime did not expose a stable executable identity.')
    finally:
        if child.poll() is None:child.terminate()
        child.wait(timeout=5)


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
                    policy.get('bank_routes_sha256')==sha(data/routing.FILE),
                    'RECOVERY_BANK_BINDING','Active bank restart requires the same authenticated recovery and selected application.',409)
        artifact = verify_installed(directory / 'artifacts' / stage['artifact_id'], release_trust)
        runtime_receipt(artifact, directory / 'runtime')
        require(any(item['path'] == 'avenra/bank_recovery.py' for item in artifact['manifest']['files']),
                'RECOVERY_RELEASE', 'The recovered application must implement authenticated bank routing.', 409)
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
                        except OSError: require(False, 'RECOVERY_PORT_BUSY', 'A bank port is occupied; no unrelated process was replaced.', 409)
                    path = data / 'synthetic-banks' / worker_id / 'recovery-service.log'
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
                        require(False, 'RECOVERY_BANK_START', 'A restored bank exited; inspect its private service log.', 409)
                    time.sleep(.1)
                else: raise TimeoutError('Restored bank did not establish its authenticated owned listener.')
                services[worker_id] = registration
            record = {'format': 'avenra-recovery-banks/1', 'set_id': checked['set_id'],
                      'routes_sha256': sha(data / routing.FILE), 'services': services, 'started_at': time.time()}
            routing.signed_write(data, directory / 'bank-services.json', record)
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
            require(process_identity(record['pid']) != record['process_identity'], 'RECOVERY_PROCESS_DRAIN', 'Owned bank process is still draining.', 409)
        if (directory / 'bank-services.json').exists():
            os.replace(directory / 'bank-services.json', directory / ('bank-services-stopped-' + str(time.time_ns()) + '.json'))
    return {'stopped': True}
