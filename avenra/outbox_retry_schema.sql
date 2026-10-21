ALTER TABLE outbox ADD COLUMN error_attempts INTEGER DEFAULT 0 CHECK(error_attempts BETWEEN 0 AND 6);
UPDATE outbox SET error_attempts=NULL WHERE last_error IS NOT NULL;
CREATE TABLE outbox_retry_schema(
 version INTEGER PRIMARY KEY CHECK(version=1),source_sha256 TEXT NOT NULL,
 installed_at TEXT NOT NULL,installation_json TEXT NOT NULL
);
