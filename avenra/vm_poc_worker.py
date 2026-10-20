"""Bounded ARM64/KVM POC executor. This file runs only inside its Linux worker.

Submitted scripts become files in an ephemeral guest initramfs. They are never
executed by a host shell, by this worker's Python interpreter, or without KVM.
The included x86 execution engine remains a separate qualification target.
"""
import base64
from contextlib import closing
import ctypes
import fcntl
import gzip
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import re
import resource
import selectors
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time

FORMAT='avenra-arm64-kvm-poc/1'
MEMORY=256*1024*1024
FIELDS={'format','request_id','tenant_id','environment_id','script','timeout_seconds','maximum_output_bytes','issued_at','expires_at'}


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def require(value,message):
    if not value:raise ValueError(message)


def private(path,maximum=65536):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno());require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077 and info.st_nlink==1,'Private worker material required.')
        raw=source.read(maximum+1)
    require(len(raw)<=maximum,'Worker material exceeds its bound.');return raw


def newc(entries):
    """Produce only the declared regular files/directories; no extraction step."""
    archive=bytearray()
    for index,(name,mode,data,uid,gid) in enumerate([*entries,('TRAILER!!!',0,b'',0,0)],1):
        encoded=name.encode()+b'\0'
        values=[index,mode,uid,gid,1,0,len(data),0,0,0,0,len(encoded),0]
        archive.extend(('070701'+''.join(f'{v:08x}' for v in values)).encode());archive.extend(encoded)
        archive.extend(b'\0'*(-len(archive)%4));archive.extend(data);archive.extend(b'\0'*(-len(archive)%4))
    return bytes(archive)


def initramfs(script,nonce,busybox):
    init='''#!/bin/busybox sh
export PATH=/bin
/bin/busybox --install -s /bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs dev /dev
mount -t tmpfs -o size=32m,nosuid,nodev tmpfs /tmp
chmod 1777 /tmp
printf 'AVENRA_VM_BOOT %s %s\\n' "'''+nonce+'''" "$(cat /proc/sys/kernel/random/boot_id)"
cd /work
su -s /bin/sh avenra -c /task.sh
result=$?
printf '\\nAVENRA_VM_RESULT %s %s %s\\n' "'''+nonce+'''" "$result" "$(cat /proc/sys/kernel/random/boot_id)"
sync
poweroff -f
'''
    entries=[(name,stat.S_IFDIR|mode,b'',uid,gid) for name,mode,uid,gid in
             [('bin',0o755,0,0),('dev',0o755,0,0),('etc',0o755,0,0),('proc',0o555,0,0),
              ('sys',0o555,0,0),('tmp',0o1777,0,0),('work',0o700,1000,1000)]]
    entries.extend([('bin/busybox',stat.S_IFREG|0o755,busybox,0,0),('init',stat.S_IFREG|0o755,init.encode(),0,0),
                    ('task.sh',stat.S_IFREG|0o755,('#!/bin/sh\n'+script+'\n').encode(),0,0),
                    ('etc/passwd',stat.S_IFREG|0o644,b'root:x:0:0:root:/:/bin/sh\navenra:x:1000:1000:guest:/work:/bin/sh\n',0,0),
                    ('etc/group',stat.S_IFREG|0o644,b'root:x:0:\navenra:x:1000:\n',0,0),
                    ('etc/shadow',stat.S_IFREG|0o600,b'root:*:0:0:99999:7:::\navenra:*:0:0:99999:7:::\n',0,0)])
    return gzip.compress(newc(entries),mtime=0)


def qmp(path,command):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as channel:
        channel.settimeout(3);deadline=time.monotonic()+3
        # QEMU may create its socket path just before listen(2). Retry only
        # connecting; never replay a management command after dispatch.
        while True:
            try:channel.connect(str(path));break
            except (ConnectionRefusedError,FileNotFoundError):
                require(time.monotonic()<deadline,'VMM management socket did not accept a connection.');time.sleep(.02)
        stream=channel.makefile('rwb',buffering=0)
        def receive():
            while True:
                raw=stream.readline(65537);require(raw and len(raw)<=65536,'Bounded QMP response required.')
                result=json.loads(raw)
                if 'event' not in result:return result
        require('QMP' in receive(),'QMP handshake unavailable.')
        stream.write(b'{"execute":"qmp_capabilities"}\n');require('return' in receive(),'QMP negotiation failed.')
        stream.write(canonical({'execute':command}).encode()+b'\n');result=receive()
        require('return' in result,'QMP command refused: '+command);return result['return']


class Worker:
    def __init__(self,root):
        require(platform.system()=='Linux' and platform.machine()=='aarch64','ARM64 Linux execution worker required; host fallback is forbidden.')
        device=Path('/dev/kvm');require(device.exists() and stat.S_ISCHR(device.stat().st_mode) and os.access(device,os.R_OK|os.W_OK),'Usable KVM device required; software emulation is forbidden.')
        self.root=Path(root);self.configuration=json.loads(private(self.root/'worker.json'))
        self.key=private(self.root/'worker.key',32);require(len(self.key)==32,'Original worker authentication key required.')
        self.kernel=self.root/'kernel';self.busybox=Path('/bin/busybox.static');self.binary=Path('/usr/bin/qemu-system-aarch64')
        for name,path in [('kernel',self.kernel),('busybox',self.busybox),('vmm',self.binary)]:
            require(path.is_file() and not path.is_symlink(),'Ordinary pinned VM asset required: '+name)
            with path.open('rb') as stream:observed=hashlib.file_digest(stream,'sha256').hexdigest()
            require(observed==self.configuration[name+'_sha256'],'Pinned VM asset differs: '+name)
        self.database=self.root/'jobs.sqlite'
        if not self.database.exists():
            try:
                fd=os.open(self.database,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
            except FileExistsError:pass
        if self.database.exists():private(self.database,32*1024*1024)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,state TEXT NOT NULL,receipt_json TEXT,created_at REAL NOT NULL)')
            db.execute("CREATE TRIGGER IF NOT EXISTS immutable_vm_receipt BEFORE UPDATE ON jobs WHEN OLD.receipt_json IS NOT NULL BEGIN SELECT RAISE(ABORT,'Immutable VM receipt'); END")
        self.database.chmod(0o600)

    def db(self):
        # The returned context explicitly closes after transaction handling.
        from contextlib import contextmanager
        @contextmanager
        def opened():
            with closing(sqlite3.connect(self.database,timeout=5)) as db:
                db.row_factory=sqlite3.Row;db.execute('PRAGMA synchronous=FULL')
                with db:yield db
        return opened()

    def authenticate(self,wrapper):
        require(isinstance(wrapper,dict) and set(wrapper)=={'document','signature'},'Signed execution request required.')
        doc=wrapper['document'];require(isinstance(doc,dict) and set(doc)==FIELDS and doc['format']==FORMAT,'Unsupported execution contract.')
        expected=hmac.new(self.key,canonical(doc).encode(),hashlib.sha256).hexdigest()
        require(isinstance(wrapper['signature'],str) and hmac.compare_digest(expected,wrapper['signature']),'Execution request authentication failed.')
        require(isinstance(doc['request_id'],str) and re.fullmatch('[0-9a-f]{32}',doc['request_id']),'Bounded execution identity required.')
        require(all(isinstance(doc[k],str) and re.fullmatch('[a-zA-Z0-9_-]{1,100}',doc[k]) for k in ['tenant_id','environment_id']),'Execution scope is invalid.')
        require(isinstance(doc['script'],str) and 0<len(doc['script'].encode())<=8192 and '\0' not in doc['script'],'Bounded guest script required.')
        require(type(doc['timeout_seconds']) is int and 1<=doc['timeout_seconds']<=30 and type(doc['maximum_output_bytes']) is int and 1024<=doc['maximum_output_bytes']<=131072,'Execution resource limits differ from this profile.')
        issued,expires=doc['issued_at'],doc['expires_at']
        require(type(issued) is int and type(expires) is int and 0<expires-issued<=60 and issued<=time.time()+5 and time.time()<expires,'Execution credential has expired.')
        return doc

    def execute(self,doc):
        payload={k:v for k,v in doc.items() if k not in {'issued_at','expires_at'}};fingerprint=digest(payload)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT * FROM jobs WHERE id=?',(doc['request_id'],)).fetchone()
            require(not old or old['payload_hash']==fingerprint,'Original execution identity has different content.')
            if old:
                require(old['receipt_json'] is not None,'Original execution is unresolved; it cannot be started again.')
                return json.loads(old['receipt_json'])
            require(db.execute('SELECT count(*) FROM jobs WHERE receipt_json IS NULL').fetchone()[0]<2,
                    'Worker capacity is occupied; preserve the request and retry without dispatch.')
            require(db.execute('SELECT count(*) FROM jobs').fetchone()[0]<5000,'Execution journal capacity requires operator action.')
            db.execute("INSERT INTO jobs VALUES(?,?,'accepted',NULL,?)",(doc['request_id'],fingerprint,time.time()))
        lock=os.open(self.root/'execution.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            # SQL admission limits concurrency to two. This shared lifetime
            # lease also lets an offline checkpoint acquire the exclusive side.
            fcntl.flock(lock,fcntl.LOCK_SH)
            receipt=self.run(doc,fingerprint)
        except Exception as error:
            receipt={'format':FORMAT,'request_id':doc['request_id'],'payload_hash':fingerprint,'scope':{k:doc[k] for k in ['tenant_id','environment_id']},
                     'state':'failed','error':type(error).__name__+': '+str(error)[:300],'production_ready':False,'official_conformance':False}
        finally:os.close(lock)
        with self.db() as db:db.execute('UPDATE jobs SET state=?,receipt_json=? WHERE id=?',(receipt['state'],canonical(receipt),doc['request_id']))
        return receipt

    def run(self,doc,fingerprint):
        import grp
        directory=self.root/'runs'/doc['request_id'];directory.mkdir(mode=0o700,parents=True)
        nonce=os.urandom(16).hex();archive=directory/'guest.cpio.gz'
        archive.write_bytes(initramfs(doc['script'],nonce,self.busybox.read_bytes()));archive.chmod(0o444)
        with self.db() as db:uid=60000+db.execute('SELECT rowid FROM jobs WHERE id=?',(doc['request_id'],)).fetchone()[0]
        os.chown(directory,uid,uid);directory.chmod(0o700)
        kvm=grp.getgrnam('kvm').gr_gid;parent=os.getpid()
        def restrictions():
            os.setgroups([kvm]);os.setgid(uid);os.setuid(uid)
            resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3));resource.setrlimit(resource.RLIMIT_FSIZE,(16*1024**2,16*1024**2))
            resource.setrlimit(resource.RLIMIT_NOFILE,(64,64));resource.setrlimit(resource.RLIMIT_CORE,(0,0))
            require(ctypes.CDLL(None).prctl(1,signal.SIGKILL)==0,'Parent-death process fence unavailable.')
            if os.getppid()!=parent:os._exit(125)
        qmp_path=directory/'qmp.sock'
        command=[str(self.binary),'-machine','virt,gic-version=host','-accel','kvm','-cpu','host','-smp','1','-m','256M',
                 '-nodefaults','-display','none','-nic','none','-monitor','none','-serial','stdio','-no-reboot','-S',
                 '-sandbox','on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny',
                 '-qmp','unix:'+str(qmp_path)+',server=on,wait=off','-kernel',str(self.kernel),'-initrd',str(archive),
                 '-append','console=ttyAMA0 rdinit=/init panic=1 quiet']
        started=time.monotonic();child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                start_new_session=True,preexec_fn=restrictions,env={'PATH':'/usr/bin:/bin','LANG':'C'})
        output=bytearray();timed_out=False;truncated=False;observations={}
        try:
            deadline=time.monotonic()+5
            while not qmp_path.exists():
                if child.poll() is not None:
                    output.extend(child.stdout.read(8192));raise RuntimeError('VMM did not boot: '+output.decode(errors='replace')[-600:])
                require(time.monotonic()<deadline,'VMM management channel did not start.');time.sleep(.02)
            observations={'kvm':qmp(qmp_path,'query-kvm'),'cpus':len(qmp(qmp_path,'query-cpus-fast')),'memory':qmp(qmp_path,'query-memory-size-summary')}
            require(observations['kvm'].get('enabled') is True and observations['cpus']==1 and observations['memory']['base-memory']==MEMORY,'Actual VMM resource/KVM observations differ from this profile.')
            qmp(qmp_path,'cont');deadline=time.monotonic()+doc['timeout_seconds']
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout,selectors.EVENT_READ)
                while True:
                    if time.monotonic()>=deadline:timed_out=True;break
                    ready=selector.select(min(.1,max(.001,deadline-time.monotonic())))
                    if ready:
                        chunk=os.read(child.stdout.fileno(),8192)
                        if not chunk:break
                        output.extend(chunk)
                        if len(output)>doc['maximum_output_bytes']:
                            output=output[:doc['maximum_output_bytes']];truncated=True;break
                    elif child.poll() is not None:break
        finally:
            if child.poll() is None:
                os.killpg(child.pid,signal.SIGKILL if timed_out or truncated else signal.SIGTERM)
                try:child.wait(timeout=3)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=3)
            else:child.wait()
            child.stdout.close()
        text=output.decode('utf-8',errors='replace');matches=re.findall('AVENRA_VM_RESULT '+nonce+r' ([0-9]{1,3}) ([0-9a-f-]{36})',text)
        state='timed_out' if timed_out else 'output_limit' if truncated else 'completed' if matches else 'failed'
        return {'format':FORMAT,'request_id':doc['request_id'],'payload_hash':fingerprint,'scope':{k:doc[k] for k in ['tenant_id','environment_id']},
                'state':state,'exit_code':int(matches[-1][0]) if matches else None,'guest_boot_id':matches[-1][1] if matches else None,
                'output':text,'output_sha256':hashlib.sha256(output).hexdigest(),'seconds':round(time.monotonic()-started,3),
                'resources':{'cpus':1,'memory_bytes':MEMORY,'network_devices':0,'host_mounts':0},'vmm_observations':observations,
                'process_reaped':child.poll() is not None,'kernel_sha256':self.configuration['kernel_sha256'],'vmm_sha256':self.configuration['vmm_sha256'],
                'production_ready':False,'official_conformance':False,'included_x86_engine_qualified':False}

    def response(self,wrapper):
        doc=self.authenticate(wrapper);receipt=self.execute(doc)
        result={'format':FORMAT,'request_hash':digest(doc),'receipt':receipt}
        return {'document':result,'signature':hmac.new(self.key,canonical(result).encode(),hashlib.sha256).hexdigest()}


def main():
    root=Path('/var/lib/avenra-vm-poc')
    try:
        raw=sys.stdin.buffer.read(32769);require(len(raw)<=32768,'Bounded execution command required.')
        result=Worker(root).response(json.loads(raw))
        print(canonical(result),flush=True);return 0
    except Exception as error:
        print(canonical({'error':type(error).__name__+': '+str(error)[:300],'executed':False}),flush=True);return 2


if __name__=='__main__':raise SystemExit(main())
