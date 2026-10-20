import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .util import canonical, digest, now, uid


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "avenra.db"
        key_path = self.directory / "evidence.key"
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(secrets.token_bytes(32))
        self.signing_key = key_path.read_bytes()
        if len(self.signing_key) != 32:
            raise RuntimeError("Avenra evidence key is invalid; preserve the original key to verify retained reports.")
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                schema = Path(__file__).with_name("schema.sql").read_text()
                db.executescript("BEGIN IMMEDIATE;\n" + schema + "\nPRAGMA user_version=1; COMMIT;")
                version = 1
            if version == 1:
                migration = Path(__file__).with_name('migrations').joinpath('002_banking.sql').read_text()
                db.executescript('BEGIN IMMEDIATE;\n' + migration + '\nPRAGMA user_version=2; COMMIT;')
                version = 2
            if version == 2:
                migration = Path(__file__).with_name('migrations').joinpath('003_services.sql').read_text()
                db.executescript('BEGIN IMMEDIATE;\n' + migration + '\nPRAGMA user_version=3; COMMIT;')
                version = 3
            if version == 3:
                migration = Path(__file__).with_name('migrations').joinpath('004_fixture_profiles.sql').read_text()
                db.executescript('BEGIN IMMEDIATE;\n' + migration + '\nPRAGMA user_version=4; COMMIT;')
                version = 4
            if version == 4:
                migration = Path(__file__).with_name('migrations').joinpath('005_identity.sql').read_text()
                db.executescript('BEGIN IMMEDIATE;\n' + migration + '\nPRAGMA user_version=5; COMMIT;')
                version = 5
            if version == 5:
                migration = Path(__file__).with_name('migrations').joinpath('006_transport.sql').read_text()
                db.executescript('BEGIN IMMEDIATE;\n' + migration + '\nPRAGMA user_version=6; COMMIT;')
                version = 6
            if version == 6:
                migration = Path(__file__).with_name('migrations').joinpath('007_simulation.sql').read_text()
                db.executescript('BEGIN IMMEDIATE;\n' + migration + '\nPRAGMA user_version=7; COMMIT;')
                version = 7
            if version == 7:
                migration = Path(__file__).with_name('migrations').joinpath('008_upi.sql').read_text()
                db.executescript('BEGIN IMMEDIATE;\n' + migration + '\nPRAGMA user_version=8; COMMIT;')
                version = 8
            if version == 8:
                migration = Path(__file__).with_name('migrations').joinpath('009_connections.sql').read_text()
                db.executescript('BEGIN IMMEDIATE;\n' + migration + '\nPRAGMA user_version=9; COMMIT;')
                version = 9
            if version != 9:
                raise RuntimeError("Avenra database requires a compatible application version.")
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=20000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @contextmanager
    def reader(self):
        with self.connect() as db:
            db.execute("BEGIN")
            try:
                yield db
            finally:
                db.execute("ROLLBACK")

    def audit(self, db, tenant_id, environment_id, kind, entity_id, payload):
        previous = db.execute("SELECT event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1").fetchone()
        previous_hash = previous[0] if previous else "0" * 64
        timestamp = now()
        body = {"tenant_id": tenant_id, "environment_id": environment_id, "kind": kind,
                "entity_id": entity_id, "payload": payload, "created_at": timestamp, "previous_hash": previous_hash}
        db.execute("INSERT INTO audit_events(tenant_id,environment_id,kind,entity_id,payload_json,created_at,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?,?)",
                   (tenant_id, environment_id, kind, entity_id, canonical(payload), timestamp, previous_hash, digest(body)))

    def enqueue(self, db, tenant_id, environment_id, kind, aggregate_id, payload, key, delay=0):
        job_id = uid("job")
        db.execute("INSERT OR IGNORE INTO outbox(id,tenant_id,environment_id,kind,aggregate_id,payload_json,dedupe_key,available_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (job_id, tenant_id, environment_id, kind, aggregate_id, canonical(payload), key, time.time() + delay, now()))

    def claim(self, owner, lease_seconds=30):
        timestamp = time.time()
        with self.transaction() as db:
            job = db.execute("SELECT * FROM outbox WHERE (state='ready' AND available_at<=?) OR (state='leased' AND lease_until<?) ORDER BY available_at,created_at LIMIT 1",
                             (timestamp, timestamp)).fetchone()
            if job is None:
                return None
            token = secrets.token_hex(16)
            db.execute("UPDATE outbox SET state='leased',attempts=attempts+1,lease_owner=?,lease_token=?,lease_until=? WHERE id=?",
                       (owner, token, timestamp + lease_seconds, job["id"]))
            return dict(db.execute("SELECT * FROM outbox WHERE id=?", (job["id"],)).fetchone())

    def finish(self, job, error=None):
        with self.transaction() as db:
            if error is None:
                result = db.execute("UPDATE outbox SET state='done',completed_at=?,lease_until=NULL WHERE id=? AND state='leased' AND lease_token=?",
                                    (now(), job["id"], job["lease_token"]))
            else:
                state = "failed" if job["attempts"] >= 6 else "ready"
                result = db.execute("UPDATE outbox SET state=?,available_at=?,last_error=?,lease_until=NULL WHERE id=? AND state='leased' AND lease_token=?",
                                    (state, time.time() + min(30, .25 * 2 ** job["attempts"]), str(error)[:600], job["id"], job["lease_token"]))
            return result.rowcount == 1

    def defer(self, job, delay=1):
        with self.transaction() as db:
            return db.execute("UPDATE outbox SET state='ready',available_at=?,lease_until=NULL WHERE id=? AND state='leased' AND lease_token=?",
                              (time.time() + delay, job['id'], job['lease_token'])).rowcount == 1

    def verify_audit(self):
        previous_hash = "0" * 64
        count = 0
        with self.reader() as db:
            for event in db.execute("SELECT * FROM audit_events ORDER BY sequence"):
                body = {"tenant_id": event["tenant_id"], "environment_id": event["environment_id"], "kind": event["kind"],
                        "entity_id": event["entity_id"], "payload": json.loads(event["payload_json"]),
                        "created_at": event["created_at"], "previous_hash": event["previous_hash"]}
                if event["previous_hash"] != previous_hash or digest(body) != event["event_hash"]:
                    return {"valid": False, "checked": count, "broken_sequence": event["sequence"]}
                previous_hash = event["event_hash"]
                count += 1
        return {"valid": True, "checked": count, "head_hash": previous_hash}
