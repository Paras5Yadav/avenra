#!/usr/bin/env python3
"""Observe cold capture, independent trust, new-database restore and real recovery.

Uses dedicated source/recovered native engines. Existing console, databases and
native service processes are not stopped or rebound by this qualification.
"""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler, Request
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from avenra.app import App
from avenra import recovery_set as recovery, recovery_restore as restore, recovery_native as native
from avenra import recovery_banks as banks, bank_recovery as routing
from avenra import release_bundle as bundle, release_runtime as releases
from avenra.database_deployment import provision
from avenra.errors import DomainError
from avenra.gateways import AccountingGateway
from avenra.lab_identity import LabIdentity, private_read, private_write, rotate_client, revoke_client
from avenra.lab_bank_client import LabBankClient
from avenra.local_database import admin_dsn
from avenra.private_config import read_document, write_document
from avenra.recovery_state import INACTIVE
from avenra.synthetic_lab import fixture, prepare, authorize, advance, provision as provision_banks, connect as connect_banks, start as start_bank
from avenra.util import application_fingerprint, canonical, digest, uid
from avenra import participant_peer_lab as peer_lab, recovery_peers as peers, peer_recovery as peer_routes
from avenra.participant_peer_identity import PeerIdentity, rotate as rotate_peer, revoke as revoke_peer
from avenra.participant_peer_client import PeerClient
from avenra.participant_peer_contract import ADAPTER as PEER
from scripts.readiness_evidence import COORDINATED_PROFILES
from scripts.qualification_storage import QualificationStorage, StorageRefused, record_refusal, harness_case_pack_hash as guarded_case_pack_hash


def case_pack_hash():return guarded_case_pack_hash(ROOT, 'verify_coordinated_recovery.py')


def free_port():
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));return listener.getsockname()[1]


def bank_rows(path):
    """Compare full ordered records/schema, independent of SQLite page layout."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
        schema=db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        records={table:db.execute('SELECT * FROM '+table+' ORDER BY id').fetchall() for table in ['requests','effects','events']}
    return {'schema_sha256':digest(schema),'tables':{table:{'rows':len(rows),'sha256':digest(rows)} for table,rows in records.items()}}


def crash_import(instructions):
    value=json.loads(private_read(instructions));original=restore.pg_command
    def command(*args,**kwargs):
        original(*args,**kwargs)
        if args[3]=='control-restore.log':os._exit(79)
    restore.pg_command=command
    restore.restore_databases(**value)
    return 9


def crash_activation(instructions):
    value=json.loads(private_read(instructions));original=restore.write_document
    def write(path,document):
        original(path,document)
        if Path(path).name=='recovery-activation.json':os._exit(78)
    restore.write_document=write
    restore.activate(**value)
    return 9


def crash_bank_start(instructions):
    value=json.loads(private_read(instructions));original=banks._registration
    def registration(directory,routes,worker_id):
        record=original(directory,routes,worker_id)
        if record and native.owns_listener(record['pid'],routing.origin(record['origin'])):os._exit(77)
        return record
    banks._registration=registration
    banks.start(**value)
    return 9


def run(xml=False, independent=False, storage=None):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    folder=ROOT/'.local/qa'/uid('xml_coordinated_recovery' if xml else 'coordinated_recovery');folder.mkdir(mode=0o700,parents=True)
    report_name='coordinated-peer-recovery-verification.json' if independent else 'coordinated-upi-wire-recovery-verification.json' if xml else 'coordinated-recovery-verification.json'
    qualification_profile,protocol_mode,participant_mode=COORDINATED_PROFILES[report_name]
    report={'passed':False,'checks':[],'directory':str(folder),'created_at':time.time(),'application_fingerprint':application_fingerprint(),'case_pack_sha256':case_pack_hash(),
            'production_ready':False,'official_conformance':False,'protocol_mode':'xml' if xml else 'legacy','scope':'Dedicated local cold managed-deployment capture; independently trusted recovery/application signatures; full table/sequence import into new roles/databases; rebuilt offline runtime, pinned native binaries and two owned mTLS bank processes at authenticated replacement addresses; operator-crash replay, credential rotation and same-app original-ID recovery with original paths/listeners unavailable. AA/SSO/provider/service rebinding, multi-host failover, broker replay, off-host retention and institutional recovery targets remain open.'}
    report.update(qualification_profile=qualification_profile,protocol_mode=protocol_mode,participant_mode=participant_mode)
    children=[];app=None;target=None;native_started=False;pg=Path('/opt/homebrew/opt/postgresql@16/bin')
    source_sockets=[];source_attempts=[];observer_stop=threading.Event();observers=[];peer_workers=[];peer_processes=[];second_target=None
    def check(name,condition):
        report['checks'].append({'name':name,'passed':bool(condition)});print(json.dumps({'check':name,'passed':bool(condition)}),flush=True)
        if not condition:raise AssertionError(name)
    def denied(name,callback,code):
        try:callback()
        except DomainError as error:return check(name,error.code==code)
        check(name,False)
    def start(command,name,environment=None):
        log=folder/(name+'.log')
        with log.open('ab') as stream:
            log.chmod(0o600);child=subprocess.Popen(command,cwd=folder,env=environment,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT)
        children.append(child);return child
    def stop(child):
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=20)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
    def wait(callback,predicate=lambda v:bool(v),timeout=30):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            try:
                result=callback()
                if predicate(result):return result
            except (OSError,URLError):pass
            time.sleep(.1)
        raise TimeoutError('Recovery rehearsal did not reach its required observed state; inspect private logs.')
    try:
        storage.checkpoint('signed_application_and_offline_runtime')
        release_key,release_trust,recovery_key,recovery_trust,other_key,other_trust=[folder/x for x in ['release-key.raw','release-trust.raw','recovery-key.raw','recovery-trust.raw','other-key.raw','other-trust.raw']]
        for key,trust in [(release_key,release_trust),(recovery_key,recovery_trust),(other_key,other_trust)]:bundle.new_key(key,trust)
        built=bundle.build(ROOT,ROOT/'.local/release-inputs/wheels',release_key,folder/'application.tar.gz','coordinated-recovery-qualification')
        installed=bundle.install(folder/'application.tar.gz',release_trust,folder/'source-artifacts');runtime=folder/'source-runtime';bundle.prepare_runtime(installed['directory'],release_trust,runtime)
        native_source=folder/'source-native';native_source.mkdir(mode=0o700)
        for role in ['accounting','fault']:
            shutil.copyfile(ROOT/'.local/bin'/role,native_source/role);(native_source/role).chmod(0o700)
        storage.checkpoint('source_database_creation')
        admin=admin_dsn(ROOT);admin_file=folder/'admin.dsn';private_write(admin_file,admin)
        suffix=uid('native').split('_')[1];db_name='avn_recovery_source_'+suffix;db_role='avn_recovery_engine_'+suffix;password=uuid.uuid4().hex
        with psycopg.connect(admin,autocommit=True) as db:
            db.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Identifier(db_role),sql.Literal(password)))
            db.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(db_name),sql.Identifier(db_role)))
            db.execute(sql.SQL('ALTER DATABASE {} SET timezone TO {}').format(sql.Identifier(db_name),sql.Literal('UTC')))
        source_dsn=make_conninfo(**{**conninfo_to_dict(admin),'dbname':db_name,'user':db_role,'password':password});source_dsn_file=folder/'source-accounting.dsn';private_write(source_dsn_file,source_dsn)
        source_port,source_fault_port=free_port(),free_port();source_origin='http://127.0.0.1:'+str(source_port)
        environment=os.environ.copy();environment['POSTGRES_URI']=source_dsn
        source_engine=start([str(native_source/'accounting'),'serve','--bind','127.0.0.1:'+str(source_port),'--auto-upgrade','--postgres-max-open-conns','3','--postgres-max-idle-conns','1'],'source-accounting',environment)
        source_fault=start([str(native_source/'fault'),'-host','127.0.0.1','-port',str(source_fault_port),'-seed','42'],'source-fault')
        source_gateway=AccountingGateway(source_origin)
        from avenra.gateways import EngineError
        def engine_ready():
            try:return source_gateway.health()
            except EngineError:return None
        wait(engine_ready)
        data=folder/'source-deployment';provision(data,admin)
        write_document(data/'engine-endpoints.json',{'format':'avenra-loopback-engines/1','accounting':source_origin,'fault':'http://127.0.0.1:'+str(source_fault_port)})
        dsn=private_read(data/'control.dsn').decode().strip();app=App(data,control_dsn=dsn);value=fixture(app);tenant,env=value['tenant_id'],value['environment_id']
        bank_workers=provision_banks(app,value)
        bank_processes=[start_bank(app,worker) for worker in bank_workers];children.extend(bank_processes)
        if xml:
            from avenra.upi_wire import WIRE_LOCAL,WIRE_TLS
            for actor in app.upi.overview(tenant,env)['participants']:
                c=app.upi.connections.create(tenant,env,{'name':'Recovered XML '+actor['role'],'participant_id':actor['id'],'adapter_id':WIRE_LOCAL,'request_key':uid('xml')})
                checked_connection=app.upi.connections.validate(tenant,env,c['id'],{})
                app.upi.connections.activate(tenant,env,c['id'],{'check_id':checked_connection['id']})
            connect_banks(app,value,bank_workers,adapter_id=WIRE_TLS)
        else:connect_banks(app,value,bank_workers)
        if independent:
            peer_workers=peer_lab.provision_all(app,tenant,env)
            peer_processes=[peer_lab.start(app,worker) for worker in peer_workers];children.extend(peer_processes)
            peer_lab.connect(app,tenant,env,peer_workers)
        normal=prepare(app,value,375);authorize(app,value,normal);normal=advance(app,value,normal)
        bank_uncertain=prepare(app,value,825,financial_fault='lost_credit_response');authorize(app,value,bank_uncertain)
        bank_uncertain=advance(app,value,bank_uncertain,state='unknown')
        bank_ledger=app.environment(tenant,env)['ledger_name'];bank_credit=bank_uncertain['operation']['legs'][1]
        original_bank_effect=source_gateway.transactions(bank_ledger,bank_credit['id'])
        with app.store.reader() as db:
            pinned_row=db.execute('SELECT adapter_bindings_json,topology_json FROM upi_payments WHERE id=?',(bank_uncertain['id'],)).fetchone()
            original_pinned=(pinned_row['adapter_bindings_json'],pinned_row['topology_json'])
            original_connections=[dict(row) for row in db.execute('SELECT * FROM participant_connections ORDER BY id')]
            original_envelopes={row['id']:{k:row[k] for k in ['id','document_json','document_hash','signature','key_id']} for row in db.execute('SELECT * FROM upi_envelopes WHERE payment_id=?',(bank_uncertain['id'],))}
        if xml:
            check('XML source intents retain every exact versioned adapter profile', {b['adapter_id'] for b in json.loads(original_pinned[0]).values()}==({PEER,WIRE_TLS} if independent else {WIRE_LOCAL,WIRE_TLS}))
            check('Original XML messages were validated before the real unacknowledged bank credit',len([e for e in bank_uncertain['events'] if e['event']=='wire.validated'])==4)
        check('Separate mTLS banks retain an uncertain original credit after real post-commit response loss',bank_uncertain['state']=='unknown' and bank_credit['state']=='unknown' and len(original_bank_effect)==1)
        legacy=app.create_environment(tenant,'Cold recovery uncertainty')['id'];app.provision(tenant,legacy)
        accounts=app.accounts(tenant,legacy);payer=next(x for x in accounts if x['role']=='payer');payee=next(x for x in accounts if x['role']=='payee')
        uncertain=app.submit_payment(tenant,legacy,{'payer_id':payer['id'],'payee_id':payee['id'],'amount_paise':725,'request_key':uid('uncertain'),'fault':'lost_response'})
        app.process_payment(tenant,legacy,uncertain['id']);uncertain=app.payment(tenant,legacy,uncertain['id'])
        ledger=app.environment(tenant,legacy)['ledger_name'];original_effect=source_gateway.transactions(ledger,'payment:'+uncertain['id'])
        check('Dedicated native authority completes a participant payment and commits an actually unacknowledged original effect',normal['evidence']['valid'] and uncertain['state']=='unknown' and len(original_effect)==1)
        expected_accounts=source_gateway.accounts(ledger);expected_journal=source_gateway.transactions(ledger,all_pages=True)
        credential=json.loads(private_read(data/'credentials.json'))
        app.close();app=None
        releases.transition(data,installed['directory'],release_trust,runtime,None,str(uuid.uuid4()),'recovery-verifier')
        capture_arguments=[data,folder/'checkpoint',installed['directory'],release_trust,recovery_key,source_dsn_file,native_source,pg,str(uuid.uuid4()),'recovery-verifier']
        app=App(data,control_dsn=dsn)
        denied('A live managed application pool prevents cold capture',lambda:recovery.capture(*capture_arguments),'RELEASE_NODES_ACTIVE')
        check('Rejected live-node capture materializes no recovery set',not (folder/'checkpoint').exists())
        app.close();app=None
        denied('Native accounting clients must also drain before cold capture',lambda:recovery.capture(*capture_arguments),'RECOVERY_ACCOUNTING_ACTIVE')
        stop(source_engine);stop(source_fault)
        denied('Cold capture refuses still-running bank services even after native accounting has stopped',lambda:recovery.capture(*capture_arguments),'RECOVERY_BANK_ACTIVE')
        for child in bank_processes:stop(child)
        if independent:
            denied('Live independent inbox services prevent a cold capture after banks have drained',lambda:recovery.capture(*capture_arguments),'RECOVERY_BANK_ACTIVE')
            check('A live inbox refusal creates no partial recovery destination',not (folder/'checkpoint').exists())
            for child in peer_processes:stop(child)
        def observe(listener):
            listener.settimeout(.2)
            while not observer_stop.is_set():
                try:
                    connection,_=listener.accept();source_attempts.append(listener.getsockname()[1]);connection.close()
                except TimeoutError:pass
                except OSError:return
        for worker in bank_workers+peer_workers:
            listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            listener.bind(('127.0.0.1',routing.origin(worker['endpoint'])));listener.listen(4);source_sockets.append(listener)
            observer=threading.Thread(target=observe,args=(listener,),daemon=True);observer.start();observers.append(observer)
        storage.checkpoint('cold_checkpoint_capture')
        captured=recovery.capture(*capture_arguments);checkpoint=folder/'checkpoint';verified=recovery.verify(checkpoint,recovery_trust,release_trust)
        if independent:
            from avenra.participant_peer_checkpoint import validate as validate_peers
            peer_snapshot=validate_peers(checkpoint/'independent-participants')
            check('The externally signed capture includes all five original independent participant authorities and durable inbox rows',len(peer_snapshot['workers'])==5 and all(item['receipt_counts'] for item in peer_snapshot['workers']) and any(item['receipt_counts']['inbox']>0 for item in peer_snapshot['workers']))
            original_peer_files={relative:bundle.sha(data/relative) for relative in peer_snapshot['files']}
            from avenra.participant_peer_checkpoint import restore as restore_private_peers
            refused_directory=folder/'refused-participant-restore';refused_directory.mkdir(mode=0o700)
            corrupted=checkpoint/'independent-participants/synthetic-participants'/peer_workers[0]['worker_id']/'server-signer.raw'
            saved=corrupted.read_bytes();corrupted.write_bytes(b'z'*32);corrupted.chmod(0o600)
            denied('The component restore validates every checkpoint hash before creating any private destination',lambda:restore_private_peers(checkpoint/'independent-participants',refused_directory),'CHECKPOINT_INTEGRITY')
            check('Rejected participant restore materializes no identity or inbox roots',not list(refused_directory.iterdir()))
            corrupted.write_bytes(saved);corrupted.chmod(0o600)
        check('Cold capture binds independent recovery/application signatures, both full database inventories and exact native pins',captured['set_id']==verified['set_id'] and set(verified['manifest']['databases'])=={'control','accounting'} and len(verified['manifest']['files'])>200)
        denied('A public key carried by another signer cannot establish recovery trust',lambda:recovery.verify(checkpoint,other_trust,release_trust),'RECOVERY_SIGNATURE')
        denied('Correct recovery trust cannot replace separate application trust',lambda:recovery.verify(checkpoint,recovery_trust,other_trust),'RELEASE_SIGNATURE')
        # A separate source cut registers a real retained callback through the
        # application. No notification is sent. Recovery must not silently
        # activate its original destination under the ordinary local default.
        app=App(data,control_dsn=dsn)
        app.callbacks.register(tenant,env,{'name':'Original source notification destination','url':'http://127.0.0.1:4888/recovery-fixture','request_key':uid('source_callback')})
        app.close();app=None
        callback_capture=list(capture_arguments);callback_capture[1]=folder/'callback-checkpoint';callback_capture[8]=str(uuid.uuid4())
        recovery.capture(*callback_capture);callback_verified=recovery.verify(folder/'callback-checkpoint',recovery_trust,release_trust)
        source_bank_manifest=read_document(checkpoint/'synthetic-banks/manifest.json')
        source_bank_before={'files':{relative:bundle.sha(data/relative) for relative in source_bank_manifest['files']},
                            'databases':{worker['worker_id']:bank_rows(Path(worker['directory'])/'bank.sqlite') for worker in bank_workers}}
        write_document(folder/'source-banks-before.json',source_bank_before)
        check('Bank checkpoints retain complete original receipt rows and schema independently of SQLite backup page layout',all(bank_rows(checkpoint/'synthetic-banks/synthetic-banks'/worker['worker_id']/'bank.sqlite')==source_bank_before['databases'][worker['worker_id']] for worker in bank_workers))
        callback_target=folder/'callback-recovered';recovery.stage(folder/'callback-checkpoint',callback_target,recovery_trust,release_trust)
        restore.restore_databases(callback_target,recovery_trust,release_trust,admin_file,pg,str(uuid.uuid4()),'recovery-verifier')
        denied('An actual retained source callback prevents activation even without a service settings file',lambda:restore.activate(callback_target,recovery_trust,release_trust,'http://127.0.0.1:'+str(free_port()),'http://127.0.0.1:'+str(free_port()),str(uuid.uuid4()),'recovery-verifier'),'RECOVERY_BINDINGS_REQUIRED')
        check('Unbound callback recovery remains inactive without launching any native or notification process',(callback_target/'data'/INACTIVE).exists() and not (callback_target/'native-services.json').exists())
        changed=checkpoint/'databases/control.dump';before=changed.read_bytes();changed.write_bytes(before+b'x');changed.chmod(0o600)
        denied('Altered database bytes are refused before staging or database creation',lambda:recovery.stage(checkpoint,folder/'rejected',recovery_trust,release_trust),'RECOVERY_INTEGRITY')
        changed.write_bytes(before);changed.chmod(0o600);check('Rejected altered capture creates no staging directory',not (folder/'rejected').exists())
        link=checkpoint/'unexpected-link';link.symlink_to('/tmp')
        denied('A recovery payload link is refused',lambda:recovery.verify(checkpoint,recovery_trust,release_trust),'RECOVERY_PATH');link.unlink()
        from avenra_fidelity.archive import encrypt_snapshot,recovery_key as archive_key,restore_snapshot
        encryption_key=archive_key(folder/'archive.key',create=True)
        encrypted=encrypt_snapshot(checkpoint,folder/'checkpoint.avenra.enc',encryption_key);decrypted=folder/'decrypted-checkpoint'
        recovered=restore_snapshot(folder/'checkpoint.avenra.enc',decrypted,encryption_key)
        check('The complete signed recovery set survives authenticated encryption with its identical manifest and external trust',encrypted['manifest_hash']==recovered['manifest_hash'] and recovery.verify(decrypted,recovery_trust,release_trust)['set_id']==captured['set_id'])
        storage.checkpoint('cold_runtime_stage_and_database_import')
        target=folder/'recovered';staged=recovery.stage(decrypted,target,recovery_trust,release_trust)
        check('Staging rebuilds a new offline runtime and retains the inactive guard',staged['activation']=='inactive' and (target/'runtime/release-runtime.json').exists() and (target/'data'/INACTIVE).exists())
        denied('App construction refuses restored source credentials before opening a pool',lambda:App(target/'data',control_dsn='deliberately-invalid-no-network'),'RECOVERY_INACTIVE')
        denied('Managed startup also refuses inactive source connections',lambda:releases.command(target/'data',release_trust,'api',free_port()),'RECOVERY_INACTIVE')
        from avenra.lab_bank import SyntheticBank
        denied('A restored bank cannot bypass inactive routing through its ordinary service launcher',lambda:SyntheticBank(target/'data/synthetic-banks'/bank_workers[0]['worker_id']/'worker.json'),'RECOVERY_INACTIVE')
        denied('A restored client cannot fall back to an original bank address before routing exists',lambda:LabBankClient(LabIdentity(target/'data',bank_workers[0]['connections'][0]['secret_reference'])),'RECOVERY_BANK_REQUIRED')
        if independent:
            from avenra.participant_peer import ParticipantPeer
            denied('The ordinary inbox launcher cannot bypass inactive restoration',lambda:ParticipantPeer(target/'data/synthetic-participants'/peer_workers[0]['worker_id']/'worker.json'),'RECOVERY_INACTIVE')
            denied('A restored participant client cannot contact a source address before routing exists',lambda:PeerClient(PeerIdentity(target/'data',peer_workers[0]['secret_reference'])),'RECOVERY_PEER_REQUIRED')
        import_id=str(uuid.uuid4())
        instructions={'directory':str(target),'recovery_trust':str(recovery_trust),'release_trust':str(release_trust),'administrative_dsn_file':str(admin_file),'pg_bin':str(pg),'request_id':import_id,'actor':'recovery-verifier'}
        instruction_file=folder/'import-crash.json';write_document(instruction_file,instructions)
        crashed=start([sys.executable,'-B',str(Path(__file__).resolve()),'--import-crash',str(instruction_file)],'import-operator-crash');wait(lambda:crashed.poll() is not None)
        check('Actual operator death after the first database commit retains an inactive resumable two-database restore',crashed.returncode==79 and (target/'data'/INACTIVE).exists() and not (target/'recovery-import.json').exists() and (target/'target-identities.json').exists())
        imported=restore.restore_databases(target,recovery_trust,release_trust,admin_file,pg,import_id,'recovery-verifier')
        repeated=restore.restore_databases(target,recovery_trust,release_trust,admin_file,pg,import_id,'recovery-verifier')
        check('New-role imports preserve every retained table row and sequence; exact replay reuses the original new databases',imported['databases']==repeated['databases'] and imported['database_inventory_hashes']==repeated['database_inventory_hashes'])
        denied('A changed restore actor cannot reuse the allocated attempt',lambda:restore.restore_databases(target,recovery_trust,release_trust,admin_file,pg,import_id,'another-actor'),'RECOVERY_CONFLICT')
        restored_dsn=private_read(target/'data/control.dsn').decode().strip()
        with psycopg.connect(restored_dsn,autocommit=True) as db:
            for statement in ['CREATE TABLE forbidden_recovery(id int)','UPDATE release_active SET generation=999','DELETE FROM audit_events']:
                rejected=False
                try:db.execute(statement)
                except psycopg.errors.InsufficientPrivilege:rejected=True
                check('Recovered runtime remains least privilege: '+statement.split()[0]+' '+statement.split()[1],rejected)
        activation_id=str(uuid.uuid4());new_port,new_fault_port=free_port(),free_port();origin='http://127.0.0.1:'+str(new_port);fault_origin='http://127.0.0.1:'+str(new_fault_port)
        denied('Activation cannot point at an unowned original or arbitrary native endpoint',lambda:restore.activate(target,recovery_trust,release_trust,source_origin,'http://127.0.0.1:'+str(source_fault_port),activation_id,'recovery-verifier'),'RECOVERY_NATIVE_REQUIRED')
        saved_dsn=private_read(target/'data/accounting.dsn');(target/'data/accounting.dsn').write_text(source_dsn);(target/'data/accounting.dsn').chmod(0o600)
        denied('Native startup refuses a source database identity substituted after import',lambda:native.start(target,new_port,new_fault_port,recovery_trust,release_trust),'RECOVERY_DATABASE_TARGET')
        (target/'data/accounting.dsn').write_bytes(saved_dsn);(target/'data/accounting.dsn').chmod(0o600)
        native.start(target,new_port,new_fault_port,recovery_trust,release_trust);native_started=True
        denied('Even healthy arbitrary endpoints cannot replace the owned recovered listeners',lambda:restore.activate(target,recovery_trust,release_trust,source_origin,fault_origin,activation_id,'recovery-verifier'),'RECOVERY_NATIVE_BINDING')
        recovered_gateway=AccountingGateway(origin)
        check('The independently restored native process returns exact original balances and journal before recovery',digest(recovered_gateway.accounts(ledger))==digest(expected_accounts) and digest(recovered_gateway.transactions(ledger,all_pages=True))==digest(expected_journal))
        denied('Owned native services alone cannot activate unbound captured bank identities',lambda:restore.activate(target,recovery_trust,release_trust,origin,fault_origin,activation_id,'recovery-verifier'),'RECOVERY_BANK_REQUIRED')
        bank_ports={worker['worker_id']:free_port() for worker in bank_workers};bank_request=str(uuid.uuid4())
        damaged=target/'data/synthetic-banks'/bank_workers[0]['worker_id']/'server-signer.raw';saved=damaged.read_bytes();damaged.write_bytes(b'x'*32);damaged.chmod(0o600)
        denied('Bank startup checks every staged private byte against the signed capture before publishing a route',lambda:banks.start(target,bank_ports,recovery_trust,release_trust,bank_request,'recovery-verifier'),'RECOVERY_BANK_BINDING')
        damaged.write_bytes(saved);damaged.chmod(0o600)
        restored_bank_db=target/'data/synthetic-banks'/bank_workers[0]['worker_id']/'bank.sqlite'
        retained_bank_db=restored_bank_db.with_name('bank.sqlite-retained');restored_bank_db.rename(retained_bank_db)
        source_bank_db=Path(bank_workers[0]['directory'])/'bank.sqlite'
        try:
            restored_bank_db.symlink_to(source_bank_db)
            denied('A linked bank receipt database is refused before it can write to a source installation',lambda:banks.start(target,bank_ports,recovery_trust,release_trust,bank_request,'recovery-verifier'),'RECOVERY_BANK_BINDING')
            restored_bank_db.unlink();os.link(source_bank_db,restored_bank_db)
            denied('A hard-linked bank receipt database is also refused before process startup',lambda:banks.start(target,bank_ports,recovery_trust,release_trust,bank_request,'recovery-verifier'),'RECOVERY_BANK_BINDING')
        finally:
            restored_bank_db.unlink(missing_ok=True);retained_bank_db.rename(restored_bank_db)
        instructions={'directory':str(target),'ports':bank_ports,'recovery_trust':str(recovery_trust),'release_trust':str(release_trust),'request_id':bank_request,'actor':'recovery-verifier'}
        instruction_file=folder/'bank-start-crash.json';write_document(instruction_file,instructions)
        crashed=start([sys.executable,'-B',str(Path(__file__).resolve()),'--bank-start-crash',str(instruction_file)],'bank-start-operator-crash');wait(lambda:crashed.poll() is not None)
        route_plan=routing.load(target/'data');first_worker=next(iter(route_plan['workers']))
        first_registration=routing.signed_read(target/'data',target/'data/synthetic-banks'/first_worker/'recovery-process.json')
        check('Actual operator death leaves a self-registered owned bank and an inactive durable routing decision',crashed.returncode==77 and native.process_identity(first_registration['pid'])==first_registration['process_identity'] and (target/'data'/INACTIVE).exists() and not (target/'bank-services.json').exists())
        started=banks.start(target,bank_ports,recovery_trust,release_trust,bank_request,'recovery-verifier')
        check('The original retry reuses the surviving bank process and completes the remaining owned bank',len(started['services'])==2 and started['services'][first_worker]['pid']==first_registration['pid'] and len(banks.check(target)['services'])==2)
        denied('Changed bank-start actor cannot reuse the accepted routing decision',lambda:banks.start(target,bank_ports,recovery_trust,release_trust,bank_request,'another-actor'),'RECOVERY_CONFLICT')
        identity=LabIdentity(target/'data',bank_workers[0]['connections'][0]['secret_reference']);route_path=target/'data'/routing.FILE;route_bytes=route_path.read_bytes()
        changed=read_document(route_path);changed['workers'][first_worker]['origin']='https://127.0.0.1:'+str(free_port());write_document(route_path,changed)
        denied('Changed physical routing is rejected before a client contacts any bank',lambda:LabBankClient(identity),'RECOVERY_BANK_AUTHENTICATION')
        route_path.write_bytes(route_bytes);route_path.chmod(0o600)
        changed=routing.load(target/'data');changed['workers'][identity.document['worker_id']]['origin']='https://192.0.2.1:4433';routing.signed_write(target/'data',route_path,changed)
        denied('Even an attributed route cannot enable non-loopback bank egress',lambda:LabBankClient(identity),'RECOVERY_BANK_ROUTE')
        route_path.write_bytes(route_bytes);route_path.chmod(0o600)
        changed=routing.load(target/'data');changed['authorities'][identity.binding]=next(worker['worker_id'] for worker in bank_workers if worker['worker_id']!=identity.document['worker_id']);routing.signed_write(target/'data',route_path,changed)
        denied('A valid routing signature cannot replace a pinned bank authority with another bank',lambda:LabBankClient(identity),'RECOVERY_BANK_BINDING')
        route_path.write_bytes(route_bytes);route_path.chmod(0o600)
        changed=routing.load(target/'data');other_worker=next(worker['worker_id'] for worker in bank_workers if worker['worker_id']!=identity.document['worker_id'])
        changed['workers'][identity.document['worker_id']]['origin']=changed['workers'][other_worker]['origin'];routing.signed_write(target/'data',route_path,changed)
        try:LabBankClient(identity).probe()
        except EngineError as error:check('Actual TLS refuses routing one bank client to a different restored bank',error.code=='OUTCOME_UNKNOWN')
        else:check('Actual TLS refuses routing one bank client to a different restored bank',False)
        route_path.write_bytes(route_bytes);route_path.chmod(0o600)
        beneficiary=next(connection for connection in bank_workers[1]['connections'] if connection['role']=='beneficiary')
        old_client=LabBankClient(LabIdentity(target/'data',beneficiary['secret_reference']))
        rotated=rotate_client(target/'data',beneficiary['secret_reference']);revoke_client(target/'data',beneficiary['secret_reference'],rotated['previous_key_id'])
        try:old_client.probe()
        except EngineError as error:check('Restored banks enforce signing-key revocation across original financial uncertainty',error.code=='SIGNING_KEY_REVOKED')
        else:check('Restored banks enforce signing-key revocation across original financial uncertainty',False)
        check('Credential rotation retains the original logical authority and authenticated new physical route',LabBankClient(LabIdentity(target/'data',beneficiary['secret_reference'])).probe()['credential_epoch']==2)
        if independent:
            denied('Bound banks alone cannot activate captured but unbound independent inboxes',lambda:restore.activate(target,recovery_trust,release_trust,origin,fault_origin,activation_id,'recovery-verifier'),'RECOVERY_PEER_REQUIRED')
            peer_ports={w['worker_id']:free_port() for w in peer_workers};peer_request=str(uuid.uuid4())
            damaged=target/'data/synthetic-participants'/peer_workers[0]['worker_id']/'server-signer.raw';saved=damaged.read_bytes()
            damaged.write_bytes(b'x'*32);damaged.chmod(0o600)
            denied('Participant startup refuses changed staged keys before publishing routes',lambda:peers.start(target,peer_ports,recovery_trust,release_trust,peer_request,'recovery-verifier'),'RECOVERY_PEER_BINDING')
            damaged.write_bytes(saved);damaged.chmod(0o600)
            restored_inbox=target/'data/synthetic-participants'/peer_workers[0]['worker_id']/'participant.sqlite'
            retained_inbox=restored_inbox.with_name('participant.sqlite-retained');restored_inbox.rename(retained_inbox)
            source_inbox=data/'synthetic-participants'/peer_workers[0]['worker_id']/'participant.sqlite'
            try:
                restored_inbox.symlink_to(source_inbox)
                denied('A linked inbox cannot redirect restored writes into the source installation',lambda:peers.start(target,peer_ports,recovery_trust,release_trust,peer_request,'recovery-verifier'),'RECOVERY_BANK_BINDING')
                restored_inbox.unlink();os.link(source_inbox,restored_inbox)
                denied('A hard-linked inbox is refused before starting a restored participant',lambda:peers.start(target,peer_ports,recovery_trust,release_trust,peer_request,'recovery-verifier'),'RECOVERY_BANK_BINDING')
            finally:
                restored_inbox.unlink(missing_ok=True);retained_inbox.rename(restored_inbox)
            started_peers=peers.start(target,peer_ports,recovery_trust,release_trust,peer_request,'recovery-verifier')
            check('All five independent restored listeners are owned by the verified offline runtime',len(started_peers['services'])==5 and len(peers.check(target)['services'])==5)
            retry=peers.start(target,peer_ports,recovery_trust,release_trust,peer_request,'recovery-verifier')
            check('An identical participant startup reuses every original registered process',retry['reused_plan'] and retry['services']==started_peers['services'])
            denied('Changed participant startup attribution cannot reuse the accepted plan',lambda:peers.start(target,peer_ports,recovery_trust,release_trust,peer_request,'other-actor'),'RECOVERY_CONFLICT')
            selected_peer=next(w for w in peer_workers if w['role']=='network');peer_reference=selected_peer['secret_reference']
            peer_identity=PeerIdentity(target/'data',peer_reference);old_peer=PeerClient(peer_identity)
            original_message=next(e for e in normal['messages'] if e['step']==1)
            from avenra.upi_wire import encode
            original_xml=encode(json.loads(original_message['document_json']),original_message['signature'])
            original_receipt=json.loads(next(e['details_json'] for e in normal['events'] if e['event']=='peer.accepted' and e['message_id']==original_message['id']))['receipt']
            try:old_peer.deliver(original_xml,0)
            except EngineError as error:check('Inactive restored inboxes permit authenticated probes while refusing message delivery',error.code=='OUTCOME_UNKNOWN')
            else:check('Inactive restored inboxes permit authenticated probes while refusing message delivery',False)
            rotated_peer=rotate_peer(target/'data',peer_reference);revoke_peer(target/'data',peer_reference,rotated_peer['previous_key_id'])
            try:old_peer.probe()
            except EngineError as error:check('Restored independent receivers enforce transport signing-key revocation',error.code=='OUTCOME_UNKNOWN')
            else:check('Restored independent receivers enforce transport signing-key revocation',False)
            new_peer=PeerClient(PeerIdentity(target/'data',peer_reference))
            check('Rotation retains the original authority and authenticated new physical route',new_peer.identity.binding==peer_identity.binding and new_peer.document['identity_epoch']==2 and new_peer.probe()['worker_id']==selected_peer['worker_id'])
            routes_path=target/'data'/peer_routes.FILE;route_saved=routes_path.read_bytes()
            tampered=read_document(routes_path);tampered['actor']='unattributed';write_document(routes_path,tampered)
            denied('A changed route signature is refused before any recovered participant connection',lambda:PeerClient(PeerIdentity(target/'data',peer_reference)),'RECOVERY_PEER_AUTHENTICATION')
            routes_path.write_bytes(route_saved);routes_path.chmod(0o600)
            tampered=peer_routes.load(target/'data');tampered['workers'][selected_peer['worker_id']]['origin']='https://192.0.2.1:8443';peer_routes.signed_write(target/'data',routes_path,tampered)
            denied('Even an attributed participant route cannot enable external egress',lambda:PeerClient(PeerIdentity(target/'data',peer_reference)),'RECOVERY_BANK_ROUTE')
            routes_path.write_bytes(route_saved);routes_path.chmod(0o600)
        # Original installation paths cease to exist. The restored launch must
        # use only its copied artifacts, wheels, binaries, identities and DBs.
        for path in [data,Path(installed['directory']).parent,runtime,native_source]:path.rename(path.with_name(path.name+'-unavailable'))
        instructions={'directory':str(target),'recovery_trust':str(recovery_trust),'release_trust':str(release_trust),'accounting_origin':origin,'fault_origin':fault_origin,'request_id':activation_id,'actor':'recovery-verifier'}
        instruction_file=folder/'activation-crash.json';write_document(instruction_file,instructions)
        crashed=start([sys.executable,'-B',str(Path(__file__).resolve()),'--activation-crash',str(instruction_file)],'activation-operator-crash');wait(lambda:crashed.poll() is not None)
        check('Actual operator death after publishing bindings leaves startup guarded until original-decision replay',crashed.returncode==78 and (target/'data'/INACTIVE).exists() and (target/'recovery-activation.json').exists())
        saved=(target/'data/engine-endpoints.json').read_bytes();write_document(target/'data/engine-endpoints.json',{'format':'avenra-loopback-engines/1','accounting':source_origin,'fault':fault_origin})
        denied('An interrupted activation refuses concurrently changed bindings and retains its guard',lambda:restore.activate(target,recovery_trust,release_trust,origin,fault_origin,activation_id,'recovery-verifier'),'RECOVERY_CONFIGURATION_CHANGED')
        (target/'data/engine-endpoints.json').write_bytes(saved);(target/'data/engine-endpoints.json').chmod(0o600)
        activated=restore.activate(target,recovery_trust,release_trust,origin,fault_origin,activation_id,'recovery-verifier')
        check('Activation rebinds the same selected release without original installation paths or financial rewrites',activated['source_release_pointer_preserved'] and not activated['financial_outcomes_rewritten'] and not (target/'data'/INACTIVE).exists())
        if independent:
            check('The activated receiver returns its byte-identical historical message receipt under rotated credentials',PeerClient(PeerIdentity(target/'data',peer_reference)).deliver(original_xml,0)==original_receipt)
        check('An identical activation retry is attributed and idempotent',restore.activate(target,recovery_trust,release_trust,origin,fault_origin,activation_id,'recovery-verifier')['replayed'])
        denied('Changed activation endpoints cannot replay the original decision',lambda:restore.activate(target,recovery_trust,release_trust,source_origin,fault_origin,activation_id,'recovery-verifier'),'RECOVERY_CONFLICT')
        api_port=free_port();api_origin='http://127.0.0.1:'+str(api_port);opener=build_opener(ProxyHandler({}))
        def api(path,body=None):
            request=Request(api_origin+path,data=canonical(body).encode() if body is not None else None,headers={'Content-Type':'application/json','Authorization':'Bearer '+credential['api_key']})
            try:response=opener.open(request,timeout=10)
            except HTTPError as error:response=error
            with response:value=json.load(response)
            return response.status,value
        api_process=start(releases.command(target/'data',release_trust,'api',api_port),'recovered-api');wait(lambda:api('/health/live'),lambda r:r[0]==200)
        check('Recovered API serves the same Avenra app and refuses to claim a missing fresh worker',api('/health/ready')[0]==503 and opener.open(api_origin+'/',timeout=10).read()==(target/'artifacts'/built['artifact_id']/'avenra/static/index.html').read_bytes())
        scope='/api/tenants/'+tenant+'/environments/'+legacy
        before_reconcile=api(scope+'/payments/'+uncertain['id'])[1]
        check('The recovered API retains original uncertainty before a fresh financial worker starts',before_reconcile['state']=='unknown')
        bank_scope='/api/tenants/'+tenant+'/environments/'+env+'/banking/upi/payments'
        check('The recovered API retains the original separate-bank uncertain credit before worker recovery',api(bank_scope+'/'+bank_uncertain['id'])[1]['state']=='unknown')
        worker=start(releases.command(target/'data',release_trust,'worker'),'recovered-worker');wait(lambda:api('/health/ready'),lambda r:r[0]==200)
        api(scope+'/payments/'+uncertain['id']+'/reconcile',{})
        done=wait(lambda:api(scope+'/payments/'+uncertain['id'])[1],lambda r:r['state']=='succeeded')
        effects=recovered_gateway.transactions(ledger,'payment:'+uncertain['id'])
        check('Recovered status confirms the same original committed financial effect once',len(effects)==1 and str(effects[0]['id'])==str(original_effect[0]['id']) and str(done['ledger_transaction_id'])==str(original_effect[0]['id']) and digest(recovered_gateway.accounts(ledger))==digest(expected_accounts))
        bank_done=wait(lambda:api(bank_scope+'/'+bank_uncertain['id'])[1],lambda r:r['state']=='succeeded' and r['evidence'])
        recovered_credit=recovered_gateway.transactions(bank_ledger,bank_credit['id'])
        check('Restored banks confirm the same original credit once after endpoint relocation and credential rotation',bank_done['operation_id']==bank_uncertain['operation_id'] and bank_done['evidence']['valid'] and len(recovered_credit)==1 and str(recovered_credit[0]['id'])==str(original_bank_effect[0]['id']))
        with psycopg.connect(restored_dsn) as db:
            pinned=db.execute('SELECT adapter_bindings_json,topology_json FROM upi_payments WHERE id=%s',(bank_uncertain['id'],)).fetchone()
            from psycopg.rows import dict_row
            with db.cursor(row_factory=dict_row) as cursor:
                restored_connections=cursor.execute('SELECT * FROM participant_connections ORDER BY id').fetchall()
        check('Original accepted connection rows and payment topology/bindings remain byte-identical',pinned==original_pinned and restored_connections==original_connections)
        if independent:
            check('Recovered delivery retains signed independent receipts without granting inboxes financial authority',len([e for e in bank_done['events'] if e['event']=='peer.accepted'])==5 and bank_done['evidence']['valid'])
            peer_route_path=target/'data'/peer_routes.FILE;peer_route_bytes=peer_route_path.read_bytes();peer_route_path.unlink()
            denied('An already-constructed client revalidates route pins before every network operation',lambda:new_peer.probe(),'RECOVERY_PEER_BINDING')
            denied('An activated participant client refuses a missing route instead of source fallback',lambda:PeerClient(PeerIdentity(target/'data',peer_reference)),'RECOVERY_PEER_BINDING')
            from avenra.private_config import deployment
            denied('Managed startup refuses a missing activated independent participant route',lambda:deployment(target/'data'),'RECOVERY_PEER_BINDING')
            peer_route_path.write_bytes(peer_route_bytes);peer_route_path.chmod(0o600)
        if xml:
            recovered_envelopes={m['id']:{k:m[k] for k in ['id','document_json','document_hash','signature','key_id']} for m in bank_done['messages']}
            check('Every accepted original XML envelope and signature survives relocation byte-identically',all(recovered_envelopes.get(k)==v for k,v in original_envelopes.items()))
            check('Restored durable XML delivery validates exactly once per message with native financial evidence',len([e for e in bank_done['events'] if e['event']=='wire.validated'])==7 and bank_done['evidence']['valid'])
        route_path.unlink()
        denied('An activated client refuses a missing routing file instead of using an original address',lambda:LabBankClient(LabIdentity(target/'data',beneficiary['secret_reference'])),'RECOVERY_BANK_BINDING')
        from avenra.private_config import deployment
        denied('Managed application startup also refuses a missing activated bank routing file',lambda:deployment(target/'data'),'RECOVERY_BANK_BINDING')
        route_path.write_bytes(route_bytes);route_path.chmod(0o600)
        original=api('/api/tenants/'+tenant+'/environments/'+env+'/banking/upi/payments/'+normal['id'])[1]
        check('Original participant payment evidence remains valid in the restored serving application',original['state']=='succeeded' and original['evidence']['valid'])
        upi_scope='/api/tenants/'+tenant+'/environments/'+env+'/banking/upi/payments'
        body={'payer_id':value['accounts'][0],'payee_id':value['accounts'][1],'device_id':value['devices'][0],'amount_paise':125,'request_key':uid('after_restore')}
        status,fresh=api(upi_scope,body)
        check('The restored serving application accepts a new participant payment',status==202 and fresh['id']!=normal['id'])
        api(upi_scope+'/'+fresh['id']+'/authorize',{'device_id':value['devices'][0],'device_proof':value['proofs'][0],'approved':True})
        fresh=wait(lambda:api(upi_scope+'/'+fresh['id'])[1],lambda r:r['state']=='succeeded' and r['evidence'])
        check('Fresh post-restore financial work completes with native evidence and valid continued identities',fresh['evidence']['valid'] and len(fresh['operation']['legs'])==2 and all(leg['ledger_id'] for leg in fresh['operation']['legs']))
        check('Post-restore evidence records actual encrypted bank traffic at authenticated new physical addresses',all(trace['channel'].get('recovery_set_id')==captured['set_id'] and trace['channel'].get('physical_endpoint')!=trace['channel'].get('logical_endpoint') for trace in fresh['evidence']['manifest']['bank_transport_receipts'] if trace.get('channel')))
        if independent:
            original_network=peers.check(target)['services'][selected_peer['worker_id']]
            os.kill(original_network['pid'],signal.SIGKILL)
            def peer_reaped():
                try:os.waitpid(original_network['pid'],os.WNOHANG)
                except ChildProcessError:pass
                return native.process_identity(original_network['pid']) is None
            wait(peer_reaped)
            status,peer_interrupted=api(upi_scope,{**body,'amount_paise':215,'request_key':uid('peer_restart')})
            api(upi_scope+'/'+peer_interrupted['id']+'/authorize',{'device_id':value['devices'][0],'device_proof':value['proofs'][0],'approved':True})
            peer_interrupted=wait(lambda:api(upi_scope+'/'+peer_interrupted['id'])[1],lambda r:any(e['event']=='peer.unconfirmed' for e in r['events']))
            check('Actual recovered network loss blocks financial dispatch without an implicit local participant',status==202 and
                  peer_interrupted['operation']['state']=='pending' and all(leg['state']=='pending' and not leg['ledger_id'] and
                  not recovered_gateway.transactions(bank_ledger,leg['id']) for leg in peer_interrupted['operation']['legs']))
            restarted_peers=peers.start(target,peer_ports,recovery_trust,release_trust,peer_request,'recovery-verifier')
            check('An active recovery restarts the dead inbox under the original attributed route decision',restarted_peers['reused_plan'] and restarted_peers['services'][selected_peer['worker_id']]['pid']!=original_network['pid'])
            peer_resumed=wait(lambda:api(upi_scope+'/'+peer_interrupted['id'])[1],lambda r:r['state']=='succeeded' and r['evidence'])
            check('Network restart completes the original accepted payment with valid evidence and one native posting per leg',peer_resumed['id']==peer_interrupted['id'] and peer_resumed['evidence']['valid'] and all(len(recovered_gateway.transactions(bank_ledger,leg['id']))==1 for leg in peer_resumed['operation']['legs']))
        before_bank_crash=banks.check(target);beneficiary_id=bank_workers[1]['worker_id'];crashed_bank=before_bank_crash['services'][beneficiary_id]
        os.kill(crashed_bank['pid'],signal.SIGKILL)
        def reaped():
            try:os.waitpid(crashed_bank['pid'],os.WNOHANG)
            except ChildProcessError:pass
            return native.process_identity(crashed_bank['pid']) is None
        wait(reaped)
        status,interrupted=api(upi_scope,{**body,'amount_paise':225,'request_key':uid('bank_restart')})
        api(upi_scope+'/'+interrupted['id']+'/authorize',{'device_id':value['devices'][0],'device_proof':value['proofs'][0],'approved':True})
        interrupted=wait(lambda:api(upi_scope+'/'+interrupted['id'])[1],lambda r:r['state']=='unknown')
        check('Actual restored-bank process loss leaves the accepted original debit confirmed and credit uncertain',status==202 and interrupted['operation']['legs'][0]['state']=='confirmed' and interrupted['operation']['legs'][1]['state']=='unknown')
        restarted=banks.start(target,bank_ports,recovery_trust,release_trust,bank_request,'recovery-verifier')
        check('An active deployment restarts the dead owned bank under the original routing decision',restarted['reused_plan'] and restarted['services'][beneficiary_id]['pid']!=crashed_bank['pid'] and len(banks.check(target)['services'])==2)
        resumed=wait(lambda:api(upi_scope+'/'+interrupted['id'])[1],lambda r:r['state']=='succeeded' and r['evidence'])
        check('Post-activation bank restart recovers original financial leg identities once with valid evidence',resumed['operation_id']==interrupted['operation_id'] and resumed['evidence']['valid'] and all(len(recovered_gateway.transactions(bank_ledger,leg['id']))==1 for leg in resumed['operation']['legs']))
        check('No restored process or verification client contacted either original bank listener',not source_attempts)
        source_bank_root=data.with_name(data.name+'-unavailable')
        source_bank_after={'files':{relative:bundle.sha(source_bank_root/relative) for relative in source_bank_manifest['files']},
                           'databases':{worker['worker_id']:bank_rows(source_bank_root/'synthetic-banks'/worker['worker_id']/'bank.sqlite') for worker in bank_workers}}
        write_document(folder/'source-banks-after.json',source_bank_after)
        check('Recovered bank activity and key rotation leave every original bank/identity byte and full receipt row unchanged',source_bank_before==source_bank_after)
        if independent:
            check('Recovered participant activity leaves every original private identity and receipt database byte unchanged',original_peer_files=={relative:bundle.sha(source_bank_root/relative) for relative in peer_snapshot['files']})
        with psycopg.connect(source_dsn) as db:
            db.execute("SET LOCAL timezone='UTC'");source_unchanged=recovery.database_inventory(db)==verified['manifest']['databases']['accounting']
        with psycopg.connect(dsn) as db:
            db.execute("SET LOCAL timezone='UTC'");source_control_unchanged=recovery.database_inventory(db)==callback_verified['manifest']['databases']['control']
        check('Restored startup and recovery leave the prepared original financial and control databases unchanged',source_unchanged and source_control_unchanged)
        # A genuine preserved signed predecessor does not understand the new
        # physical binding policy. Reject it before changing this recovered DB,
        # rather than selecting it and discovering a startup/source-route error.
        stop(worker);stop(api_process)
        predecessor=read_document(ROOT/'.local/peer-recovery-predecessor/baseline.json') if independent else read_document(ROOT/'.local/release-baselines'/('before-upi-wire-recovery.json' if xml else 'before-operations.json'))
        predecessor_directory=predecessor.get('artifact_directory',predecessor.get('directory'))
        old=bundle.verify_installed(predecessor_directory,predecessor['trust_file'])
        with psycopg.connect(private_read(target/'data/migration.dsn').decode().strip()) as db:
            db.execute("SET LOCAL timezone='UTC'");selection_before=recovery.database_inventory(db)
        denied('A restored deployment refuses a genuine signed predecessor without required binding capabilities',lambda:releases.transition(target/'data',predecessor_directory,predecessor['trust_file'],predecessor['runtime_directory'],built['artifact_id'],str(uuid.uuid4()),'recovery-verifier'),'RELEASE_CAPABILITIES')
        with psycopg.connect(private_read(target/'data/migration.dsn').decode().strip()) as db:
            db.execute("SET LOCAL timezone='UTC'");selection_after=recovery.database_inventory(db)
        check('Refused incompatible selection preserves every restored control row, sequence and the original release pointer',selection_before==selection_after and releases.status(target/'data')['selection']['artifact_id']==built['artifact_id'] and not (target/'data/release-pending.json').exists())
        if xml:
            from avenra.participant_capabilities import ensure
            with psycopg.connect(private_read(target/'data/migration.dsn').decode().strip()) as db:
                needed=ensure(db,bundle.verify_installed(target/'artifacts'/built['artifact_id'],release_trust))
            check('The selected signed build explicitly supports every retained XML profile hash',len(needed)==(3 if independent else 2) and all(item.startswith((WIRE_LOCAL+'/',WIRE_TLS+'/',PEER+'/')) for item in needed))
        expected_bindings={'engine_bindings':'avenra-loopback-engines/1','bank_routes':'avenra-bank-recovery-routes/1'}
        if independent:expected_bindings['peer_routes']=peer_routes.FORMAT
        check('The selected signed recovery build explicitly declares every configured binding format',releases.require_deployment_capabilities(target/'data',bundle.verify_installed(target/'artifacts'/built['artifact_id'],release_trust))==expected_bindings)
        if independent:
            peers.stop(target);banks.stop(target);native.stop(target);native_started=False
            storage.checkpoint('second_cold_generation')
            second_checkpoint=folder/'second-checkpoint'
            second_capture=recovery.capture(target/'data',second_checkpoint,target/'artifacts'/built['artifact_id'],release_trust,recovery_key,target/'data/accounting.dsn',target/'native',pg,str(uuid.uuid4()),'recovery-verifier')
            second_peer_snapshot=validate_peers(second_checkpoint/'independent-participants')
            check('A second-generation capture records current physical origins while preserving original logical authorities',all(item['source_origin']=='https://127.0.0.1:'+str(peer_ports[item['worker_id']]) for item in second_peer_snapshot['workers']))
            second_target=folder/'second-recovered';recovery.stage(second_checkpoint,second_target,recovery_trust,release_trust)
            restore.restore_databases(second_target,recovery_trust,release_trust,admin_file,pg,str(uuid.uuid4()),'recovery-verifier')
            second_accounting_port,second_fault_port=free_port(),free_port()
            second_origin='http://127.0.0.1:'+str(second_accounting_port);second_fault='http://127.0.0.1:'+str(second_fault_port)
            native.start(second_target,second_accounting_port,second_fault_port,recovery_trust,release_trust)
            banks.start(second_target,{w['worker_id']:free_port() for w in bank_workers},recovery_trust,release_trust,str(uuid.uuid4()),'recovery-verifier')
            second_peer_request=str(uuid.uuid4());second_ports={w['worker_id']:free_port() for w in peer_workers}
            denied('Second-generation recovery refuses reusing the preceding physical participant ports',lambda:peers.start(second_target,peer_ports,recovery_trust,release_trust,second_peer_request,'recovery-verifier'),'RECOVERY_PEER_PLAN')
            peers.start(second_target,second_ports,recovery_trust,release_trust,second_peer_request,'recovery-verifier')
            target.rename(target.with_name('first-recovered-unavailable'))
            restore.activate(second_target,recovery_trust,release_trust,second_origin,second_fault,str(uuid.uuid4()),'recovery-verifier')
            check('A second cold generation retains original signed inbox receipts after both preceding installations disappear',PeerClient(PeerIdentity(second_target/'data',peer_reference)).deliver(original_xml,0)==original_receipt)
            second_app=App(second_target/'data',control_dsn=private_read(second_target/'data/control.dsn').decode().strip())
            try:
                next_payment=prepare(second_app,value,145);authorize(second_app,value,next_payment);next_payment=advance(second_app,value,next_payment)
                check('New financial work completes after a second cold restore with each original native leg posted once',next_payment['evidence']['valid'] and all(len(AccountingGateway(second_origin).transactions(bank_ledger,leg['id']))==1 for leg in next_payment['operation']['legs']))
            finally:second_app.close()
            check('Neither restoration contacted an original source participant address',not source_attempts)
            report['second_recovery_set_id']=second_capture['set_id']
        check('Qualification retained one fixed build and one fixed case pack',report['application_fingerprint']==application_fingerprint() and report['case_pack_sha256']==case_pack_hash())
        report.update(passed=True,set_id=captured['set_id'],artifact_id=built['artifact_id'],capture_seconds=captured['capture_seconds'],stage_seconds=staged['stage_seconds'],source_effect_id=str(original_effect[0]['id']),uncertain_payment_id=uncertain['id'],restored_databases=imported['databases'],observed_end_to_end_seconds=time.time()-report['created_at'])
    except Exception as error:report.update(error=type(error).__name__+': '+str(error),traceback=traceback.format_exc())
    finally:
        for child in reversed(children):stop(child)
        if app:app.close()
        observer_stop.set()
        for listener in source_sockets:listener.close()
        for observer in observers:observer.join(timeout=1)
        if target and (target/'data'/routing.FILE).exists():
            try:banks.stop(target)
            except Exception as error:report['bank_cleanup_error']=type(error).__name__+': '+str(error);report['passed']=False
        if second_target:
            for component in [peers,banks,native]:
                try:component.stop(second_target)
                except Exception as error:report['second_cleanup_error']=type(error).__name__+': '+str(error);report['passed']=False
        elif target and (target/'data'/peer_routes.FILE).exists():
            try:peers.stop(target)
            except Exception as error:report['peer_cleanup_error']=type(error).__name__+': '+str(error);report['passed']=False
        if native_started:
            try:native.stop(target)
            except Exception as error:report['cleanup_error']=type(error).__name__+': '+str(error);report['passed']=False
        report['storage_admission']=storage.snapshot()
        report['completed_at']=time.time();write_document(folder/'verification.json',report)
        summary={**report,'evidence':{'path':str(folder/'verification.json'),'sha256':hashlib.sha256((folder/'verification.json').read_bytes()).hexdigest()}}
        write_document(ROOT/'.local'/report_name,summary)
        print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'directory':str(folder),'error':report.get('error'),'cleanup_error':report.get('cleanup_error')}),flush=True)
    return 0 if report['passed'] else 1


def main(xml=False, independent=False):
    try:
        with QualificationStorage(ROOT,'coordinated_recovery') as storage:
            return run(xml,independent,storage)
    except StorageRefused as error:
        name='coordinated-peer-recovery-verification.json' if independent else 'coordinated-upi-wire-recovery-verification.json' if xml else 'coordinated-recovery-verification.json'
        return record_refusal(ROOT,name,error,application_fingerprint(),case_pack_hash())


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--import-crash':raise SystemExit(crash_import(Path(sys.argv[2])))
    if len(sys.argv)==3 and sys.argv[1]=='--activation-crash':raise SystemExit(crash_activation(Path(sys.argv[2])))
    if len(sys.argv)==3 and sys.argv[1]=='--bank-start-crash':raise SystemExit(crash_bank_start(Path(sys.argv[2])))
    raise SystemExit(main(xml='--xml' in sys.argv or '--independent' in sys.argv,independent='--independent' in sys.argv))
