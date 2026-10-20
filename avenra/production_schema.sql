CREATE TABLE runtime_schema(version INTEGER PRIMARY KEY);
INSERT INTO runtime_schema VALUES(1);
CREATE TABLE runtime_policies(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,version INTEGER NOT NULL,
 document_json TEXT NOT NULL,document_hash TEXT NOT NULL,signature TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(tenant_id,environment_id,version),UNIQUE(tenant_id,environment_id,request_key),FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE runtime_policy_bindings(
 tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,policy_id TEXT NOT NULL,updated_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,environment_id),FOREIGN KEY(policy_id) REFERENCES runtime_policies(id)
);
CREATE TABLE runtime_grants(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,principal TEXT NOT NULL,capabilities_json TEXT NOT NULL,environments_json TEXT NOT NULL,
 expires_at REAL NOT NULL,revoked_at TEXT,created_at TEXT NOT NULL,UNIQUE(tenant_id,principal)
);
CREATE TABLE runtime_sessions(
 token_hash TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,principal TEXT NOT NULL,approval_principal TEXT NOT NULL,capabilities_json TEXT NOT NULL,issuer_hash TEXT NOT NULL,
 authenticated_at REAL NOT NULL,expires_at REAL NOT NULL,revoked_at TEXT
);
CREATE TABLE runtime_buckets(
 tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,tokens REAL NOT NULL,updated_at REAL NOT NULL,
 PRIMARY KEY(tenant_id,environment_id)
);
CREATE TABLE runtime_requests(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,principal TEXT NOT NULL,capability TEXT NOT NULL,
 started_at REAL NOT NULL,expires_at REAL NOT NULL,completed_at REAL,status INTEGER,domain_status INTEGER,deadline_exceeded INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE runtime_workers(
 id TEXT PRIMARY KEY,role TEXT NOT NULL,source_fingerprint TEXT NOT NULL,last_seen REAL NOT NULL,started_at REAL NOT NULL
);
CREATE INDEX runtime_grant_active ON runtime_grants(tenant_id,principal) WHERE revoked_at IS NULL;
CREATE INDEX runtime_request_active ON runtime_requests(tenant_id,environment_id,expires_at) WHERE completed_at IS NULL;
CREATE INDEX runtime_request_metrics ON runtime_requests(tenant_id,environment_id,started_at);
