CREATE TABLE simulation_profiles(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,profile_key TEXT NOT NULL,
 document_json TEXT NOT NULL,document_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,profile_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE simulation_plans(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,profile_id TEXT NOT NULL,
 name TEXT NOT NULL,version INTEGER NOT NULL,plan_json TEXT NOT NULL,plan_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,name,version),
 FOREIGN KEY(profile_id,tenant_id,environment_id) REFERENCES simulation_profiles(id,tenant_id,environment_id)
);
CREATE TABLE simulation_runs(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,plan_id TEXT NOT NULL,
 fixture_environment_id TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('queued','running','passed','failed')),cursor INTEGER NOT NULL DEFAULT 0,
 context_json TEXT NOT NULL DEFAULT '{}',error TEXT,evidence_json TEXT,lease_token TEXT,lease_until REAL,
 created_at TEXT NOT NULL,completed_at TEXT,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(plan_id,tenant_id,environment_id) REFERENCES simulation_plans(id,tenant_id,environment_id),
 FOREIGN KEY(fixture_environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE simulation_steps(
 run_id TEXT NOT NULL REFERENCES simulation_runs(id),ordinal INTEGER NOT NULL,step_id TEXT NOT NULL,
 input_hash TEXT NOT NULL,output_json TEXT NOT NULL,virtual_time REAL NOT NULL,completed_at TEXT NOT NULL,
 PRIMARY KEY(run_id,ordinal),UNIQUE(run_id,step_id)
);
CREATE TABLE simulation_clocks(
 environment_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,epoch REAL NOT NULL,revision INTEGER NOT NULL DEFAULT 0,
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE simulation_clock_events(
 environment_id TEXT NOT NULL REFERENCES simulation_clocks(environment_id),request_key TEXT NOT NULL,
 request_hash TEXT NOT NULL,before_epoch REAL NOT NULL,after_epoch REAL NOT NULL,created_at TEXT NOT NULL,
 PRIMARY KEY(environment_id,request_key)
);
CREATE TABLE simulation_messages(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,profile_id TEXT NOT NULL,
 sender TEXT NOT NULL,recipient TEXT NOT NULL,message_key TEXT NOT NULL,envelope_json TEXT NOT NULL,
 envelope_hash TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('accepted','rejected')),reason TEXT,
 virtual_time REAL NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(tenant_id,environment_id,sender,message_key,envelope_hash,virtual_time),
 FOREIGN KEY(profile_id,tenant_id,environment_id) REFERENCES simulation_profiles(id,tenant_id,environment_id)
);
CREATE INDEX simulation_runs_scope ON simulation_runs(tenant_id,environment_id,created_at);
CREATE INDEX simulation_messages_scope ON simulation_messages(tenant_id,environment_id,created_at);
CREATE TRIGGER simulation_profiles_no_update BEFORE UPDATE ON simulation_profiles BEGIN SELECT RAISE(ABORT,'Publish a new profile key'); END;
CREATE TRIGGER simulation_profiles_no_delete BEFORE DELETE ON simulation_profiles BEGIN SELECT RAISE(ABORT,'Profiles are retained'); END;
CREATE TRIGGER simulation_plans_no_update BEFORE UPDATE ON simulation_plans BEGIN SELECT RAISE(ABORT,'Publish a new plan version'); END;
CREATE TRIGGER simulation_plans_no_delete BEFORE DELETE ON simulation_plans BEGIN SELECT RAISE(ABORT,'Plans are retained'); END;
CREATE TRIGGER simulation_steps_no_update BEFORE UPDATE ON simulation_steps BEGIN SELECT RAISE(ABORT,'Step receipts are immutable'); END;
CREATE TRIGGER simulation_steps_no_delete BEFORE DELETE ON simulation_steps BEGIN SELECT RAISE(ABORT,'Step receipts are retained'); END;
CREATE TRIGGER simulation_messages_no_update BEFORE UPDATE ON simulation_messages BEGIN SELECT RAISE(ABORT,'Messages are immutable'); END;
CREATE TRIGGER simulation_messages_no_delete BEFORE DELETE ON simulation_messages BEGIN SELECT RAISE(ABORT,'Messages are retained'); END;
CREATE TRIGGER simulation_clock_events_no_update BEFORE UPDATE ON simulation_clock_events BEGIN SELECT RAISE(ABORT,'Clock events are immutable'); END;
CREATE TRIGGER simulation_clock_events_no_delete BEFORE DELETE ON simulation_clock_events BEGIN SELECT RAISE(ABORT,'Clock events are retained'); END;
CREATE TRIGGER simulation_runs_no_update BEFORE UPDATE ON simulation_runs WHEN OLD.evidence_json IS NOT NULL BEGIN SELECT RAISE(ABORT,'Completed evidence is immutable'); END;
CREATE TRIGGER simulation_runs_no_delete BEFORE DELETE ON simulation_runs WHEN OLD.evidence_json IS NOT NULL BEGIN SELECT RAISE(ABORT,'Completed evidence is retained'); END;
