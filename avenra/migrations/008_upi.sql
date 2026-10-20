ALTER TABLE bank_operations ADD COLUMN executor TEXT NOT NULL DEFAULT 'banking' CHECK(executor IN ('banking','upi'));
CREATE TABLE upi_participants(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,actor_key TEXT NOT NULL,
 bank_id TEXT,role TEXT NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('active','paused')),
 key_epoch INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,actor_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id),
 FOREIGN KEY(bank_id,tenant_id,environment_id) REFERENCES bank_institutions(id,tenant_id,environment_id)
);
CREATE TABLE upi_keys(
 id TEXT PRIMARY KEY,participant_id TEXT NOT NULL REFERENCES upi_participants(id),epoch INTEGER NOT NULL,
 public_key TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,UNIQUE(participant_id,epoch)
);
CREATE TABLE upi_devices(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,customer_id TEXT NOT NULL,
 device_reference TEXT NOT NULL,challenge_hash TEXT NOT NULL,expires_at REAL NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','active','revoked')),request_key TEXT NOT NULL,request_hash TEXT NOT NULL,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 UNIQUE(tenant_id,environment_id,customer_id,device_reference),
 FOREIGN KEY(customer_id,tenant_id,environment_id) REFERENCES bank_customers(id,tenant_id,environment_id)
);
CREATE TABLE upi_links(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,device_id TEXT NOT NULL,
 account_id TEXT NOT NULL,psp_id TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(device_id,account_id,psp_id),
 FOREIGN KEY(device_id,tenant_id,environment_id) REFERENCES upi_devices(id,tenant_id,environment_id),
 FOREIGN KEY(account_id,tenant_id,environment_id) REFERENCES bank_accounts(account_id,tenant_id,environment_id),
 FOREIGN KEY(psp_id,tenant_id,environment_id) REFERENCES upi_participants(id,tenant_id,environment_id)
);
CREATE TABLE upi_payments(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,
 profile_hash TEXT NOT NULL,mode TEXT NOT NULL CHECK(mode IN ('pay','collect','qr','refund')),
 parent_id TEXT,payer_id TEXT NOT NULL,payee_id TEXT NOT NULL,amount_paise INTEGER NOT NULL CHECK(amount_paise>0),
 topology_json TEXT NOT NULL,device_id TEXT,authorization_json TEXT,expires_at REAL NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('awaiting_authorization','queued','routing','debit_pending','credit_pending','unknown','succeeded','declined','reversed','expired','cancelled')),
 operation_id TEXT UNIQUE,financial_fault TEXT NOT NULL,delivery_fault_json TEXT NOT NULL,reason TEXT,
 lease_token TEXT,lease_until REAL,evidence_json TEXT,source_fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id),
 FOREIGN KEY(parent_id,tenant_id,environment_id) REFERENCES upi_payments(id,tenant_id,environment_id),
 FOREIGN KEY(payer_id,tenant_id,environment_id) REFERENCES bank_accounts(account_id,tenant_id,environment_id),
 FOREIGN KEY(payee_id,tenant_id,environment_id) REFERENCES bank_accounts(account_id,tenant_id,environment_id),
 FOREIGN KEY(device_id,tenant_id,environment_id) REFERENCES upi_devices(id,tenant_id,environment_id),
 FOREIGN KEY(operation_id,tenant_id,environment_id) REFERENCES bank_operations(id,tenant_id,environment_id)
);
CREATE TABLE upi_envelopes(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,payment_id TEXT NOT NULL,
 step INTEGER NOT NULL,generation INTEGER NOT NULL,sender TEXT NOT NULL,recipient TEXT NOT NULL,kind TEXT NOT NULL,
 document_json TEXT NOT NULL,document_hash TEXT NOT NULL,signature TEXT NOT NULL,key_id TEXT NOT NULL,expires_at REAL NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(payment_id,step,generation),UNIQUE(id,tenant_id,environment_id),
 FOREIGN KEY(payment_id,tenant_id,environment_id) REFERENCES upi_payments(id,tenant_id,environment_id),
 FOREIGN KEY(sender,tenant_id,environment_id) REFERENCES upi_participants(id,tenant_id,environment_id),
 FOREIGN KEY(recipient,tenant_id,environment_id) REFERENCES upi_participants(id,tenant_id,environment_id),
 FOREIGN KEY(key_id) REFERENCES upi_keys(id)
);
CREATE TABLE upi_deliveries(
 message_id TEXT PRIMARY KEY REFERENCES upi_envelopes(id),state TEXT NOT NULL CHECK(state IN ('ready','delivered','expired','rejected')),
 available_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,fault_applied INTEGER NOT NULL DEFAULT 0,error TEXT,updated_at TEXT NOT NULL
);
CREATE TABLE upi_inbox(
 message_id TEXT PRIMARY KEY REFERENCES upi_envelopes(id),document_hash TEXT NOT NULL,receipt_json TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE upi_actor_views(
 payment_id TEXT NOT NULL REFERENCES upi_payments(id),participant_id TEXT NOT NULL REFERENCES upi_participants(id),
 state TEXT NOT NULL,last_message_id TEXT NOT NULL REFERENCES upi_envelopes(id),updated_at TEXT NOT NULL,
 PRIMARY KEY(payment_id,participant_id)
);
CREATE TABLE upi_events(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,payment_id TEXT NOT NULL,
 message_id TEXT,event TEXT NOT NULL,details_json TEXT NOT NULL,virtual_time REAL NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(payment_id,tenant_id,environment_id) REFERENCES upi_payments(id,tenant_id,environment_id)
);
CREATE INDEX upi_payment_scope ON upi_payments(tenant_id,environment_id,created_at);
CREATE INDEX upi_envelope_payment ON upi_envelopes(payment_id,step,generation);
CREATE INDEX upi_delivery_due ON upi_deliveries(state,available_at);
CREATE TRIGGER upi_envelopes_no_update BEFORE UPDATE ON upi_envelopes BEGIN SELECT RAISE(ABORT,'Participant envelopes are immutable'); END;
CREATE TRIGGER upi_envelopes_no_delete BEFORE DELETE ON upi_envelopes BEGIN SELECT RAISE(ABORT,'Participant envelopes are retained'); END;
CREATE TRIGGER upi_inbox_no_update BEFORE UPDATE ON upi_inbox BEGIN SELECT RAISE(ABORT,'Participant receipts are immutable'); END;
CREATE TRIGGER upi_inbox_no_delete BEFORE DELETE ON upi_inbox BEGIN SELECT RAISE(ABORT,'Participant receipts are retained'); END;
CREATE TRIGGER upi_events_no_update BEFORE UPDATE ON upi_events BEGIN SELECT RAISE(ABORT,'Participant events are immutable'); END;
CREATE TRIGGER upi_events_no_delete BEFORE DELETE ON upi_events BEGIN SELECT RAISE(ABORT,'Participant events are retained'); END;
CREATE TRIGGER upi_payments_no_update BEFORE UPDATE ON upi_payments WHEN OLD.evidence_json IS NOT NULL BEGIN SELECT RAISE(ABORT,'Completed evidence is immutable'); END;
CREATE TRIGGER upi_payments_no_delete BEFORE DELETE ON upi_payments WHEN OLD.evidence_json IS NOT NULL BEGIN SELECT RAISE(ABORT,'Completed evidence is retained'); END;
