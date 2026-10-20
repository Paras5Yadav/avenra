CREATE TABLE IF NOT EXISTS fidelity_schema(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO fidelity_schema VALUES(2);
CREATE TABLE IF NOT EXISTS fidelity_profiles(
 environment_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,profile_hash TEXT NOT NULL,document_json TEXT NOT NULL,enabled_at REAL NOT NULL,activated_at TEXT NOT NULL,activation_sequence INTEGER NOT NULL,
 FOREIGN KEY(environment_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS fidelity_challenges(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,device_id TEXT NOT NULL,account_id TEXT NOT NULL,purpose TEXT NOT NULL,
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,expires_at REAL NOT NULL,issued_epoch INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'pending',completion_hash TEXT,created_at TEXT NOT NULL,
 UNIQUE(tenant_id,environment_id,request_key)
);
CREATE TABLE IF NOT EXISTS fidelity_credentials(
 account_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,device_id TEXT NOT NULL,
 salt TEXT NOT NULL,pin_hash TEXT NOT NULL,epoch INTEGER NOT NULL,failed_attempts INTEGER NOT NULL DEFAULT 0,
 locked_until REAL NOT NULL DEFAULT 0,cooling_until REAL NOT NULL DEFAULT 0,updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fidelity_guards(
 payment_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,device_id TEXT NOT NULL,source TEXT NOT NULL,
 source_id TEXT NOT NULL,credential_epoch INTEGER NOT NULL,intent_hash TEXT NOT NULL,day_index INTEGER NOT NULL,
 amount_paise INTEGER NOT NULL,decision_hash TEXT NOT NULL,authorized_at REAL NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fidelity_mandates(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,payer_id TEXT NOT NULL,payee_id TEXT NOT NULL,device_id TEXT NOT NULL,
 maximum_paise INTEGER NOT NULL,interval_seconds INTEGER NOT NULL,starts_at REAL NOT NULL,ends_at REAL NOT NULL,occurrences INTEGER NOT NULL,
 state TEXT NOT NULL,authorization_json TEXT,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,action_version INTEGER NOT NULL DEFAULT 0,
 UNIQUE(tenant_id,environment_id,request_key)
);
CREATE TABLE IF NOT EXISTS fidelity_occurrences(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,mandate_id TEXT NOT NULL,occurrence INTEGER NOT NULL,
 amount_paise INTEGER NOT NULL,due_at REAL NOT NULL,payment_id TEXT,state TEXT NOT NULL,error TEXT,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,retry_after REAL NOT NULL DEFAULT 0,
 UNIQUE(mandate_id,occurrence),UNIQUE(tenant_id,environment_id,request_key),FOREIGN KEY(mandate_id) REFERENCES fidelity_mandates(id)
);
CREATE TABLE IF NOT EXISTS fidelity_notifications(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,resource_id TEXT NOT NULL,kind TEXT NOT NULL,document_json TEXT NOT NULL,
 document_hash TEXT NOT NULL,signature TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(resource_id,kind)
);
CREATE TABLE IF NOT EXISTS fidelity_complaints(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,payment_id TEXT NOT NULL,reason TEXT NOT NULL,state TEXT NOT NULL,
 deadline REAL NOT NULL,escalated INTEGER NOT NULL DEFAULT 0,version INTEGER NOT NULL DEFAULT 1,resolution_json TEXT,
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(tenant_id,environment_id,request_key)
);
CREATE TABLE IF NOT EXISTS fidelity_events(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,resource_id TEXT NOT NULL,kind TEXT NOT NULL,actor TEXT NOT NULL,
 document_json TEXT NOT NULL,business_time REAL NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fidelity_proposals(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,resource_id TEXT NOT NULL,kind TEXT NOT NULL,maker TEXT NOT NULL,
 document_json TEXT NOT NULL,document_hash TEXT NOT NULL,state TEXT NOT NULL,checker TEXT,decision_json TEXT,
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(tenant_id,environment_id,request_key)
);
CREATE TABLE IF NOT EXISTS fidelity_cycles(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,cutoff REAL NOT NULL,state TEXT NOT NULL,
 manifest_json TEXT NOT NULL,manifest_hash TEXT NOT NULL,signature TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(tenant_id,environment_id,request_key),UNIQUE(tenant_id,environment_id,cutoff)
);
CREATE TABLE IF NOT EXISTS fidelity_cycle_items(
 payment_id TEXT PRIMARY KEY,cycle_id TEXT NOT NULL,FOREIGN KEY(cycle_id) REFERENCES fidelity_cycles(id)
);
CREATE TABLE IF NOT EXISTS fidelity_reconciliations(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,cycle_id TEXT NOT NULL,observed_json TEXT NOT NULL,
 exceptions_json TEXT NOT NULL,manifest_hash TEXT NOT NULL,signature TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(tenant_id,environment_id,request_key),FOREIGN KEY(cycle_id) REFERENCES fidelity_cycles(id)
);
CREATE TABLE IF NOT EXISTS fidelity_acceptance(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,manifest_json TEXT NOT NULL,manifest_hash TEXT NOT NULL,signature TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fidelity_occurrence_due ON fidelity_occurrences(state,due_at);
CREATE INDEX IF NOT EXISTS fidelity_guard_day ON fidelity_guards(tenant_id,environment_id,day_index);
CREATE INDEX IF NOT EXISTS fidelity_event_resource ON fidelity_events(resource_id,created_at,id);
CREATE UNIQUE INDEX IF NOT EXISTS fidelity_release_once ON fidelity_proposals(resource_id) WHERE kind='cycle_release' AND state='approved';
