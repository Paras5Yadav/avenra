"""Scoped operational observations and lease-fenced incidents in the current app.

Monitoring never dispatches financial work or treats acknowledgement as recovery.
Only a current, fenced observation can open/resolve an incident. Current samples
are bounded rows; immutable transition records retain the evidence that mattered.
"""
import base64
import hashlib
import hmac
import json
import logging
import math
from pathlib import Path
import re
import threading
import time

from .errors import DomainError, require
from .private_config import deployment
from .util import canonical, digest, now, reference, uid

TABLES = ['ops_schema', 'ops_policies', 'ops_bindings', 'ops_observations', 'ops_rejections', 'ops_monitor', 'ops_alerts', 'ops_alert_events']
DEFAULT = {'window_seconds': 300, 'sample_interval_seconds': 2, 'queue_age_seconds': 30, 'queue_depth': 2000,
           'unknown_age_seconds': 60, 'failed_records': 1, 'request_errors': 5, 'rejections': 20,
           'request_p95_ms': 2000, 'minimum_latency_samples': 20, 'worker_grace_seconds': 10,
           'for_seconds': 10, 'recovery_seconds': 10}
BOUNDS = {'window_seconds': (30,3600), 'sample_interval_seconds': (1,60), 'queue_age_seconds': (1,86400), 'queue_depth': (1,100000),
          'unknown_age_seconds': (1,86400), 'failed_records': (1,100000), 'request_errors': (1,100000), 'rejections': (1,100000),
          'request_p95_ms': (1,120000), 'minimum_latency_samples': (1,5000), 'worker_grace_seconds': (6,300),
          'for_seconds': (0,3600), 'recovery_seconds': (0,3600)}
SIGNALS = {
    'worker_unavailable': ('Worker unavailable', 'Restore a matching worker. Retain accepted identities and inspect readiness before resuming traffic.'),
    'accounting_unavailable': ('Accounting unavailable', 'Restore the original accounting dependency. Reconcile uncertain effects under their original identities.'),
    'queue_lag': ('Due work is delayed', 'Inspect worker health and due jobs. Preserve original outbox identities; do not create replacement financial requests.'),
    'queue_depth': ('Pending-work capacity', 'Reduce new admission or restore capacity while keeping original status and reconciliation available.'),
    'outcome_unknown': ('Unconfirmed operations', 'Inspect original payment/operation evidence and reconcile each original identity. A timeout is not a decline.'),
    'failed_work': ('Failed delivery/work records', 'Inspect failed outbox and participant delivery records. Use supported original-ID recovery after correcting the cause.'),
    'request_errors': ('Request failures', 'Correlate client deadlines with eventual domain outcomes and dependency health before retrying original requests.'),
    'request_latency': ('Request latency', 'Inspect capacity, dependency latency and physical dispatch occupancy. The reported percentile is a bounded recent sample.'),
    'admission_rejections': ('Admission pressure', 'Inspect the recorded rate/concurrency/queue refusal codes and tune the declared workload or capacity.'),
}


def schema_hash(): return hashlib.sha256(Path(__file__).with_name('operations_schema.sql').read_bytes()).hexdigest()


def install_schema(db, postgres, installation=None):
    """Closed, additive migration recipe; caller owns the migration transaction."""
    source = Path(__file__).with_name('operations_schema.sql').read_text()
    if postgres: source = source.replace(' REAL', ' DOUBLE PRECISION').replace('sequence INTEGER PRIMARY KEY AUTOINCREMENT','sequence BIGSERIAL PRIMARY KEY')
    for statement in source.split(';'):
        if statement.strip(): db.execute(statement)
    db.execute('INSERT INTO ops_schema VALUES(?,?,?,?)', (1,schema_hash(),now(),canonical(installation or {'mode':'explicit_local_or_fresh_install','actor':'installation'})))
    for table in ['ops_schema', 'ops_policies', 'ops_alert_events']:
        for action in ['UPDATE', 'DELETE']:
            if postgres: db.execute(f'CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record()')
            else: db.execute(f"CREATE TRIGGER {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Operational history is immutable'); END")


class Operations:
    def __init__(self, runtime):
        self.runtime, self.app, self.store = runtime, runtime.app, runtime.store
        self.thread = None
        with self.store.transaction() as db:
            present = db.execute("SELECT to_regclass('ops_schema')").fetchone()[0] if hasattr(self.store, 'pool') else db.execute("SELECT 1 FROM sqlite_master WHERE name='ops_schema' AND type='table'").fetchone()
            if not present:
                require(deployment(self.store.directory).get('database', {}).get('mode') != 'runtime', 'MIGRATION_REQUIRED', 'Install the additive operations schema with the offline migration identity before serving.', 503)
                install_schema(db, hasattr(self.store, 'pool'))
            rows = db.execute('SELECT version,source_sha256 FROM ops_schema').fetchall()
            require(len(rows) == 1 and rows[0][0] == 1 and rows[0][1] == schema_hash(), 'MIGRATION_REQUIRED', 'Operations schema checksum/version requires its matching reviewed migration.', 503)

    def bound(self, db, tenant, env):
        value = db.execute('SELECT p.* FROM ops_policies p JOIN ops_bindings b ON b.policy_id=p.id WHERE b.tenant_id=? AND b.environment_id=?', (tenant, env)).fetchone()
        if not value: return None
        item = dict(value);document = json.loads(item['document_json']);hashed, signature = self.runtime.signed(document)
        require(item['document_hash'] == hashed and hmac.compare_digest(item['signature'], signature), 'MONITOR_POLICY_INTEGRITY', 'Monitoring policy failed integrity verification.', 503)
        item['document'] = document;return item

    def event(self, db, tenant, env, alert, kind, metrics, actor=None, key=None, hashed=None):
        identity = uid('incident_event');timestamp = self.runtime.clock(db)
        db.execute('INSERT INTO ops_alert_events(id,tenant_id,environment_id,alert_id,kind,actor,request_key,request_hash,recorded_at,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?)', (identity,tenant,env,alert,kind,actor,key,hashed,timestamp,canonical(metrics)))
        return identity

    def publish(self, tenant, env, data, actor):
        self.app.environment(tenant, env)
        require(isinstance(data, dict) and set(data) == set(DEFAULT) | {'request_key'}, 'MONITOR_POLICY', 'Publish the complete bounded operational policy.')
        key = reference(data['request_key']);document = {k:data[k] for k in DEFAULT}
        require(all(type(document[k]) is int and low <= document[k] <= high for k,(low,high) in BOUNDS.items()), 'MONITOR_POLICY', 'Monitoring thresholds exceed their supported bounds.')
        hashed, signature = self.runtime.signed(document)
        with self.store.transaction() as db:
            old = db.execute('SELECT * FROM ops_policies WHERE tenant_id=? AND environment_id=? AND request_key=?', (tenant,env,key)).fetchone()
            if old:
                require(old['document_hash'] == hashed and old['actor'] == actor, 'IDEMPOTENCY_CONFLICT', 'Monitoring policy identity changed content or actor.', 409);return dict(old)
            version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM ops_policies WHERE tenant_id=? AND environment_id=?', (tenant,env)).fetchone()[0]
            bound = db.execute('SELECT 1 FROM ops_bindings WHERE tenant_id=? AND environment_id=?',(tenant,env)).fetchone()
            require(bound or db.execute('SELECT count(*) FROM ops_bindings').fetchone()[0]<1000,'MONITOR_CAPACITY','This deployment supports at most 1,000 configured monitoring scopes.',409)
            identity = uid('monitor_policy')
            db.execute('INSERT INTO ops_policies VALUES(?,?,?,?,?,?,?,?,?,?)', (identity,tenant,env,version,canonical(document),hashed,signature,key,actor,now()))
            db.execute('INSERT INTO ops_bindings VALUES(?,?,?) ON CONFLICT(tenant_id,environment_id) DO UPDATE SET policy_id=excluded.policy_id', (tenant,env,identity))
            self.store.audit(db,tenant,env,'operations.policy_published',identity,{'actor':actor,'document_hash':hashed,'version':version})
            self.event(db,tenant,env,None,'policy_published',{'policy_id':identity,'document_hash':hashed,'version':version},actor)
            return dict(db.execute('SELECT * FROM ops_policies WHERE id=?', (identity,)).fetchone())

    def rejection(self, tenant, env, code):
        if not env or code not in {'TENANT_CAPACITY','REQUEST_RATE','QUEUE_CAPACITY'}: return
        with self.store.transaction() as db:
            second = int(self.runtime.clock(db));slot = second % 4096
            old = db.execute('SELECT epoch_second,count FROM ops_rejections WHERE tenant_id=? AND environment_id=? AND slot=? AND code=?', (tenant,env,slot,code)).fetchone()
            if not old: db.execute('INSERT INTO ops_rejections VALUES(?,?,?,?,?,1)', (tenant,env,slot,code,second))
            elif old['epoch_second'] <= second:
                db.execute('UPDATE ops_rejections SET epoch_second=?,count=? WHERE tenant_id=? AND environment_id=? AND slot=? AND code=?', (second,old['count']+1 if old['epoch_second']==second else 1,tenant,env,slot,code))

    def claim(self):
        with self.store.transaction() as db:
            timestamp = self.runtime.clock(db);old = db.execute('SELECT * FROM ops_monitor WHERE singleton=1').fetchone()
            if old and old['lease_until'] > timestamp and old['owner'] != self.runtime.node_id: return None
            token = old['token'] if old and old['owner'] == self.runtime.node_id and old['lease_until'] > timestamp else uid('monitor_lease')
            if old:
                db.execute('UPDATE ops_monitor SET owner=?,token=?,lease_until=?,source_fingerprint=? WHERE singleton=1', (self.runtime.node_id,token,timestamp+15,self.app.source_fingerprint))
            else: db.execute("INSERT INTO ops_monitor VALUES(1,?,?,?,?,NULL,'')", (self.runtime.node_id,token,timestamp+15,self.app.source_fingerprint))
            return token

    def collect(self, tenant, env, policy, dependency):
        with self.store.reader() as db:
            if hasattr(self.store, 'pool'): db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            timestamp = self.runtime.clock(db);start = timestamp-policy['window_seconds']
            metrics = {'observed_at':timestamp,'window_seconds':policy['window_seconds'],'rejection_bucket_seconds':1}
            latest = db.execute('SELECT MAX(last_seen) FROM runtime_workers WHERE role LIKE ? AND source_fingerprint=?', ('%worker%',self.app.source_fingerprint)).fetchone()[0]
            metrics['worker_age_seconds'] = max(0,timestamp-latest) if latest is not None else None
            metrics['worker_available'] = latest is not None and timestamp-latest <= policy['worker_grace_seconds']
            metrics['accounting_available'] = next((x['ready'] for x in dependency['checks'] if x['component']=='accounting'), None)
            queue = [dict(x) for x in db.execute('SELECT state,count(*) n FROM outbox WHERE tenant_id=? AND environment_id=? GROUP BY state', (tenant,env))]
            metrics['queue_states'] = {x['state']:x['n'] for x in queue};metrics['pending_jobs'] = sum(x['n'] for x in queue if x['state'] in {'ready','leased'})
            due = db.execute("SELECT MIN(available_at) FROM outbox WHERE tenant_id=? AND environment_id=? AND state='ready' AND available_at<=?", (tenant,env,timestamp)).fetchone()[0]
            expired = db.execute("SELECT MIN(lease_until) FROM outbox WHERE tenant_id=? AND environment_id=? AND state='leased' AND lease_until<?", (tenant,env,timestamp)).fetchone()[0]
            due_values = [x for x in [due,expired] if x is not None];metrics['oldest_due_seconds'] = max(0,timestamp-min(due_values)) if due_values else 0
            unknown = {}
            from datetime import datetime
            for table in ['payments','bank_operations','upi_payments']:
                row = db.execute("SELECT count(*) n,MIN(updated_at) oldest FROM " + table + " WHERE tenant_id=? AND environment_id=? AND state='unknown'", (tenant,env)).fetchone()
                unknown[table] = {'count':row['n'],'oldest_seconds':max(0,timestamp-datetime.fromisoformat(row['oldest'].replace('Z','+00:00')).timestamp()) if row['oldest'] else 0}
            metrics['unknown_by_layer'] = unknown
            # These are record counts by system, not a claim of unique financial
            # intents: one journey can have records at more than one layer.
            failed = {'outbox':metrics['queue_states'].get('failed',0)}
            failed['aa_deliveries'] = db.execute("SELECT count(*) FROM aa_jobs WHERE tenant_id=? AND environment_id=? AND state='failed'", (tenant,env)).fetchone()[0]
            failed['callbacks'] = db.execute("SELECT count(*) FROM bank_notifications n JOIN bank_operations o ON o.id=n.operation_id WHERE o.tenant_id=? AND o.environment_id=? AND n.state='dead_letter'", (tenant,env)).fetchone()[0]
            metrics['failed_by_system'] = failed
            metrics['rejections'] = {x['code']:x['n'] for x in db.execute('SELECT code,SUM(count) n FROM ops_rejections WHERE tenant_id=? AND environment_id=? AND epoch_second>=? AND epoch_second<=? GROUP BY code', (tenant,env,int(start),int(timestamp)))}
            totals = db.execute('SELECT count(*) n,COALESCE(SUM(CASE WHEN status>=500 THEN 1 ELSE 0 END),0) errors,COALESCE(SUM(deadline_exceeded),0) deadlines FROM runtime_requests WHERE tenant_id=? AND environment_id=? AND started_at>=?', (tenant,env,start)).fetchone()
            metrics.update(requests=totals['n'],request_errors=totals['errors'],request_deadlines=totals['deadlines'])
            rows = db.execute('SELECT started_at,completed_at FROM runtime_requests WHERE tenant_id=? AND environment_id=? AND started_at>=? AND completed_at IS NOT NULL ORDER BY started_at DESC LIMIT 5001', (tenant,env,start)).fetchall()
            metrics['latency_sample_truncated'] = len(rows)>5000;latencies = sorted(max(0,(r['completed_at']-r['started_at'])*1000) for r in rows[:5000])
            metrics['latency_sample_size'] = len(latencies);metrics['request_p95_ms'] = latencies[max(0,math.ceil(len(latencies)*.95)-1)] if len(latencies)>=policy['minimum_latency_samples'] else None
            metrics['request_observation_enabled'] = bool(self.runtime.policy(db,tenant,env) or self.bound(db,tenant,env))
        return metrics

    def conditions(self, metrics, policy):
        return {'worker_unavailable':not metrics['worker_available'],
                'accounting_unavailable':None if metrics['accounting_available'] is None else not metrics['accounting_available'],
                'queue_lag':metrics['oldest_due_seconds']>=policy['queue_age_seconds'], 'queue_depth':metrics['pending_jobs']>=policy['queue_depth'],
                'outcome_unknown':any(x['count'] and x['oldest_seconds']>=policy['unknown_age_seconds'] for x in metrics['unknown_by_layer'].values()),
                'failed_work':any(x>=policy['failed_records'] for x in metrics['failed_by_system'].values()),
                'request_errors':metrics['request_errors']>=policy['request_errors'] if metrics['request_observation_enabled'] else None,
                'request_latency':metrics['request_p95_ms']>=policy['request_p95_ms'] if metrics['request_p95_ms'] is not None else None,
                'admission_rejections':sum(metrics['rejections'].values())>=policy['rejections']}

    def apply(self, tenant, env, policy, metrics, token):
        with self.store.transaction() as db:
            timestamp = self.runtime.clock(db);lease = db.execute('SELECT * FROM ops_monitor WHERE singleton=1').fetchone();current = self.bound(db,tenant,env)
            require(lease and lease['token']==token and lease['owner']==self.runtime.node_id and lease['lease_until']>timestamp and current and current['id']==policy['id'],
                    'MONITOR_FENCED', 'Discard stale monitor ownership or a superseded policy observation.', 409)
            # A late observation must not reset dwell/recovery timing or resolve
            # an incident using a view older than the currently retained sample.
            old = db.execute('SELECT observed_at FROM ops_observations WHERE tenant_id=? AND environment_id=?', (tenant,env)).fetchone()
            require(not old or metrics['observed_at']>old[0], 'MONITOR_STALE', 'Discard an older operational observation.', 409)
            db.execute('INSERT INTO ops_observations VALUES(?,?,?,?,?,?) ON CONFLICT(tenant_id,environment_id) DO UPDATE SET policy_id=excluded.policy_id,observed_at=excluded.observed_at,source_fingerprint=excluded.source_fingerprint,metrics_json=excluded.metrics_json',
                       (tenant,env,policy['id'],metrics['observed_at'],self.app.source_fingerprint,canonical(metrics)))
            gap = bool(old and metrics['observed_at']-old[0]>max(20,policy['document']['sample_interval_seconds']*3))
            for signal,condition in self.conditions(metrics,policy['document']).items():
                rows = db.execute("SELECT * FROM ops_alerts WHERE tenant_id=? AND environment_id=? AND signal=? AND state!='resolved' ORDER BY first_seen,id", (tenant,env,signal)).fetchall()
                # Policy changes preserve incident continuity. They do not
                # automatically clear a currently firing problem.
                alert = dict(rows[0]) if rows else None
                if condition is None:
                    if alert:
                        if alert['state']=='recovering':
                            db.execute("UPDATE ops_alerts SET state='firing',recovery_since=NULL,changed_at=? WHERE id=?",(timestamp,alert['id']))
                            self.event(db,tenant,env,alert['id'],'recovery_unconfirmed',{'policy_id':policy['id'],'signal':signal,'metrics':metrics})
                        db.execute('UPDATE ops_alerts SET last_seen=?,latest_metrics_json=? WHERE id=?', (timestamp,canonical(metrics),alert['id']))
                    continue
                if condition and not alert:
                    episode = db.execute('SELECT COALESCE(MAX(episode),0)+1 FROM ops_alerts WHERE tenant_id=? AND environment_id=? AND policy_id=? AND signal=?', (tenant,env,policy['id'],signal)).fetchone()[0]
                    identity = uid('incident');state = 'firing' if policy['document']['for_seconds']==0 else 'pending'
                    db.execute('INSERT INTO ops_alerts(id,tenant_id,environment_id,policy_id,signal,episode,state,first_seen,last_seen,condition_since,recovery_since,changed_at,acknowledged_by,acknowledged_at,latest_metrics_json) VALUES(?,?,?,?,?,?,?,?,?,?,NULL,?,NULL,NULL,?)', (identity,tenant,env,policy['id'],signal,episode,state,timestamp,timestamp,timestamp,timestamp,canonical(metrics)))
                    self.event(db,tenant,env,identity,state,{'policy_id':policy['id'],'signal':signal,'metrics':metrics});continue
                if not alert: continue
                state, since, recovery = alert['state'], alert['condition_since'], alert['recovery_since']
                if gap:
                    since=timestamp
                    if state=='recovering':state='firing';recovery=None
                if condition:
                    # Unknown telemetry does not establish continuous failure.
                    # Retain the pending identity, but start its dwell anew at
                    # the first confirmed failing observation after that gap.
                    if state=='pending' and self.conditions(json.loads(alert['latest_metrics_json']),policy['document'])[signal] is None:since=timestamp
                    if state=='recovering': state='firing';recovery=None;since=timestamp
                    elif state=='pending' and timestamp-since>=policy['document']['for_seconds']: state='firing'
                elif state=='pending': state='resolved'
                elif state=='firing':
                    state = 'resolved' if policy['document']['recovery_seconds']==0 else 'recovering';recovery=timestamp
                elif state=='recovering' and timestamp-recovery>=policy['document']['recovery_seconds']: state='resolved'
                changed = state!=alert['state']
                db.execute('UPDATE ops_alerts SET state=?,last_seen=?,condition_since=?,recovery_since=?,changed_at=?,latest_metrics_json=? WHERE id=?',
                           (state,timestamp,since,recovery,timestamp if changed else alert['changed_at'],canonical(metrics),alert['id']))
                if changed: self.event(db,tenant,env,alert['id'],state,{'policy_id':policy['id'],'signal':signal,'metrics':metrics})
            db.execute('UPDATE ops_monitor SET completed_at=?,cursor_environment=? WHERE singleton=1 AND token=?', (timestamp,env,token))

    def cycle(self):
        token = self.claim()
        if not token: return 0
        with self.store.reader() as db:
            cursor = db.execute('SELECT cursor_environment FROM ops_monitor WHERE singleton=1').fetchone()[0]
            rows = db.execute('SELECT tenant_id,environment_id FROM ops_bindings WHERE environment_id>? ORDER BY environment_id LIMIT 50', (cursor,)).fetchall()
            if not rows: rows = db.execute('SELECT tenant_id,environment_id FROM ops_bindings ORDER BY environment_id LIMIT 50').fetchall()
        dependency = self.runtime.readiness(include_operations=False) if rows else None;completed=0
        for row in rows:
            if self.app.stop_event.is_set(): break
            with self.store.reader() as db:
                policy = self.bound(db,row['tenant_id'],row['environment_id']);old = db.execute('SELECT observed_at FROM ops_observations WHERE tenant_id=? AND environment_id=?', (row['tenant_id'],row['environment_id'])).fetchone();timestamp=self.runtime.clock(db)
            if old and timestamp-old[0]<policy['document']['sample_interval_seconds']:
                with self.store.transaction() as db:
                    db.execute('UPDATE ops_monitor SET cursor_environment=? WHERE singleton=1 AND token=? AND owner=? AND lease_until>?',(row['environment_id'],token,self.runtime.node_id,self.runtime.clock(db)))
                continue
            try:
                metrics = self.collect(row['tenant_id'],row['environment_id'],policy['document'],dependency)
                self.apply(row['tenant_id'],row['environment_id'],policy,metrics,token);completed+=1
            except DomainError as error:
                if error.code=='MONITOR_FENCED':raise
                logging.exception('Scoped operational observation failed')
            except Exception:logging.exception('Scoped operational observation failed')
            with self.store.transaction() as db:
                db.execute('UPDATE ops_monitor SET cursor_environment=? WHERE singleton=1 AND token=? AND owner=? AND lease_until>?',(row['environment_id'],token,self.runtime.node_id,self.runtime.clock(db)))
        # Empty deployments still expose monitor liveness; it is not financial
        # worker readiness and never substitutes for the worker heartbeat.
        with self.store.transaction() as db:
            db.execute('UPDATE ops_monitor SET completed_at=? WHERE singleton=1 AND token=? AND owner=?', (self.runtime.clock(db),token,self.runtime.node_id))
        return completed

    def start(self):
        if self.thread: return
        def work():
            while not self.app.stop_event.is_set():
                try: self.cycle()
                except DomainError as error:
                    if error.code not in {'MONITOR_FENCED','MONITOR_STALE'}: logging.exception('Operational observation failed')
                except Exception: logging.exception('Operational monitor failed; freshness must remain visible')
                self.app.stop_event.wait(1)
        self.thread = threading.Thread(target=work,name='avenra-operations',daemon=True);self.thread.start()

    def stop(self):
        if self.thread:
            self.thread.join(timeout=12)
            require(not self.thread.is_alive(), 'MONITOR_DRAIN', 'Operational monitor has not drained; preserve its lease for fenced recovery.', 503)
        with self.store.transaction() as db:
            db.execute('UPDATE ops_monitor SET lease_until=? WHERE singleton=1 AND owner=?',(self.runtime.clock(db),self.runtime.node_id))

    def health(self):
        with self.store.reader() as db:
            active = db.execute('SELECT count(*) FROM ops_bindings').fetchone()[0];lease = db.execute('SELECT * FROM ops_monitor WHERE singleton=1').fetchone();timestamp=self.runtime.clock(db)
            observations=db.execute('SELECT b.policy_id,p.document_json,o.policy_id observed_policy,o.source_fingerprint,o.observed_at FROM ops_bindings b JOIN ops_policies p ON p.id=b.policy_id LEFT JOIN ops_observations o ON o.tenant_id=b.tenant_id AND o.environment_id=b.environment_id LIMIT 1001').fetchall()
        fresh = bool(lease and lease['source_fingerprint']==self.app.source_fingerprint and lease['lease_until']>timestamp and lease['completed_at'] and timestamp-lease['completed_at']<20)
        stale=sum(1 for row in observations if row['observed_policy']!=row['policy_id'] or row['source_fingerprint']!=self.app.source_fingerprint or row['observed_at'] is None or timestamp-row['observed_at']>max(20,json.loads(row['document_json'])['sample_interval_seconds']*3))
        return {'ready':not active or (fresh and not stale and active<=1000),'configured_scopes':active,'stale_scopes':stale,'monitor_age_seconds':max(0,timestamp-lease['completed_at']) if lease and lease['completed_at'] else None}

    def acknowledge(self, tenant, env, identity, data, actor):
        self.app.environment(tenant,env);require(isinstance(data,dict) and set(data)=={'request_key'}, 'INCIDENT_REQUEST', 'Use a stable acknowledgement request key.')
        key=reference(data['request_key']);hashed=digest({'alert_id':identity,'actor':actor})
        with self.store.transaction() as db:
            alert=db.execute('SELECT * FROM ops_alerts WHERE id=? AND tenant_id=? AND environment_id=?',(identity,tenant,env)).fetchone();require(alert,'NOT_FOUND','Operational incident not found.',404)
            old=db.execute('SELECT request_hash,alert_id FROM ops_alert_events WHERE tenant_id=? AND environment_id=? AND request_key=?',(tenant,env,key)).fetchone()
            if old:
                require(old['request_hash']==hashed and old['alert_id']==identity,'IDEMPOTENCY_CONFLICT','Acknowledgement identity changed actor or incident.',409);return {'id':identity,'acknowledged':True,'replayed':True}
            timestamp=self.runtime.clock(db);db.execute('UPDATE ops_alerts SET acknowledged_by=?,acknowledged_at=? WHERE id=?',(actor,timestamp,identity))
            self.event(db,tenant,env,identity,'acknowledged',{'state':alert['state'],'acknowledged_at':timestamp},actor,key,hashed)
            self.store.audit(db,tenant,env,'operations.incident_acknowledged',identity,{'actor':actor,'state':alert['state'],'request_key':key})
        return {'id':identity,'acknowledged':True,'replayed':False}

    def cursor(self, tenant, env, position, kind='incidents', identity=None):
        value={'tenant':tenant,'environment':env,'position':position,'kind':kind,'identity':identity};raw=canonical(value).encode();signature=hmac.new(self.store.signing_key,raw,hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw+signature).decode().rstrip('=')

    def position(self, tenant, env, cursor, kind='incidents', identity=None):
        if not cursor: return None
        try:
            require(isinstance(cursor,str) and len(cursor)<=1200,'INCIDENT_CURSOR','Invalid incident cursor.');raw=base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4));document,signature=raw[:-32],raw[-32:]
            value=json.loads(document);valid=hmac.compare_digest(signature,hmac.new(self.store.signing_key,document,hashlib.sha256).digest())
            require(valid and value['tenant']==tenant and value['environment']==env and value['kind']==kind and value['identity']==identity and isinstance(value['position'],list) and len(value['position'])==2,'INCIDENT_CURSOR','Incident cursor changed scope or content.')
            stamp,row_id=value['position'];pattern='incident_[a-z0-9]+' if kind=='incidents' else 'incident_event_[a-z0-9]+'
            require(type(stamp) is int and stamp>0 and isinstance(row_id,str) and re.fullmatch(pattern,row_id),'INCIDENT_CURSOR','Invalid incident position.')
            return stamp,row_id
        except (ValueError,KeyError,TypeError):raise DomainError('INCIDENT_CURSOR','Invalid incident cursor.')

    def overview(self, tenant, env, cursor=None):
        self.app.environment(tenant,env);position=self.position(tenant,env,cursor)
        with self.store.reader() as db:
            if hasattr(self.store,'pool'):db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            policy=self.bound(db,tenant,env);observation=db.execute('SELECT * FROM ops_observations WHERE tenant_id=? AND environment_id=?',(tenant,env)).fetchone();timestamp=self.runtime.clock(db)
            tail=' AND sequence<?' if position else '';parameters=[tenant,env,*([position[0]] if position else [])]
            rows=[dict(x) for x in db.execute('SELECT * FROM ops_alerts WHERE tenant_id=? AND environment_id=?'+tail+' ORDER BY sequence DESC LIMIT 51',parameters)]
            open_count=db.execute("SELECT count(*) FROM ops_alerts WHERE tenant_id=? AND environment_id=? AND state!='resolved'",(tenant,env)).fetchone()[0]
            open_rows=[dict(x) for x in db.execute("SELECT * FROM ops_alerts WHERE tenant_id=? AND environment_id=? AND state!='resolved' ORDER BY sequence LIMIT 16",(tenant,env))]
        more=len(rows)>50;rows=rows[:50]
        for row in rows:
            row['title'],row['action']=SIGNALS[row['signal']];row['latest_metrics']=json.loads(row.pop('latest_metrics_json'))
        for row in open_rows:
            row['title'],row['action']=SIGNALS[row['signal']];row['latest_metrics']=json.loads(row.pop('latest_metrics_json'))
        return {'policy':policy,'defaults':DEFAULT,'signals':SIGNALS,'observation':{**dict(observation),'metrics':json.loads(observation['metrics_json'])} if observation else None,
                'observation_fresh':bool(policy and observation and observation['policy_id']==policy['id'] and observation['source_fingerprint']==self.app.source_fingerprint and timestamp-observation['observed_at']<=max(20,policy['document']['sample_interval_seconds']*3)),
                'open_count':open_count,'open_incidents':open_rows,'incidents':rows,'next_cursor':self.cursor(tenant,env,[rows[-1]['sequence'],rows[-1]['id']]) if more else None,
                'scope':'Current scoped observations and retained incident transitions; acknowledgement is not recovery. No external notifications are sent.'}

    def history(self, tenant, env, identity, cursor=None):
        self.app.environment(tenant,env);position=self.position(tenant,env,cursor,'events',identity)
        with self.store.reader() as db:
            require(db.execute('SELECT 1 FROM ops_alerts WHERE id=? AND tenant_id=? AND environment_id=?',(identity,tenant,env)).fetchone(),'NOT_FOUND','Operational incident not found.',404)
            tail=' AND sequence<?' if position else '';parameters=[identity,tenant,env,*([position[0]] if position else [])]
            rows=[dict(x) for x in db.execute('SELECT * FROM ops_alert_events WHERE alert_id=? AND tenant_id=? AND environment_id=?'+tail+' ORDER BY sequence DESC LIMIT 51',parameters)]
        more=len(rows)>50;rows=rows[:50]
        for row in rows:row['payload']=json.loads(row.pop('payload_json'))
        return {'events':rows,'next_cursor':self.cursor(tenant,env,[rows[-1]['sequence'],rows[-1]['id']],'events',identity) if more else None,'alert_id':identity}
