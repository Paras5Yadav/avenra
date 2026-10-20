ALTER TABLE api_keys ADD COLUMN revoked_at TEXT;
ALTER TABLE api_keys ADD COLUMN expires_at REAL;
ALTER TABLE api_keys ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '["read","write"]';
CREATE TABLE bank_callback_destinations(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,name TEXT NOT NULL,url TEXT NOT NULL,
 key_version INTEGER NOT NULL DEFAULT 1,status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
ALTER TABLE bank_operations ADD COLUMN callback_id TEXT REFERENCES bank_callback_destinations(id);
CREATE TABLE bank_notifications(
 id TEXT PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE REFERENCES bank_operations(id),destination_id TEXT NOT NULL REFERENCES bank_callback_destinations(id),
 payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,key_version INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','delivered','dead_letter')),created_at TEXT NOT NULL,delivered_at TEXT
);
CREATE TABLE bank_notification_attempts(id TEXT PRIMARY KEY,event_id TEXT NOT NULL REFERENCES bank_notifications(id),http_status INTEGER NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE external_commands(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,service TEXT NOT NULL,action TEXT NOT NULL,
 path TEXT NOT NULL,body_json TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','unknown','succeeded','rejected')),result_json TEXT,error TEXT,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,service,request_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TRIGGER bank_cycle_reports_no_update BEFORE UPDATE ON bank_cycle_reports BEGIN SELECT RAISE(ABORT,'Reports are append-only'); END;
CREATE TRIGGER bank_cycle_reports_no_delete BEFORE DELETE ON bank_cycle_reports BEGIN SELECT RAISE(ABORT,'Reports are append-only'); END;
CREATE TRIGGER bank_evidence_no_update BEFORE UPDATE ON bank_test_runs WHEN OLD.evidence_json IS NOT NULL BEGIN SELECT RAISE(ABORT,'Final evidence is immutable'); END;
CREATE TRIGGER bank_evidence_no_delete BEFORE DELETE ON bank_test_runs WHEN OLD.evidence_json IS NOT NULL BEGIN SELECT RAISE(ABORT,'Final evidence is immutable'); END;
