ALTER TABLE sessions ADD COLUMN tenant_id TEXT REFERENCES tenants(id);
ALTER TABLE sessions ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '["read","write","admin"]';
CREATE TABLE identity_logins(state_hash TEXT PRIMARY KEY,verifier TEXT NOT NULL,nonce TEXT NOT NULL,expires_at REAL NOT NULL);
