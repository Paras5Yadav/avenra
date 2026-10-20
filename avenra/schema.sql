CREATE TABLE tenants(id TEXT PRIMARY KEY,name TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE environments(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL REFERENCES tenants(id),name TEXT NOT NULL,
 kind TEXT NOT NULL DEFAULT 'simulation' CHECK(kind='simulation'),
 purpose TEXT NOT NULL CHECK(purpose IN ('interactive','run')),
 ledger_name TEXT NOT NULL UNIQUE,status TEXT NOT NULL CHECK(status IN ('provisioning','active','failed')),
 error TEXT,created_at TEXT NOT NULL,UNIQUE(id,tenant_id)
);
CREATE TABLE accounts(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,name TEXT NOT NULL,
 vpa TEXT NOT NULL,address TEXT NOT NULL,role TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(environment_id,vpa),UNIQUE(environment_id,address),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE payments(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,
 payer_id TEXT NOT NULL,payee_id TEXT NOT NULL,amount_paise INTEGER NOT NULL CHECK(typeof(amount_paise)='integer' AND amount_paise>0),
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','succeeded','declined','unknown')),
 fault TEXT NOT NULL CHECK(fault IN ('none','lost_response')),fault_applied INTEGER NOT NULL DEFAULT 0,
 ledger_transaction_id TEXT,ledger_result_json TEXT,reason TEXT,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(tenant_id,environment_id,request_key),UNIQUE(id,tenant_id,environment_id),
 FOREIGN KEY(payer_id,tenant_id,environment_id) REFERENCES accounts(id,tenant_id,environment_id),
 FOREIGN KEY(payee_id,tenant_id,environment_id) REFERENCES accounts(id,tenant_id,environment_id)
);
CREATE TABLE callback_events(
 id TEXT PRIMARY KEY,payment_id TEXT NOT NULL REFERENCES payments(id),payload_json TEXT NOT NULL,
 payload_hash TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(payment_id)
);
CREATE TABLE callback_inbox(event_id TEXT PRIMARY KEY REFERENCES callback_events(id),payload_hash TEXT NOT NULL,received_at TEXT NOT NULL);
CREATE TABLE callback_attempts(id INTEGER PRIMARY KEY,event_id TEXT NOT NULL REFERENCES callback_events(id),
 result TEXT NOT NULL CHECK(result IN ('accepted','duplicate')),created_at TEXT NOT NULL);
CREATE TABLE outbox(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL REFERENCES tenants(id),environment_id TEXT NOT NULL REFERENCES environments(id),
 kind TEXT NOT NULL,aggregate_id TEXT NOT NULL,payload_json TEXT NOT NULL,dedupe_key TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL DEFAULT 'ready' CHECK(state IN ('ready','leased','done','failed')),attempts INTEGER NOT NULL DEFAULT 0,
 available_at REAL NOT NULL,lease_owner TEXT,lease_token TEXT,lease_until REAL,last_error TEXT,created_at TEXT NOT NULL,completed_at TEXT
);
CREATE INDEX outbox_ready ON outbox(state,available_at,lease_until);
CREATE TABLE runs(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,source_environment_id TEXT NOT NULL,
 name TEXT NOT NULL,scenario_key TEXT NOT NULL,profile_version TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,
 seed TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('queued','running','passed','failed')),
 error TEXT,created_at TEXT NOT NULL,started_at TEXT,completed_at TEXT,
 UNIQUE(tenant_id,request_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id),
 FOREIGN KEY(source_environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE run_steps(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),step_key TEXT NOT NULL,name TEXT NOT NULL,
 expected_json TEXT NOT NULL,observed_json TEXT NOT NULL,passed INTEGER NOT NULL CHECK(passed IN (0,1)),created_at TEXT NOT NULL,
 UNIQUE(run_id,step_key)
);
CREATE TABLE evidence(
 run_id TEXT PRIMARY KEY REFERENCES runs(id),manifest_json TEXT NOT NULL,manifest_hash TEXT NOT NULL,signature TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE audit_events(
 sequence INTEGER PRIMARY KEY,tenant_id TEXT NOT NULL REFERENCES tenants(id),environment_id TEXT,kind TEXT NOT NULL,entity_id TEXT NOT NULL,
 payload_json TEXT NOT NULL,created_at TEXT NOT NULL,previous_hash TEXT NOT NULL,event_hash TEXT NOT NULL
);
CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,csrf TEXT NOT NULL,expires_at REAL NOT NULL);
CREATE TABLE api_keys(token_hash TEXT PRIMARY KEY,tenant_id TEXT NOT NULL REFERENCES tenants(id),name TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TRIGGER immutable_audit_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT,'Audit events are append-only'); END;
CREATE TRIGGER immutable_audit_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT,'Audit events are append-only'); END;
CREATE TRIGGER immutable_evidence_update BEFORE UPDATE ON evidence BEGIN SELECT RAISE(ABORT,'Evidence is append-only'); END;
CREATE TRIGGER immutable_evidence_delete BEFORE DELETE ON evidence BEGIN SELECT RAISE(ABORT,'Evidence is append-only'); END;
CREATE TRIGGER immutable_steps_update BEFORE UPDATE ON run_steps BEGIN SELECT RAISE(ABORT,'Run steps are append-only'); END;
CREATE TRIGGER immutable_steps_delete BEFORE DELETE ON run_steps BEGIN SELECT RAISE(ABORT,'Run steps are append-only'); END;
