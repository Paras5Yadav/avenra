"""Durable participant notices with fenced leases and retained delivery history."""
import json
import math
import os
import secrets
import time
import uuid

from ..errors import DomainError,require
from ..gateways import EngineError
from ..util import canonical,digest
from .contracts import identifier,stamp

DEFAULT={'base_seconds':1.0,'maximum_seconds':60.0,'maximum_attempts':12,'timeout_seconds':8.0,'lease_seconds':30.0}


def validated_policy(given):
    require(isinstance(given,dict) and set(given)<=set(DEFAULT),'AA_NOTICE_POLICY','Unknown notification policy.',503)
    value={**DEFAULT,**given}
    for name in DEFAULT:
        v=value[name]
        require(type(v) in {int,float} and math.isfinite(v),'AA_NOTICE_POLICY','Use finite notification policy values.',503)
    require(.1<=value['base_seconds']<=10 and value['base_seconds']<=value['maximum_seconds']<=300 and 1<=value['timeout_seconds']<=15 and value['timeout_seconds']+1<=value['lease_seconds']<=60,'AA_NOTICE_POLICY','Notification policy is outside its bounded retry/dispatch limits.',503)
    require(type(value['maximum_attempts']) is int and 1<=value['maximum_attempts']<=30,'AA_NOTICE_POLICY','Use 1–30 attempts per explicit retry generation.',503)
    return value


class NoticeQueue:
    def __init__(self,peer):
        self.peer=peer;self.policy=validated_policy(peer.config.get('notice_policy',{}))
        with peer.db(True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS role_schema(version INTEGER NOT NULL)')
            row=db.execute('SELECT version FROM role_schema').fetchone()
            if row is None:db.execute('INSERT INTO role_schema VALUES(1)');version=1
            else:version=row[0]
            require(version in {1,2},'AA_ROLE_SCHEMA','Counterparty journal requires its matching software version.',503)
            if version==1:
                db.execute('CREATE TABLE notice_schedules(notice_id TEXT PRIMARY KEY,next_at REAL NOT NULL,lease_until REAL,token TEXT,last_error TEXT,last_status INTEGER,last_at REAL,generation INTEGER NOT NULL DEFAULT 0,window_attempts INTEGER NOT NULL DEFAULT 0)')
                db.execute('CREATE TABLE notice_attempts(id TEXT PRIMARY KEY,notice_id TEXT NOT NULL,generation INTEGER NOT NULL,attempt INTEGER NOT NULL,started_at REAL NOT NULL,finished_at REAL,outcome TEXT,error TEXT,status INTEGER,sent_hash TEXT NOT NULL,payload_hash TEXT NOT NULL,response_hash TEXT)')
                db.execute('CREATE TABLE notice_retries(id TEXT PRIMARY KEY,notice_id TEXT NOT NULL,actor_hash TEXT NOT NULL,response TEXT NOT NULL,created_at REAL NOT NULL)')
                db.execute('CREATE TABLE notice_observations(id TEXT PRIMARY KEY,attempt_id TEXT NOT NULL,observed_at REAL NOT NULL,outcome TEXT NOT NULL,error TEXT,status INTEGER,response_hash TEXT)')
                db.execute('CREATE INDEX notice_due ON notice_schedules(next_at,lease_until)')
                db.execute('CREATE INDEX notice_history ON notice_attempts(notice_id,started_at)')
                db.execute("CREATE TRIGGER notice_message_immutable BEFORE UPDATE OF id,path,body ON notices WHEN NEW.id IS NOT OLD.id OR NEW.path IS NOT OLD.path OR NEW.body IS NOT OLD.body BEGIN SELECT RAISE(ABORT,'Notification identity and payload are immutable'); END")
                db.execute("CREATE TRIGGER notice_attempt_fixed BEFORE UPDATE ON notice_attempts WHEN OLD.finished_at IS NOT NULL OR NEW.id IS NOT OLD.id OR NEW.notice_id IS NOT OLD.notice_id OR NEW.generation IS NOT OLD.generation OR NEW.attempt IS NOT OLD.attempt OR NEW.started_at IS NOT OLD.started_at OR NEW.sent_hash IS NOT OLD.sent_hash OR NEW.payload_hash IS NOT OLD.payload_hash BEGIN SELECT RAISE(ABORT,'Notification attempts are append-only observations'); END")
                for table in ['notice_attempts','notice_retries','notice_observations']:
                    db.execute(f"CREATE TRIGGER {table}_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'Notification history is immutable'); END")
                for table in ['notice_retries','notice_observations']:
                    db.execute(f"CREATE TRIGGER {table}_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'Notification history is immutable'); END")
                # Retain exhausted legacy work visibly. An operator may recover
                # it under the same identity after inspecting its delivery state.
                for notice in db.execute('SELECT * FROM notices').fetchall():
                    exhausted=notice['state']=='pending' and notice['attempts']>=min(20,self.policy['maximum_attempts'])
                    db.execute('INSERT INTO notice_schedules VALUES(?,0,NULL,NULL,?,NULL,NULL,0,?)',(notice['id'],'LEGACY_ATTEMPTS_EXHAUSTED' if exhausted else None,notice['attempts']))
                    if exhausted:db.execute("UPDATE notices SET state='failed' WHERE id=?",(notice['id'],))
                db.execute('UPDATE role_schema SET version=2')

    def enqueue(self,db,path,body):
        require(identifier(body.get('txnid')),'InvalidRequest','Use a UUID notification identity.')
        db.execute('INSERT INTO notices(id,path,body,state) VALUES(?,?,?,?)',(body['txnid'],path,canonical(body),'pending'))
        db.execute('INSERT INTO notice_schedules(notice_id,next_at) VALUES(?,?)',(body['txnid'],time.time()))

    def claim(self):
        current=time.time()
        with self.peer.db(True) as db:
            expired=db.execute('SELECT notice_id,token FROM notice_schedules WHERE lease_until IS NOT NULL AND lease_until<=?',(current,)).fetchall()
            for row in expired:
                db.execute("UPDATE notice_attempts SET finished_at=?,outcome='unconfirmed',error='WORKER_LEASE_EXPIRED' WHERE id=? AND finished_at IS NULL",(current,row['token']))
                db.execute("UPDATE notice_schedules SET token=NULL,lease_until=NULL,last_error='WORKER_LEASE_EXPIRED' WHERE notice_id=?",(row['notice_id'],))
            db.execute("UPDATE notices SET state='failed' WHERE state='pending' AND id IN (SELECT notice_id FROM notice_schedules WHERE token IS NULL AND window_attempts>=?)",(self.policy['maximum_attempts'],))
            row=db.execute("SELECT n.*,s.generation,s.window_attempts FROM notices n JOIN notice_schedules s ON s.notice_id=n.id WHERE n.state='pending' AND s.next_at<=? AND s.token IS NULL ORDER BY s.next_at,n.id LIMIT 1",(current,)).fetchone()
            if row is None:return None
            row=dict(row);body=json.loads(row['body']);body['timestamp']=stamp();token=str(uuid.uuid4())
            db.execute('UPDATE notice_schedules SET token=?,lease_until=?,window_attempts=window_attempts+1,last_at=? WHERE notice_id=?',(token,current+self.policy['lease_seconds'],current,row['id']))
            db.execute('UPDATE notices SET attempts=attempts+1 WHERE id=?',(row['id'],))
            db.execute('INSERT INTO notice_attempts(id,notice_id,generation,attempt,started_at,sent_hash,payload_hash) VALUES(?,?,?,?,?,?,?)',(token,row['id'],row['generation'],row['attempts']+1,current,digest(body),digest({k:v for k,v in body.items() if k!='timestamp'})))
            return {**row,'body':body,'token':token,'window_attempts':row['window_attempts']+1}

    def complete(self,claim,outcome,error=None,status=None,response_hash=None):
        current=time.time()
        with self.peer.db(True) as db:
            schedule=db.execute('SELECT * FROM notice_schedules WHERE notice_id=?',(claim['id'],)).fetchone()
            if schedule['token']!=claim['token']:
                db.execute('INSERT INTO notice_observations VALUES(?,?,?,?,?,?,?)',(str(uuid.uuid4()),claim['token'],current,outcome,error,status,response_hash));return False
            succeeded=outcome=='delivered';terminal=outcome=='refused' or claim['window_attempts']>=self.policy['maximum_attempts']
            state='delivered' if succeeded else 'failed' if terminal else 'pending'
            delay=min(self.policy['maximum_seconds'],self.policy['base_seconds']*2**min(claim['window_attempts']-1,16))
            # Persist jitter as an absolute due time; a process restart cannot
            # reset the budget or turn a prolonged outage into a tight loop.
            next_at=current+min(self.policy['maximum_seconds'],delay*(.75+secrets.randbelow(501)/1000))
            db.execute('UPDATE notice_attempts SET finished_at=?,outcome=?,error=?,status=?,response_hash=? WHERE id=? AND finished_at IS NULL',(current,outcome,error,status,response_hash,claim['token']))
            db.execute('UPDATE notices SET state=? WHERE id=?',(state,claim['id']))
            db.execute('UPDATE notice_schedules SET token=NULL,lease_until=NULL,last_error=?,last_status=?,next_at=? WHERE notice_id=?',(error,status,next_at,claim['id']))
            return True

    def deliver(self):
        claim=self.claim()
        if claim is None:return False
        try:
            result=self.peer.upstream.call('POST',claim['path'],claim['body'],timeout=self.policy['timeout_seconds'])
            with self.peer.db(True) as db:
                fault=db.execute("SELECT id FROM faults WHERE path='/Notification/after_accept' AND remaining>0 ORDER BY id LIMIT 1").fetchone()
                if fault:db.execute('UPDATE faults SET remaining=remaining-1,triggered=triggered+1 WHERE id=?',(fault['id'],))
            if fault:os._exit(77)  # Deliberate crash of this trusted synthetic role after real recipient commit.
            self.complete(claim,'delivered',status=200,response_hash=digest(result))
        except EngineError:self.complete(claim,'unconfirmed','OUTCOME_UNKNOWN')
        except DomainError as error:self.complete(claim,'retryable' if error.status==429 or error.status>=500 else 'refused',error.code,status=error.status)
        except Exception as error:self.complete(claim,'unconfirmed',type(error).__name__)
        return True

    def status(self,db,data):
        require(set(data)<={'notice_id'},'InvalidRequest','Choose one notice or the bounded delivery overview.')
        identity=data.get('notice_id')
        if identity is not None:require(identifier(identity),'InvalidRequest','Use a UUID notice identity.')
        rows=[dict(r) for r in db.execute('SELECT n.id,n.path,n.state,n.attempts,s.next_at,s.lease_until,s.last_error,s.last_status,s.last_at,s.generation,s.window_attempts FROM notices n JOIN notice_schedules s ON s.notice_id=n.id'+(' WHERE n.id=?' if identity else '')+" ORDER BY CASE n.state WHEN 'failed' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,COALESCE(s.last_at,0) DESC,n.id LIMIT 100",(identity,) if identity else ())]
        if identity:require(rows,'NotFound','Notice is not in this participant journal.',404)
        counts={r['state']:r['count'] for r in db.execute('SELECT state,count(*) count FROM notices GROUP BY state')}
        value={'classification':'synthetic_notification_delivery','journal_version':2,'counts':counts,'notices':rows,'policy':self.policy}
        if identity:
            value['attempts']=[dict(r) for r in db.execute('SELECT * FROM notice_attempts WHERE notice_id=? ORDER BY started_at,id LIMIT 100',(identity,))]
            value['late_observations']=[dict(r) for r in db.execute('SELECT o.* FROM notice_observations o JOIN notice_attempts a ON a.id=o.attempt_id WHERE a.notice_id=? ORDER BY o.observed_at LIMIT 100',(identity,))]
        return value

    def retry(self,db,data):
        require(set(data)=={'notice_id','request_id','actor_hash'} and identifier(data['notice_id']) and identifier(data['request_id']) and isinstance(data['actor_hash'],str) and len(data['actor_hash'])==64,'InvalidRequest','Use a scoped notice, retry identity and authenticated actor hash.')
        old=db.execute('SELECT * FROM notice_retries WHERE id=?',(data['request_id'],)).fetchone()
        if old:
            require(old['notice_id']==data['notice_id'] and old['actor_hash']==data['actor_hash'],'CONFLICT','Retry identity has changed scope.',409);return json.loads(old['response'])
        row=db.execute('SELECT n.state,s.token,s.generation FROM notices n JOIN notice_schedules s ON s.notice_id=n.id WHERE n.id=?',(data['notice_id'],)).fetchone();require(row,'NotFound','Notification is not in this participant journal.',404)
        require(row['state']=='failed' and not row['token'],'NOTICE_NOT_FAILED','Only an exhausted or refused notification needs an explicit retry.',409)
        response={'classification':'synthetic_notification_retry','notice_id':data['notice_id'],'request_id':data['request_id'],'generation':row['generation']+1,'state':'pending'}
        db.execute("UPDATE notices SET state='pending' WHERE id=?",(data['notice_id'],))
        db.execute('UPDATE notice_schedules SET generation=generation+1,window_attempts=0,next_at=? WHERE notice_id=?',(time.time(),data['notice_id']))
        db.execute('INSERT INTO notice_retries VALUES(?,?,?,?,?)',(data['request_id'],data['notice_id'],data['actor_hash'],canonical(response),time.time()))
        return response
