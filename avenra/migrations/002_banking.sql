CREATE TABLE bank_institutions(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,name TEXT NOT NULL,code TEXT NOT NULL,
 roles_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended')),
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(environment_id,code),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE bank_customers(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,name TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('customer','merchant')),request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE bank_accounts(
 account_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,bank_id TEXT NOT NULL,customer_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('provisioning','active','blocked','closed')),opening_paise INTEGER NOT NULL CHECK(opening_paise>=0),
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(account_id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(account_id,tenant_id,environment_id) REFERENCES accounts(id,tenant_id,environment_id),
 FOREIGN KEY(bank_id,tenant_id,environment_id) REFERENCES bank_institutions(id,tenant_id,environment_id),
 FOREIGN KEY(customer_id,tenant_id,environment_id) REFERENCES bank_customers(id,tenant_id,environment_id)
);
CREATE TABLE bank_operations(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('transfer','refund','reversal')),
 parent_id TEXT,payer_id TEXT NOT NULL,payee_id TEXT NOT NULL,amount_paise INTEGER NOT NULL CHECK(amount_paise>0),
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,profile TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','processing','unknown','succeeded','declined','reversed')),
 fault TEXT NOT NULL CHECK(fault IN ('none','lost_debit_response','lost_credit_response','reject_credit','reject_authorization')),
 reason TEXT,lease_token TEXT,lease_until REAL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id),
 FOREIGN KEY(parent_id,tenant_id,environment_id) REFERENCES bank_operations(id,tenant_id,environment_id),
 FOREIGN KEY(payer_id,tenant_id,environment_id) REFERENCES bank_accounts(account_id,tenant_id,environment_id),
 FOREIGN KEY(payee_id,tenant_id,environment_id) REFERENCES bank_accounts(account_id,tenant_id,environment_id)
);
CREATE TABLE bank_legs(
 id TEXT PRIMARY KEY,operation_id TEXT NOT NULL REFERENCES bank_operations(id),ordinal INTEGER NOT NULL,kind TEXT NOT NULL,
 source TEXT NOT NULL,destination TEXT NOT NULL,amount_paise INTEGER NOT NULL CHECK(amount_paise>0),
 request_hash TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('pending','confirmed','declined','unknown')),
 fault_applied INTEGER NOT NULL DEFAULT 0,ledger_id TEXT,reason TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(operation_id,kind),UNIQUE(operation_id,ordinal)
);
CREATE TABLE bank_messages(
 id TEXT PRIMARY KEY,operation_id TEXT NOT NULL REFERENCES bank_operations(id),kind TEXT NOT NULL,
 sender TEXT NOT NULL,recipient TEXT NOT NULL,payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(operation_id,kind)
);
CREATE TABLE bank_rules(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,name TEXT NOT NULL,version INTEGER NOT NULL,
 threshold_paise INTEGER NOT NULL CHECK(threshold_paise>0),action TEXT NOT NULL CHECK(action IN ('alert','block')),
 enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,name,version),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE bank_risk_results(
 id TEXT PRIMARY KEY,operation_id TEXT NOT NULL REFERENCES bank_operations(id),rule_id TEXT NOT NULL REFERENCES bank_rules(id),
 matched INTEGER NOT NULL,action TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(operation_id,rule_id)
);
CREATE TABLE bank_mandates(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,payer_id TEXT NOT NULL,payee_id TEXT NOT NULL,
 maximum_paise INTEGER NOT NULL CHECK(maximum_paise>0),starts_at REAL NOT NULL,expires_at REAL NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','active','revoked')),request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(payer_id,tenant_id,environment_id) REFERENCES bank_accounts(account_id,tenant_id,environment_id),
 FOREIGN KEY(payee_id,tenant_id,environment_id) REFERENCES bank_accounts(account_id,tenant_id,environment_id)
);
CREATE TABLE bank_mandate_executions(
 id TEXT PRIMARY KEY,mandate_id TEXT NOT NULL REFERENCES bank_mandates(id),occurrence TEXT NOT NULL,
 amount_paise INTEGER NOT NULL CHECK(amount_paise>0),due_at REAL NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('scheduled','dispatched','cancelled')),operation_id TEXT REFERENCES bank_operations(id),created_at TEXT NOT NULL,
 UNIQUE(mandate_id,occurrence)
);
CREATE TABLE bank_disputes(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,operation_id TEXT NOT NULL,
 reason TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('open','reviewing','resolved','rejected')),resolution TEXT,
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(operation_id,tenant_id,environment_id) REFERENCES bank_operations(id,tenant_id,environment_id)
);
CREATE TABLE bank_cycles(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,name TEXT NOT NULL,request_key TEXT NOT NULL,
 request_hash TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('prepared','reconciled','exceptions')),positions_json TEXT NOT NULL,
 exceptions_json TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE bank_cycle_items(operation_id TEXT PRIMARY KEY REFERENCES bank_operations(id),cycle_id TEXT NOT NULL REFERENCES bank_cycles(id),amount_paise INTEGER NOT NULL);
CREATE TABLE bank_cycle_reports(id TEXT PRIMARY KEY,cycle_id TEXT NOT NULL REFERENCES bank_cycles(id),observed_json TEXT NOT NULL,exceptions_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE bank_consents(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,customer_id TEXT NOT NULL,
 account_ids_json TEXT NOT NULL,purpose TEXT NOT NULL,expires_at REAL NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','active','revoked')),request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(customer_id,tenant_id,environment_id) REFERENCES bank_customers(id,tenant_id,environment_id)
);
CREATE TABLE bank_case_definitions(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,name TEXT NOT NULL,version INTEGER NOT NULL,
 plan_json TEXT NOT NULL,plan_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,name,version),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE bank_test_runs(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,case_id TEXT NOT NULL,fixture_environment_id TEXT NOT NULL,
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('queued','running','passed','failed')),
 evidence_json TEXT,error TEXT,created_at TEXT NOT NULL,completed_at TEXT,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(case_id,tenant_id,environment_id) REFERENCES bank_case_definitions(id,tenant_id,environment_id),
 FOREIGN KEY(fixture_environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE INDEX bank_operations_scope ON bank_operations(tenant_id,environment_id,created_at);
CREATE INDEX bank_legs_operation ON bank_legs(operation_id,ordinal);
CREATE INDEX bank_mandates_due ON bank_mandate_executions(state,due_at);
CREATE TRIGGER bank_messages_no_update BEFORE UPDATE ON bank_messages BEGIN SELECT RAISE(ABORT,'Messages are append-only'); END;
CREATE TRIGGER bank_messages_no_delete BEFORE DELETE ON bank_messages BEGIN SELECT RAISE(ABORT,'Messages are append-only'); END;
CREATE TRIGGER bank_rules_no_update BEFORE UPDATE ON bank_rules BEGIN SELECT RAISE(ABORT,'Publish a new rule version'); END;
CREATE TRIGGER bank_rules_no_delete BEFORE DELETE ON bank_rules BEGIN SELECT RAISE(ABORT,'Rules are versioned'); END;
CREATE TRIGGER bank_cases_no_update BEFORE UPDATE ON bank_case_definitions BEGIN SELECT RAISE(ABORT,'Publish a new case version'); END;
CREATE TRIGGER bank_cases_no_delete BEFORE DELETE ON bank_case_definitions BEGIN SELECT RAISE(ABORT,'Cases are versioned'); END;
