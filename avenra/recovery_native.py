"""Owned native processes for a staged local recovery; no source services stopped."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

from .errors import require
from .gateways import AccountingGateway, FaultGateway
from .lab_identity import private_read
from .private_config import read_document, write_document
from .release_bundle import sha
from .util import canonical


def process_identity(pid):
    environment={**os.environ,'LC_ALL':'C'}
    def read(field,wide=False):
        arguments=['ps',*(['-ww'] if wide else []),'-p',str(pid),'-o',field+'=']
        result=subprocess.run(arguments,capture_output=True,timeout=5,check=False,env=environment)
        return result.stdout.decode().strip() if result.returncode==0 else None
    try:
        # On macOS, even -ww truncates command when another column follows it.
        # Read it alone and bracket that read with an unchanged birth stamp.
        before=read('lstart');command=read('command',True);after=read('lstart')
    except OSError: return None
    return command+' | started='+before if before and command and before==after else None


def process_image(pid):
    """Read the executable image from the OS, independently of argv[0]."""
    if sys.platform == 'darwin':
        import ctypes
        library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        library.proc_pidpath.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(4096)
        count = library.proc_pidpath(pid, buffer, len(buffer))
        return buffer.value.decode() if count > 0 else None
    if sys.platform.startswith('linux'):
        try: return os.readlink('/proc/' + str(pid) + '/exe')
        except OSError: return None
    require(False, 'RECOVERY_HOST', 'Bank recovery requires qualified executable-image inspection on this host.', 409)


def owns_listener(pid, port):
    command = shutil.which('lsof')
    require(command, 'RECOVERY_HOST', 'This recovery host requires lsof for native listener ownership verification.', 409)
    result = subprocess.run([command,'-nP','-a','-p',str(pid),'-iTCP:'+str(port),'-sTCP:LISTEN','-Fp'],capture_output=True,timeout=5)
    return result.returncode == 0 and ('p'+str(pid)) in result.stdout.decode().splitlines()


def signed(directory, value):
    return hmac.new(private_read(Path(directory)/'data/evidence.key'),canonical(value).encode(),hashlib.sha256).hexdigest()


def check(directory, accounting_origin, fault_origin):
    directory = Path(directory);record = read_document(directory/'native-services.json')
    require(record and record.get('format') == 'avenra-recovery-native/1', 'RECOVERY_NATIVE_REQUIRED', 'Start this recovery attempt’s owned native services before activation.', 409)
    payload = {k:v for k,v in record.items() if k!='signature'}
    require(hmac.compare_digest(record.get('signature',''),signed(directory,payload)) and record['accounting_dsn_sha256']==hashlib.sha256(private_read(directory/'data/accounting.dsn')).hexdigest(),
            'RECOVERY_NATIVE_BINDING','Native ownership receipt no longer matches the target database identity.',409)
    for role,origin in [('accounting',accounting_origin),('fault',fault_origin)]:
        entry = record['services'][role]
        require(entry['origin']==origin and entry['process_identity'].startswith(str(directory/'native'/role)+' ') and process_identity(entry['pid'])==entry['process_identity'] and sha(directory/'native'/role)==entry['binary_sha256'] and owns_listener(entry['pid'],entry['port']),
                'RECOVERY_NATIVE_BINDING','An endpoint is not owned by this recovery attempt’s pinned native process.',409)
    return record


def start(directory, accounting_port, fault_port, recovery_trust, release_trust):
    from .recovery_restore import checked_stage
    directory, _, checked = checked_stage(directory,recovery_trust,release_trust);data = directory/'data'
    imported=read_document(directory/'recovery-import.json')
    require(imported and (data/'recovery-inactive.json').exists(),'RECOVERY_IMPORT_REQUIRED','Import the new recovery databases before starting native services.',409)
    require(all(hashlib.sha256(private_read(data/name)).hexdigest()==value for name,value in imported['credential_hashes'].items()),
            'RECOVERY_DATABASE_TARGET','Recovery database identities changed after authenticated import.',409)
    for port in [accounting_port,fault_port]:
        require(type(port) is int and 1024<=port<=65535,'RECOVERY_PORT','Select an unprivileged explicit loopback port.',409)
        with socket.socket() as listener:
            try:listener.bind(('127.0.0.1',port))
            except OSError:require(False,'RECOVERY_PORT_BUSY','Preserve an occupied port; choose an unused recovery port.',409)
    require(accounting_port!=fault_port,'RECOVERY_PORT','Use different native service ports.',409)
    require(not (directory/'native-services.json').exists(),'RECOVERY_NATIVE_EXISTS','Inspect and stop the existing owned native attempt before starting another.',409)
    manifest=json.loads(private_read(directory/'payload/application/manifest.json',4_000_000))
    children=[];services={}
    try:
        for role,port in [('accounting',accounting_port),('fault',fault_port)]:
            binary=directory/'native'/role
            require(sha(binary)==manifest['native_services'][role]['sha256'],'RECOVERY_NATIVE','Materialized native binary differs from the pinned application.',409)
            command=[str(binary),'serve','--bind','127.0.0.1:'+str(port),'--postgres-max-open-conns','3','--postgres-max-idle-conns','1'] if role=='accounting' else [str(binary),'-host','127.0.0.1','-port',str(port),'-seed','42']
            environment=os.environ.copy()
            if role=='accounting':environment['POSTGRES_URI']=private_read(data/'accounting.dsn').decode().strip()
            log=directory/(role+'-service.log')
            with log.open('ab') as output:
                log.chmod(0o600);child=subprocess.Popen(command,env=environment,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
            children.append(child);origin='http://127.0.0.1:'+str(port);deadline=time.monotonic()+25
            gateway=AccountingGateway(origin) if role=='accounting' else FaultGateway(origin)
            while time.monotonic()<deadline:
                require(child.poll() is None,'RECOVERY_NATIVE_START','Native process exited; inspect its private log.',409)
                try:
                    gateway.health()
                    if owns_listener(child.pid,port):break
                except OSError:pass
                except Exception as error:
                    from .gateways import EngineError
                    if not isinstance(error,EngineError):raise
                time.sleep(.1)
            else:raise TimeoutError('Recovered native service did not establish its owned loopback listener.')
            services[role]={'pid':child.pid,'port':port,'origin':origin,'process_identity':process_identity(child.pid),'binary_sha256':sha(binary)}
        record={'format':'avenra-recovery-native/1','set_id':imported['set_id'],'accounting_dsn_sha256':hashlib.sha256(private_read(data/'accounting.dsn')).hexdigest(),'started_at':time.time(),'services':services}
        record['signature']=signed(directory,record);write_document(directory/'native-services.json',record)
        check(directory,services['accounting']['origin'],services['fault']['origin'])
        return {'started':True,'services':{role:{'pid':value['pid'],'origin':value['origin']} for role,value in services.items()}}
    except BaseException:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=15)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
        raise


def stop(directory):
    import signal
    directory=Path(directory);record=read_document(directory/'native-services.json')
    require(record,'RECOVERY_NATIVE_REQUIRED','No tracked recovery native services exist.',409)
    payload={k:v for k,v in record.items() if k!='signature'}
    require(hmac.compare_digest(record.get('signature',''),signed(directory,payload)),'RECOVERY_NATIVE_BINDING','Native ownership receipt failed verification.',409)
    for role,entry in reversed(list(record['services'].items())):
        require(entry['process_identity'].startswith(str(directory.resolve()/'native'/role)+' '),'RECOVERY_PROCESS_CHANGED','A native ownership receipt belongs to another recovery directory.',409)
        current=process_identity(entry['pid'])
        if current is None:continue
        require(current==entry['process_identity'],'RECOVERY_PROCESS_CHANGED','A tracked PID now belongs to another process; it was not stopped.',409)
        os.kill(entry['pid'],signal.SIGTERM)
        end=time.monotonic()+20
        while time.monotonic()<end and process_identity(entry['pid'])==current:
            try:
                done,_=os.waitpid(entry['pid'],os.WNOHANG)
                if done:break
            except ChildProcessError:pass
            time.sleep(.1)
        require(process_identity(entry['pid'])!=current,'RECOVERY_PROCESS_DRAIN','Owned native process is still draining; inspect its private log.',409)
    previous=directory/('native-services-stopped-'+str(time.time_ns())+'.json')
    os.replace(directory/'native-services.json',previous)
    return {'stopped':True}
