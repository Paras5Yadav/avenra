ALTER TABLE external_commands ADD COLUMN binding_hash TEXT;
ALTER TABLE external_commands ADD COLUMN lease_token TEXT;
ALTER TABLE external_commands ADD COLUMN lease_until REAL;
