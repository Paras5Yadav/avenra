"""Restore a signed recovery set into new, separately identified databases.

Imports are transactional and resumable by exact row inventories. Activation is
separate; unresolved external service bindings retain the inactive guard.
"""
import hashlib
import hmac
from pathlib import Path
import secrets
import time

from .errors import require
from .lab_identity import private_read
from .private_config import read_document, write_document
from .recovery_state import INACTIVE, engine_bindings
from .recovery_set import database_inventory, pg_command, verify
from .release_runtime import installation_lock, validate_request
from .util import canonical, digest


def checked_stage(directory, recovery_trust, release_trust):
    directory = Path(directory).resolve();stage = read_document(directory / 'recovery-stage.json')
    checked = verify(directory / 'payload', recovery_trust, release_trust)
    require(stage and stage.get('format') == 'avenra-recovery-stage/1' and stage.get('set_id') == checked['set_id'] and stage.get('artifact_id') == checked['manifest']['artifact_id'],
            'RECOVERY_STAGE', 'Stage and authenticated checkpoint disagree.', 409)
    return directory, stage, checked


def grant_runtime(db, migrator, runtime):
    from psycopg import sql
    db.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
    db.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(runtime)))
    rows = db.execute("""SELECT c.relname,EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgrelid=c.oid AND p.proname='avenra_immutable_record' AND (t.tgtype & 16)=16
        AND t.tgqual IS NULL AND t.tgattr=''::int2vector)
        FROM pg_class c WHERE c.relnamespace='public'::regnamespace AND c.relkind='r'""").fetchall()
    readonly = {'avenra_schema_version','fidelity_schema','runtime_schema','aa_schema','ops_schema','outbox_retry_schema','release_active','release_events'}
    for table, immutable in rows:
        permissions = ['SELECT'] if table in readonly else ['SELECT','INSERT'] + ([] if immutable else ['UPDATE'])
        if table in {'sessions','identity_logins'}: permissions.append('DELETE')
        db.execute(sql.SQL('GRANT {} ON TABLE {} TO {}').format(sql.SQL(',').join(map(sql.SQL, permissions)), sql.Identifier(table), sql.Identifier(runtime)))
    db.execute(sql.SQL('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(runtime)))
    db.execute(sql.SQL('ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC').format(sql.Identifier(migrator)))


def restore_databases(directory, recovery_trust, release_trust, administrative_dsn_file, pg_bin, request_id, actor):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    directory, stage, checked = checked_stage(directory, recovery_trust, release_trust)
    validate_request(request_id, None, actor, 'activate')
    admin = private_read(administrative_dsn_file).decode().strip();connection = conninfo_to_dict(admin)
    require(connection.get('host') == '127.0.0.1', 'RECOVERY_DATABASE_TARGET', 'This qualified restore profile creates new databases on an explicit loopback PostgreSQL server.', 409)
    target = {k:v for k,v in connection.items() if k not in {'password','passfile'}}
    payload_hash = digest({'set_id': checked['set_id'], 'request_id': request_id, 'actor': actor, 'target': target})
    with installation_lock(directory):
        require((directory / 'data' / INACTIVE).exists(), 'RECOVERY_ALREADY_ACTIVE', 'A serving recovery cannot be imported again.', 409)
        identity = read_document(directory / 'target-identities.json')
        if identity:
            require(identity['payload_hash'] == payload_hash, 'RECOVERY_CONFLICT', 'Restore identity, actor or target changed; preserve the existing attempt.', 409)
        else:
            suffix = secrets.token_hex(10)
            identity = {'format': 'avenra-recovery-target/1', 'payload_hash': payload_hash, 'request_id': request_id, 'actor': actor, 'set_id': checked['set_id'],
                        'databases': {'control':'avn_restore_' + suffix + '_control','accounting':'avn_restore_' + suffix + '_accounting'},
                        'roles': {r:'avn_restore_' + suffix + '_' + r for r in ['migrate','runtime','engine']},
                        'passwords': {r:secrets.token_urlsafe(36) for r in ['migrate','runtime','engine']}}
            write_document(directory / 'target-identities.json', identity)
        marker = 'avenra-recovery:' + payload_hash
        with psycopg.connect(admin, autocommit=True) as db:
            with db.transaction():
                for role, name in identity['roles'].items():
                    existing = db.execute("SELECT shobj_description(oid,'pg_authid'),rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s", (name,)).fetchone()
                    if existing:
                        require(existing == (marker,False,False,False,False,False), 'RECOVERY_DATABASE_COLLISION', 'A planned recovery role has different ownership or privileges.', 409)
                    else:
                        db.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 16 PASSWORD {}').format(sql.Identifier(name), sql.Literal(identity['passwords'][role])))
                        db.execute(sql.SQL('COMMENT ON ROLE {} IS {}').format(sql.Identifier(name), sql.Literal(marker)))
            for role, name in identity['databases'].items():
                owner = identity['roles']['migrate' if role == 'control' else 'engine']
                existing = db.execute("SELECT pg_get_userbyid(datdba),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s", (name,)).fetchone()
                if existing:
                    require(existing[0] == owner and existing[1] in {None,marker}, 'RECOVERY_DATABASE_COLLISION', 'A planned recovery database belongs to another attempt.', 409)
                else: db.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name), sql.Identifier(owner)))
                db.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(name), sql.Literal(marker)))
                db.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
                if role == 'control': db.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(name), sql.Identifier(identity['roles']['runtime'])))
                db.execute(sql.SQL('ALTER DATABASE {} SET timezone TO {}').format(sql.Identifier(name), sql.Literal(checked['manifest']['database_settings'][role]['timezone'])))
        dsns = {}
        for role in ['migrate','runtime','engine']:
            name = identity['databases']['accounting' if role == 'engine' else 'control']
            dsns[role] = make_conninfo(**{**connection,'dbname':name,'user':identity['roles'][role],'password':identity['passwords'][role]})
        matched = {}
        for role, identity_role in [('control','migrate'),('accounting','engine')]:
            dsn = dsns[identity_role]
            with psycopg.connect(dsn) as db:
                db.execute("SET LOCAL timezone='UTC'");observed = database_inventory(db)
            expected = checked['manifest']['databases'][role]
            if observed != expected:
                require(not observed, 'RECOVERY_DATABASE_CHANGED', 'A recovery database is nonempty and differs from its checkpoint; no overwrite is permitted.', 409)
                pg_command(Path(pg_bin) / 'pg_restore', dsn, directory, role + '-restore.log', ['--single-transaction','--exit-on-error','--no-owner','--no-privileges',str(directory / 'payload/databases' / (role + '.dump'))])
                with psycopg.connect(dsn) as db:
                    db.execute("SET LOCAL timezone='UTC'");observed = database_inventory(db)
                require(observed == expected, 'RECOVERY_DATABASE_INTEGRITY', 'Restored rows differ from the complete original database inventory.', 409)
            matched[role] = digest(observed)
        with psycopg.connect(dsns['migrate'], autocommit=True) as db:
            grant_runtime(db, identity['roles']['migrate'], identity['roles']['runtime'])
        data = directory / 'data'
        for name, dsn in [('control.dsn',dsns['runtime']),('migration.dsn',dsns['migrate']),('accounting.dsn',dsns['engine'])]:
            from .lab_identity import replace_private
            replace_private(data / name, dsn)
        result = {'format':'avenra-recovery-import/1','set_id':checked['set_id'],'request_id':request_id,'actor':actor,'payload_hash':payload_hash,
                  'database_inventory_hashes':matched,'databases':identity['databases'],
                  'credential_hashes':{name:hashlib.sha256(private_read(data/name)).hexdigest() for name in ['control.dsn','migration.dsn','accounting.dsn']},
                  'restored_at':time.time(),'activation':'inactive'}
        write_document(directory / 'recovery-import.json', result)
        return result


def activate(directory, recovery_trust, release_trust, accounting_origin, fault_origin, request_id, actor):
    """Activate recovered local/TLS synthetic participants after explicit rebinding.

    External counterparties/SSO/services need their own coordinated rebind, not a
    flag that silently reuses original endpoints. Their snapshots stay retained.
    """
    import psycopg
    from . import release_bundle as bundle
    from .release_runtime import runtime_receipt, schema_versions, require_deployment_capabilities, LOCK
    from .participant_connections import LOCAL
    from .lab_identity import TLS_LAB, LabIdentity
    from .upi_wire import WIRE_LOCAL,WIRE_TLS
    from .gateways import AccountingGateway, FaultGateway
    directory, stage, checked = checked_stage(directory, recovery_trust, release_trust)
    validate_request(request_id, None, actor, 'activate');data = directory / 'data'
    payload = {'set_id':checked['set_id'],'accounting':accounting_origin,'fault':fault_origin,'request_id':request_id,'actor':actor};hashed = digest(payload)
    with installation_lock(directory):
        previous = read_document(directory / 'recovery-activation.json')
        if previous:
            require(previous['payload_hash'] == hashed, 'RECOVERY_CONFLICT', 'Activation request, actor or endpoints changed.', 409)
            signed_record={k:v for k,v in previous.items() if k!='signature'}
            expected=hmac.new(private_read(data/'evidence.key'),canonical(signed_record).encode(),hashlib.sha256).hexdigest()
            require(hmac.compare_digest(previous.get('signature',''),expected),'RECOVERY_ACTIVATION_INTEGRITY','Recovery activation receipt failed installation authentication.',409)
            if (data / INACTIVE).exists():
                # The durable receipt is published only after all bindings. A
                # crash immediately before guard removal replays that exact
                # decision; it cannot choose new endpoints or financial IDs.
                from .recovery_native import check as check_native
                check_native(directory,accounting_origin,fault_origin)
                if 'bank-recovery.json' in previous['bound_file_hashes']:
                    from .recovery_banks import check as check_banks
                    check_banks(directory)
                if 'aa-recovery.json' in previous['bound_file_hashes']:
                    from .recovery_aa import check as check_aa
                    check_aa(directory)
                if 'peer-recovery.json' in previous['bound_file_hashes']:
                    from .recovery_peers import check as check_peers
                    check_peers(directory)
                if 'issuer-recovery.json' in previous['bound_file_hashes']:
                    from .recovery_identity import check as check_identity
                    check_identity(directory)
                artifact=bundle.verify_installed(directory/'artifacts'/stage['artifact_id'],release_trust);runtime_receipt(artifact,directory/'runtime')
                require_deployment_capabilities(data,artifact,read_document(data/'deployment.json',{}))
                require(all(bundle.sha(data/name)==hashed_file for name,hashed_file in previous['bound_file_hashes'].items()),'RECOVERY_CONFIGURATION_CHANGED','Retain the guard: published recovery bindings changed after activation preparation.',409)
                (data / INACTIVE).unlink()
            return {**previous,'replayed':True}
        imported = read_document(directory / 'recovery-import.json')
        require(imported and imported['set_id'] == checked['set_id'] and (data / INACTIVE).exists(), 'RECOVERY_IMPORT_REQUIRED', 'Complete the authenticated database import before activation.', 409)
        require(all(hashlib.sha256(private_read(data/name)).hexdigest()==value for name,value in imported['credential_hashes'].items()),
                'RECOVERY_DATABASE_TARGET','Recovery database identities changed after authenticated import.',409)
        # Validate endpoint syntax without publishing usable settings.
        binding = {'format':'avenra-loopback-engines/1','accounting':accounting_origin,'fault':fault_origin}
        validation = directory / 'binding-validation';validation.mkdir(mode=0o700,exist_ok=True);write_document(validation / 'engine-endpoints.json',binding);engine_bindings(validation,'','')
        config = read_document(data / 'deployment.json', {})
        services = read_document(data / 'services.json', {})
        identity_config = read_document(data / 'identity.json', {})
        captured_identity = directory / 'payload/private-deployment/identity.json'
        require((not captured_identity.exists() and not (data/'identity.json').exists()) or
                (captured_identity.exists() and (data/'identity.json').exists() and bundle.sha(data/'identity.json')==bundle.sha(captured_identity)),
                'RECOVERY_IDENTITY_BINDING', 'Retain the captured identity settings; recovery cannot disable or replace institutional sign-in.', 409)
        captured_deployment = read_document(directory/'payload/private-deployment/deployment.json',{})
        require(all(config.get(key,{})==captured_deployment.get(key,{}) for key in ['http','identity']),
                'RECOVERY_IDENTITY_BINDING','Retain the captured API TLS, authentication and session policy.',409)
        require(not services.get('services') and not services.get('callback_origins') and not config.get('transport'),
                'RECOVERY_BINDINGS_REQUIRED', 'Retained external service, identity or counterparty bindings require coordinated replacement before activation.', 409)
        recovered_identity = None
        if identity_config.get('mode','local') != 'local':
            require(identity_config.get('mode')=='institutional' and (directory/'identity-service.json').exists(),
                    'RECOVERY_IDENTITY_REQUIRED','Start the captured synthetic issuer through authenticated replacement HTTPS routes before activation.',409)
            from .recovery_identity import check as check_identity
            recovered_identity = check_identity(directory)
        recovered_aa = None;aa_scopes = set()
        if stage['private_roles']['files']:
            require((directory/'aa-services.json').exists(),'RECOVERY_AA_REQUIRED','Start all captured AA listeners through authenticated replacement routes before activation.',409)
            from .recovery_aa import check as check_aa
            from .aa.routing import load as aa_routes
            recovered_aa = check_aa(directory)
            for node_id in aa_routes(data)['nodes']:
                env = node_id.split('/')[0]
                profile = read_document(data/'aa-scopes'/env/'profile.json')
                aa_scopes.add((profile['tenant_id'],profile['environment_id']))
        # Destinations and accepted integration work can outlive their current
        # service configuration. Check retained state, not just settings files,
        # before starting any worker or contacting a native listener.
        from psycopg import sql
        with psycopg.connect(private_read(data/'migration.dsn').decode().strip()) as audit:
            audit.execute('SET TRANSACTION READ ONLY')
            unbound = [table for table in ['bank_callback_destinations','external_commands']
                       if audit.execute(sql.SQL('SELECT 1 FROM {} LIMIT 1').format(sql.Identifier(table))).fetchone()]
            from .aa.manager import TABLES
            for table in TABLES:
                if table=='aa_schema':continue
                scopes = audit.execute(sql.SQL('SELECT DISTINCT tenant_id,environment_id FROM {}').format(sql.Identifier(table))).fetchall()
                if not all(tuple(scope) in aa_scopes for scope in scopes):unbound.append(table)
        require(not unbound,'RECOVERY_BINDINGS_REQUIRED','Retained notification, service or financial-data records require coordinated recovery bindings before activation.',409)
        artifact = bundle.verify_installed(directory / 'artifacts' / stage['artifact_id'], release_trust);runtime_receipt(artifact, directory / 'runtime')
        for role in ['accounting','fault']:
            require(bundle.sha(directory / 'native' / role) == artifact['manifest']['native_services'][role]['sha256'], 'RECOVERY_NATIVE', 'Materialized native service changed after staging.', 409)
        from .recovery_native import check as check_native
        check_native(directory,accounting_origin,fault_origin)
        AccountingGateway(accounting_origin).health();FaultGateway(fault_origin).health()
        recovered_banks = None
        if stage['private_banks']['workers']:
            require((directory/'bank-services.json').exists(), 'RECOVERY_BANK_REQUIRED', 'Start all captured banks through their authenticated new routes before activation.', 409)
            from .recovery_banks import check as check_banks
            recovered_banks = check_banks(directory)
        config.update(database={**config.get('database',{}),'mode':'runtime'},release={'artifact_id':stage['artifact_id'],'application_fingerprint':stage['application_fingerprint']})
        recovered_peers = None
        if stage.get('private_peers', {}).get('workers'):
            require((directory/'peer-services.json').exists(), 'RECOVERY_PEER_REQUIRED',
                    'Start every captured participant through authenticated replacement routes before activation.', 409)
            from .recovery_peers import check as check_peers
            recovered_peers = check_peers(directory)
        if recovered_banks or recovered_aa or recovered_peers or recovered_identity:
            config['recovery'] = {'set_id':checked['set_id']}
            if recovered_banks:config['recovery']['bank_routes_sha256']=bundle.sha(data/'bank-recovery.json')
            if recovered_aa:config['recovery']['aa_routes_sha256']=bundle.sha(data/'aa-recovery.json')
            if recovered_peers:config['recovery']['peer_routes_sha256']=bundle.sha(data/'peer-recovery.json')
            if recovered_identity:config['recovery']['issuer_routes_sha256']=bundle.sha(data/'issuer-recovery.json')
        require_deployment_capabilities(data,artifact,config)
        dsn = private_read(data / 'migration.dsn').decode().strip()
        with psycopg.connect(dsn, autocommit=True) as db:
            require(db.execute('SELECT pg_try_advisory_lock(%s)', (LOCK,)).fetchone()[0], 'RELEASE_NODES_ACTIVE', 'Do not rebind a serving deployment.', 409)
            with db.transaction():
                schema_versions(db,artifact['manifest']['schemas'])
                selected = db.execute('SELECT artifact_id,application_fingerprint,transition_id FROM release_active WHERE singleton=1').fetchone()
                require(selected[:2] == (stage['artifact_id'],stage['application_fingerprint']), 'RECOVERY_RELEASE', 'Restored database selects another release.', 409)
                from .participant_capabilities import ensure
                ensure(db,artifact)
                from .participant_peer_contract import ADAPTER as PEER
                remote = db.execute('SELECT 1 FROM participant_connections WHERE adapter_id NOT IN (%s,%s,%s,%s,%s) LIMIT 1',(LOCAL,TLS_LAB,WIRE_LOCAL,WIRE_TLS,PEER)).fetchone()
                require(not remote, 'RECOVERY_BINDINGS_REQUIRED', 'Retained network participant connections require coordinated replacement before activation.', 409)
                for tenant, environment, participant, raw in db.execute('SELECT tenant_id,environment_id,participant_id,config_json FROM participant_connections WHERE adapter_id IN (%s,%s)',(TLS_LAB,WIRE_TLS)):
                    import json
                    from .lab_bank_client import LabBankClient
                    connection = json.loads(raw)
                    identity = LabIdentity(data,connection['secret_reference'])
                    require(recovered_banks and (identity.document['tenant_id'],identity.document['environment_id'],identity.document['participant_id']) == (tenant,environment,participant),
                            'RECOVERY_BANK_BINDING', 'A retained connection lacks its captured, scoped bank authority.', 409)
                    LabBankClient(identity,connection['endpoint'],connection['credential_binding']).probe()
                for tenant, environment, participant, raw in db.execute('SELECT tenant_id,environment_id,participant_id,config_json FROM participant_connections WHERE adapter_id=%s', (PEER,)):
                    import json
                    from .participant_peer_identity import PeerIdentity
                    from .participant_peer_client import PeerClient
                    connection = json.loads(raw)
                    identity = PeerIdentity(data, connection['secret_reference'])
                    require(recovered_peers and (identity.document['tenant_id'],identity.document['environment_id'],identity.document['participant_id']) == (tenant,environment,participant),
                            'RECOVERY_PEER_BINDING', 'A retained connection lacks its captured scoped participant authority.', 409)
                    PeerClient(identity, connection['endpoint'], connection['credential_binding']).probe()
                # Liveness is deployment-specific. Financial identities, policy
                # history and uncertainty are never rewritten by activation.
                db.execute('UPDATE runtime_workers SET last_seen=0')
                db.execute('UPDATE ops_monitor SET lease_until=0')
            write_document(data / 'deployment.json',config)
            write_document(data / 'engine-endpoints.json',binding)
            # The ordinary local default permits a development callback port.
            # A recovered deployment starts with no implicit callback egress.
            write_document(data / 'services.json',{**services,'callback_origins':[]})
            write_document(data / 'release-selection.json',{'artifact_id':stage['artifact_id'],'application_fingerprint':stage['application_fingerprint'],'artifact_directory':artifact['directory'],'runtime_directory':str((directory / 'runtime').resolve()),'request_id':selected[2]})
            bound_files = ['deployment.json','engine-endpoints.json','services.json','release-selection.json','control.dsn','migration.dsn','accounting.dsn'] + (['bank-recovery.json'] if recovered_banks else []) + (['aa-recovery.json'] if recovered_aa else []) + (['peer-recovery.json'] if recovered_peers else []) + (['issuer-recovery.json','identity.json'] if recovered_identity else [])
            record = {'format':'avenra-recovery-activation/1',**payload,'payload_hash':hashed,'activated_at':time.time(),'artifact_id':stage['artifact_id'],
                      'bound_file_hashes':{name:bundle.sha(data/name) for name in bound_files},
                      'source_release_pointer_preserved':True,'financial_outcomes_rewritten':False,'scope':'Recovered loopback native services, local/TLS synthetic participants and explicitly bound owned synthetic identity; other external roles/services remain gated.'}
            key = private_read(data / 'evidence.key');record['signature'] = hmac.new(key, canonical(record).encode(),hashlib.sha256).hexdigest()
            write_document(directory / 'recovery-activation.json',record)
            (data / INACTIVE).unlink()
        return {**record,'replayed':False}
