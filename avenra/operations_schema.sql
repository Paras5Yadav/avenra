CREATE TABLE ops_schema(version INTEGER PRIMARY KEY,source_sha256 TEXT NOT NULL,installed_at TEXT NOT NULL,installation_json TEXT NOT NULL);
CREATE TABLE ops_policies(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,version INTEGER NOT NULL,
 document_json TEXT NOT NULL,document_hash TEXT NOT NULL,signature TEXT NOT NULL,request_key TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(tenant_id,environment_id,version),UNIQUE(tenant_id,environment_id,request_key),UNIQUE(id,tenant_id,environment_id),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE ops_bindings(
 tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,policy_id TEXT NOT NULL,
 PRIMARY KEY(tenant_id,environment_id),FOREIGN KEY(policy_id,tenant_id,environment_id) REFERENCES ops_policies(id,tenant_id,environment_id)
);
CREATE TABLE ops_observations(
 tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,policy_id TEXT NOT NULL,observed_at REAL NOT NULL,
 source_fingerprint TEXT NOT NULL,metrics_json TEXT NOT NULL,
 PRIMARY KEY(tenant_id,environment_id),FOREIGN KEY(policy_id,tenant_id,environment_id) REFERENCES ops_policies(id,tenant_id,environment_id)
);
CREATE TABLE ops_rejections(
 tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,slot INTEGER NOT NULL CHECK(slot>=0 AND slot<4096),
 code TEXT NOT NULL CHECK(code IN ('TENANT_CAPACITY','REQUEST_RATE','QUEUE_CAPACITY')),epoch_second INTEGER NOT NULL,count INTEGER NOT NULL CHECK(count>0),
 PRIMARY KEY(tenant_id,environment_id,slot,code),FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE ops_monitor(
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),owner TEXT NOT NULL,token TEXT NOT NULL,lease_until REAL NOT NULL,
 source_fingerprint TEXT NOT NULL,completed_at REAL,cursor_environment TEXT NOT NULL
);
CREATE TABLE ops_alerts(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL UNIQUE,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,policy_id TEXT NOT NULL,signal TEXT NOT NULL,episode INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','firing','recovering','resolved')),first_seen REAL NOT NULL,last_seen REAL NOT NULL,
 condition_since REAL NOT NULL,recovery_since REAL,changed_at REAL NOT NULL,acknowledged_by TEXT,acknowledged_at REAL,
 latest_metrics_json TEXT NOT NULL,UNIQUE(tenant_id,environment_id,policy_id,signal,episode),UNIQUE(id,tenant_id,environment_id),
 FOREIGN KEY(policy_id,tenant_id,environment_id) REFERENCES ops_policies(id,tenant_id,environment_id)
);
CREATE TABLE ops_alert_events(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL UNIQUE,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,alert_id TEXT,kind TEXT NOT NULL,
 actor TEXT,request_key TEXT,request_hash TEXT,recorded_at REAL NOT NULL,payload_json TEXT NOT NULL,
 UNIQUE(tenant_id,environment_id,request_key),FOREIGN KEY(alert_id,tenant_id,environment_id) REFERENCES ops_alerts(id,tenant_id,environment_id),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE UNIQUE INDEX ops_alert_single_open ON ops_alerts(tenant_id,environment_id,signal) WHERE state!='resolved';
CREATE INDEX ops_alert_history ON ops_alerts(tenant_id,environment_id,sequence);
CREATE INDEX ops_event_history ON ops_alert_events(tenant_id,environment_id,alert_id,sequence);
CREATE INDEX ops_rejection_window ON ops_rejections(tenant_id,environment_id,epoch_second);
CREATE INDEX ops_queue_scope ON outbox(tenant_id,environment_id,state,available_at,lease_until);
CREATE INDEX ops_legacy_unknown ON payments(tenant_id,environment_id,updated_at) WHERE state='unknown';
CREATE INDEX ops_bank_unknown ON bank_operations(tenant_id,environment_id,updated_at) WHERE state='unknown';
CREATE INDEX ops_upi_unknown ON upi_payments(tenant_id,environment_id,updated_at) WHERE state='unknown';
