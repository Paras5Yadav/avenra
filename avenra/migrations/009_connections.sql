CREATE TABLE participant_connections(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,participant_id TEXT NOT NULL,
 name TEXT NOT NULL,adapter_id TEXT NOT NULL,profile_hash TEXT NOT NULL,config_json TEXT NOT NULL,config_hash TEXT NOT NULL,
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(participant_id,tenant_id,environment_id) REFERENCES upi_participants(id,tenant_id,environment_id)
);
CREATE TABLE participant_connection_checks(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,connection_id TEXT NOT NULL,
 manifest_json TEXT NOT NULL,manifest_hash TEXT NOT NULL,signature TEXT NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(connection_id,tenant_id,environment_id) REFERENCES participant_connections(id,tenant_id,environment_id)
);
CREATE TABLE participant_connection_bindings(
 participant_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,connection_id TEXT NOT NULL,updated_at TEXT NOT NULL,
 FOREIGN KEY(participant_id,tenant_id,environment_id) REFERENCES upi_participants(id,tenant_id,environment_id),
 FOREIGN KEY(connection_id,tenant_id,environment_id) REFERENCES participant_connections(id,tenant_id,environment_id)
);
ALTER TABLE upi_payments ADD COLUMN adapter_bindings_json TEXT NOT NULL DEFAULT '{}';
CREATE TRIGGER upi_adapter_bindings_no_update BEFORE UPDATE OF adapter_bindings_json ON upi_payments BEGIN SELECT RAISE(ABORT,'Payment adapter bindings are immutable'); END;
CREATE INDEX participant_connection_scope ON participant_connections(tenant_id,environment_id,created_at);
CREATE INDEX participant_connection_check_scope ON participant_connection_checks(connection_id,created_at);
CREATE TRIGGER participant_connections_no_update BEFORE UPDATE ON participant_connections BEGIN SELECT RAISE(ABORT,'Connection revisions are immutable'); END;
CREATE TRIGGER participant_connections_no_delete BEFORE DELETE ON participant_connections BEGIN SELECT RAISE(ABORT,'Connection revisions are retained'); END;
CREATE TRIGGER participant_connection_checks_no_update BEFORE UPDATE ON participant_connection_checks BEGIN SELECT RAISE(ABORT,'Connection checks are immutable'); END;
CREATE TRIGGER participant_connection_checks_no_delete BEFORE DELETE ON participant_connection_checks BEGIN SELECT RAISE(ABORT,'Connection checks are retained'); END;
