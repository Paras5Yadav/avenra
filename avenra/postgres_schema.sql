-- Avenra control schema, version 9. Generated from the owned SQL migrations.

CREATE FUNCTION avenra_immutable_record() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Published records are immutable'; END; $$;

-- schema.sql
CREATE TABLE tenants(id TEXT PRIMARY KEY,name TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE environments(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL REFERENCES tenants(id),name TEXT NOT NULL,
 kind TEXT NOT NULL DEFAULT 'simulation' CHECK(kind='simulation'),
 purpose TEXT NOT NULL CHECK(purpose IN ('interactive','run')),
 ledger_name TEXT NOT NULL UNIQUE,status TEXT NOT NULL CHECK(status IN ('provisioning','active','failed')),
 error TEXT,created_at TEXT NOT NULL,UNIQUE(id,tenant_id)
);
CREATE TABLE accounts(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,name TEXT NOT NULL,
 vpa TEXT NOT NULL,address TEXT NOT NULL,role TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(environment_id,vpa),UNIQUE(environment_id,address),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE payments(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,
 payer_id TEXT NOT NULL,payee_id TEXT NOT NULL,amount_paise BIGINT NOT NULL CHECK(amount_paise>0),
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','succeeded','declined','unknown')),
 fault TEXT NOT NULL CHECK(fault IN ('none','lost_response')),fault_applied BIGINT NOT NULL DEFAULT 0,
 ledger_transaction_id TEXT,ledger_result_json TEXT,reason TEXT,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(tenant_id,environment_id,request_key),UNIQUE(id,tenant_id,environment_id),
 FOREIGN KEY(payer_id,tenant_id,environment_id) REFERENCES accounts(id,tenant_id,environment_id),
 FOREIGN KEY(payee_id,tenant_id,environment_id) REFERENCES accounts(id,tenant_id,environment_id)
);
CREATE TABLE callback_events(
 id TEXT PRIMARY KEY,payment_id TEXT NOT NULL REFERENCES payments(id),payload_json TEXT NOT NULL,
 payload_hash TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(payment_id)
);
CREATE TABLE callback_inbox(event_id TEXT PRIMARY KEY REFERENCES callback_events(id),payload_hash TEXT NOT NULL,received_at TEXT NOT NULL);
CREATE TABLE callback_attempts(id BIGSERIAL PRIMARY KEY,event_id TEXT NOT NULL REFERENCES callback_events(id),
 result TEXT NOT NULL CHECK(result IN ('accepted','duplicate')),created_at TEXT NOT NULL);
CREATE TABLE outbox(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL REFERENCES tenants(id),environment_id TEXT NOT NULL REFERENCES environments(id),
 kind TEXT NOT NULL,aggregate_id TEXT NOT NULL,payload_json TEXT NOT NULL,dedupe_key TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL DEFAULT 'ready' CHECK(state IN ('ready','leased','done','failed')),attempts BIGINT NOT NULL DEFAULT 0,
 available_at DOUBLE PRECISION NOT NULL,lease_owner TEXT,lease_token TEXT,lease_until DOUBLE PRECISION,last_error TEXT,created_at TEXT NOT NULL,completed_at TEXT
);
CREATE INDEX outbox_ready ON outbox(state,available_at,lease_until);
CREATE TABLE runs(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,source_environment_id TEXT NOT NULL,
 name TEXT NOT NULL,scenario_key TEXT NOT NULL,profile_version TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,
 seed TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('queued','running','passed','failed')),
 error TEXT,created_at TEXT NOT NULL,started_at TEXT,completed_at TEXT,
 UNIQUE(tenant_id,request_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id),
 FOREIGN KEY(source_environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE run_steps(
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),step_key TEXT NOT NULL,name TEXT NOT NULL,
 expected_json TEXT NOT NULL,observed_json TEXT NOT NULL,passed BIGINT NOT NULL CHECK(passed IN (0,1)),created_at TEXT NOT NULL,
 UNIQUE(run_id,step_key)
);
CREATE TABLE evidence(
 run_id TEXT PRIMARY KEY REFERENCES runs(id),manifest_json TEXT NOT NULL,manifest_hash TEXT NOT NULL,signature TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE audit_events(
 sequence BIGSERIAL PRIMARY KEY,tenant_id TEXT NOT NULL REFERENCES tenants(id),environment_id TEXT,kind TEXT NOT NULL,entity_id TEXT NOT NULL,
 payload_json TEXT NOT NULL,created_at TEXT NOT NULL,previous_hash TEXT NOT NULL,event_hash TEXT NOT NULL
);
CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,csrf TEXT NOT NULL,expires_at DOUBLE PRECISION NOT NULL);
CREATE TABLE api_keys(token_hash TEXT PRIMARY KEY,tenant_id TEXT NOT NULL REFERENCES tenants(id),name TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TRIGGER immutable_audit_update BEFORE UPDATE ON audit_events FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER immutable_audit_delete BEFORE DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER immutable_evidence_update BEFORE UPDATE ON evidence FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER immutable_evidence_delete BEFORE DELETE ON evidence FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER immutable_steps_update BEFORE UPDATE ON run_steps FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER immutable_steps_delete BEFORE DELETE ON run_steps FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();


-- 002_banking.sql
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
 status TEXT NOT NULL CHECK(status IN ('provisioning','active','blocked','closed')),opening_paise BIGINT NOT NULL CHECK(opening_paise>=0),
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(account_id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(account_id,tenant_id,environment_id) REFERENCES accounts(id,tenant_id,environment_id),
 FOREIGN KEY(bank_id,tenant_id,environment_id) REFERENCES bank_institutions(id,tenant_id,environment_id),
 FOREIGN KEY(customer_id,tenant_id,environment_id) REFERENCES bank_customers(id,tenant_id,environment_id)
);
CREATE TABLE bank_operations(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('transfer','refund','reversal')),
 parent_id TEXT,payer_id TEXT NOT NULL,payee_id TEXT NOT NULL,amount_paise BIGINT NOT NULL CHECK(amount_paise>0),
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,profile TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','processing','unknown','succeeded','declined','reversed')),
 fault TEXT NOT NULL CHECK(fault IN ('none','lost_debit_response','lost_credit_response','reject_credit','reject_authorization')),
 reason TEXT,lease_token TEXT,lease_until DOUBLE PRECISION,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id),
 FOREIGN KEY(parent_id,tenant_id,environment_id) REFERENCES bank_operations(id,tenant_id,environment_id),
 FOREIGN KEY(payer_id,tenant_id,environment_id) REFERENCES bank_accounts(account_id,tenant_id,environment_id),
 FOREIGN KEY(payee_id,tenant_id,environment_id) REFERENCES bank_accounts(account_id,tenant_id,environment_id)
);
CREATE TABLE bank_legs(
 id TEXT PRIMARY KEY,operation_id TEXT NOT NULL REFERENCES bank_operations(id),ordinal BIGINT NOT NULL,kind TEXT NOT NULL,
 source TEXT NOT NULL,destination TEXT NOT NULL,amount_paise BIGINT NOT NULL CHECK(amount_paise>0),
 request_hash TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('pending','confirmed','declined','unknown')),
 fault_applied BIGINT NOT NULL DEFAULT 0,ledger_id TEXT,reason TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(operation_id,kind),UNIQUE(operation_id,ordinal)
);
CREATE TABLE bank_messages(
 id TEXT PRIMARY KEY,operation_id TEXT NOT NULL REFERENCES bank_operations(id),kind TEXT NOT NULL,
 sender TEXT NOT NULL,recipient TEXT NOT NULL,payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(operation_id,kind)
);
CREATE TABLE bank_rules(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,name TEXT NOT NULL,version BIGINT NOT NULL,
 threshold_paise BIGINT NOT NULL CHECK(threshold_paise>0),action TEXT NOT NULL CHECK(action IN ('alert','block')),
 enabled BIGINT NOT NULL DEFAULT 1,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,name,version),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE bank_risk_results(
 id TEXT PRIMARY KEY,operation_id TEXT NOT NULL REFERENCES bank_operations(id),rule_id TEXT NOT NULL REFERENCES bank_rules(id),
 matched BIGINT NOT NULL,action TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(operation_id,rule_id)
);
CREATE TABLE bank_mandates(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,payer_id TEXT NOT NULL,payee_id TEXT NOT NULL,
 maximum_paise BIGINT NOT NULL CHECK(maximum_paise>0),starts_at DOUBLE PRECISION NOT NULL,expires_at DOUBLE PRECISION NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','active','revoked')),request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(payer_id,tenant_id,environment_id) REFERENCES bank_accounts(account_id,tenant_id,environment_id),
 FOREIGN KEY(payee_id,tenant_id,environment_id) REFERENCES bank_accounts(account_id,tenant_id,environment_id)
);
CREATE TABLE bank_mandate_executions(
 id TEXT PRIMARY KEY,mandate_id TEXT NOT NULL REFERENCES bank_mandates(id),occurrence TEXT NOT NULL,
 amount_paise BIGINT NOT NULL CHECK(amount_paise>0),due_at DOUBLE PRECISION NOT NULL,
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
CREATE TABLE bank_cycle_items(operation_id TEXT PRIMARY KEY REFERENCES bank_operations(id),cycle_id TEXT NOT NULL REFERENCES bank_cycles(id),amount_paise BIGINT NOT NULL);
CREATE TABLE bank_cycle_reports(id TEXT PRIMARY KEY,cycle_id TEXT NOT NULL REFERENCES bank_cycles(id),observed_json TEXT NOT NULL,exceptions_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE bank_consents(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,customer_id TEXT NOT NULL,
 account_ids_json TEXT NOT NULL,purpose TEXT NOT NULL,expires_at DOUBLE PRECISION NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','active','revoked')),request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(customer_id,tenant_id,environment_id) REFERENCES bank_customers(id,tenant_id,environment_id)
);
CREATE TABLE bank_case_definitions(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,name TEXT NOT NULL,version BIGINT NOT NULL,
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
CREATE TRIGGER bank_messages_no_update BEFORE UPDATE ON bank_messages FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER bank_messages_no_delete BEFORE DELETE ON bank_messages FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER bank_rules_no_update BEFORE UPDATE ON bank_rules FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER bank_rules_no_delete BEFORE DELETE ON bank_rules FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER bank_cases_no_update BEFORE UPDATE ON bank_case_definitions FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER bank_cases_no_delete BEFORE DELETE ON bank_case_definitions FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();


-- 003_services.sql
ALTER TABLE api_keys ADD COLUMN revoked_at TEXT;
ALTER TABLE api_keys ADD COLUMN expires_at DOUBLE PRECISION;
ALTER TABLE api_keys ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '["read","write"]';
CREATE TABLE bank_callback_destinations(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,name TEXT NOT NULL,url TEXT NOT NULL,
 key_version BIGINT NOT NULL DEFAULT 1,status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
 request_key TEXT NOT NULL,request_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
ALTER TABLE bank_operations ADD COLUMN callback_id TEXT REFERENCES bank_callback_destinations(id);
CREATE TABLE bank_notifications(
 id TEXT PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE REFERENCES bank_operations(id),destination_id TEXT NOT NULL REFERENCES bank_callback_destinations(id),
 payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,key_version BIGINT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','delivered','dead_letter')),created_at TEXT NOT NULL,delivered_at TEXT
);
CREATE TABLE bank_notification_attempts(id TEXT PRIMARY KEY,event_id TEXT NOT NULL REFERENCES bank_notifications(id),http_status BIGINT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE external_commands(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,service TEXT NOT NULL,action TEXT NOT NULL,
 path TEXT NOT NULL,body_json TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','unknown','succeeded','rejected')),result_json TEXT,error TEXT,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,service,request_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TRIGGER bank_cycle_reports_no_update BEFORE UPDATE ON bank_cycle_reports FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER bank_cycle_reports_no_delete BEFORE DELETE ON bank_cycle_reports FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER bank_evidence_no_update BEFORE UPDATE ON bank_test_runs FOR EACH ROW WHEN (OLD.evidence_json IS NOT NULL) EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER bank_evidence_no_delete BEFORE DELETE ON bank_test_runs FOR EACH ROW WHEN (OLD.evidence_json IS NOT NULL) EXECUTE FUNCTION avenra_immutable_record();


-- 004_fixture_profiles.sql
ALTER TABLE environments ADD COLUMN fixture_profile TEXT NOT NULL DEFAULT 'legacy' CHECK(fixture_profile IN ('legacy','banking'));


-- 005_identity.sql
ALTER TABLE sessions ADD COLUMN tenant_id TEXT REFERENCES tenants(id);
ALTER TABLE sessions ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '["read","write","admin"]';
CREATE TABLE identity_logins(state_hash TEXT PRIMARY KEY,verifier TEXT NOT NULL,nonce TEXT NOT NULL,expires_at DOUBLE PRECISION NOT NULL);


-- 006_transport.sql
ALTER TABLE external_commands ADD COLUMN binding_hash TEXT;
ALTER TABLE external_commands ADD COLUMN lease_token TEXT;
ALTER TABLE external_commands ADD COLUMN lease_until DOUBLE PRECISION;


-- 007_simulation.sql
CREATE TABLE simulation_profiles(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,profile_key TEXT NOT NULL,
 document_json TEXT NOT NULL,document_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,profile_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE simulation_plans(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,profile_id TEXT NOT NULL,
 name TEXT NOT NULL,version BIGINT NOT NULL,plan_json TEXT NOT NULL,plan_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,name,version),
 FOREIGN KEY(profile_id,tenant_id,environment_id) REFERENCES simulation_profiles(id,tenant_id,environment_id)
);
CREATE TABLE simulation_runs(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,plan_id TEXT NOT NULL,
 fixture_environment_id TEXT NOT NULL,request_key TEXT NOT NULL,request_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('queued','running','passed','failed')),cursor BIGINT NOT NULL DEFAULT 0,
 context_json TEXT NOT NULL DEFAULT '{}',error TEXT,evidence_json TEXT,lease_token TEXT,lease_until DOUBLE PRECISION,
 created_at TEXT NOT NULL,completed_at TEXT,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,request_key),
 FOREIGN KEY(plan_id,tenant_id,environment_id) REFERENCES simulation_plans(id,tenant_id,environment_id),
 FOREIGN KEY(fixture_environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE simulation_steps(
 run_id TEXT NOT NULL REFERENCES simulation_runs(id),ordinal BIGINT NOT NULL,step_id TEXT NOT NULL,
 input_hash TEXT NOT NULL,output_json TEXT NOT NULL,virtual_time DOUBLE PRECISION NOT NULL,completed_at TEXT NOT NULL,
 PRIMARY KEY(run_id,ordinal),UNIQUE(run_id,step_id)
);
CREATE TABLE simulation_clocks(
 environment_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,epoch DOUBLE PRECISION NOT NULL,revision BIGINT NOT NULL DEFAULT 0,
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id)
);
CREATE TABLE simulation_clock_events(
 environment_id TEXT NOT NULL REFERENCES simulation_clocks(environment_id),request_key TEXT NOT NULL,
 request_hash TEXT NOT NULL,before_epoch DOUBLE PRECISION NOT NULL,after_epoch DOUBLE PRECISION NOT NULL,created_at TEXT NOT NULL,
 PRIMARY KEY(environment_id,request_key)
);
CREATE TABLE simulation_messages(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,profile_id TEXT NOT NULL,
 sender TEXT NOT NULL,recipient TEXT NOT NULL,message_key TEXT NOT NULL,envelope_json TEXT NOT NULL,
 envelope_hash TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('accepted','rejected')),reason TEXT,
 virtual_time DOUBLE PRECISION NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(tenant_id,environment_id,sender,message_key,envelope_hash,virtual_time),
 FOREIGN KEY(profile_id,tenant_id,environment_id) REFERENCES simulation_profiles(id,tenant_id,environment_id)
);
CREATE INDEX simulation_runs_scope ON simulation_runs(tenant_id,environment_id,created_at);
CREATE INDEX simulation_messages_scope ON simulation_messages(tenant_id,environment_id,created_at);
CREATE TRIGGER simulation_profiles_no_update BEFORE UPDATE ON simulation_profiles FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER simulation_profiles_no_delete BEFORE DELETE ON simulation_profiles FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER simulation_plans_no_update BEFORE UPDATE ON simulation_plans FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER simulation_plans_no_delete BEFORE DELETE ON simulation_plans FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER simulation_steps_no_update BEFORE UPDATE ON simulation_steps FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER simulation_steps_no_delete BEFORE DELETE ON simulation_steps FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER simulation_messages_no_update BEFORE UPDATE ON simulation_messages FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER simulation_messages_no_delete BEFORE DELETE ON simulation_messages FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER simulation_clock_events_no_update BEFORE UPDATE ON simulation_clock_events FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER simulation_clock_events_no_delete BEFORE DELETE ON simulation_clock_events FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER simulation_runs_no_update BEFORE UPDATE ON simulation_runs FOR EACH ROW WHEN (OLD.evidence_json IS NOT NULL) EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER simulation_runs_no_delete BEFORE DELETE ON simulation_runs FOR EACH ROW WHEN (OLD.evidence_json IS NOT NULL) EXECUTE FUNCTION avenra_immutable_record();


-- 008_upi.sql
ALTER TABLE bank_operations ADD COLUMN executor TEXT NOT NULL DEFAULT 'banking' CHECK(executor IN ('banking','upi'));
CREATE TABLE upi_participants(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,actor_key TEXT NOT NULL,
 bank_id TEXT,role TEXT NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('active','paused')),
 key_epoch BIGINT NOT NULL DEFAULT 1,created_at TEXT NOT NULL,
 UNIQUE(id,tenant_id,environment_id),UNIQUE(tenant_id,environment_id,actor_key),
 FOREIGN KEY(environment_id,tenant_id) REFERENCES environments(id,tenant_id),
 FOREIGN KEY(bank_id,tenant_id,environment_id) REFERENCES bank_institutions(id,tenant_id,environment_id)
);
CREATE TABLE upi_keys(
 id TEXT PRIMARY KEY,participant_id TEXT NOT NULL REFERENCES upi_participants(id),epoch BIGINT NOT NULL,
 public_key TEXT NOT NULL,revoked BIGINT NOT NULL DEFAULT 0,created_at TEXT NOT NULL,UNIQUE(participant_id,epoch)
);
CREATE TABLE upi_devices(
 id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,environment_id TEXT NOT NULL,customer_id TEXT NOT NULL,
 device_reference TEXT NOT NULL,challenge_hash TEXT NOT NULL,expires_at DOUBLE PRECISION NOT NULL,
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
 parent_id TEXT,payer_id TEXT NOT NULL,payee_id TEXT NOT NULL,amount_paise BIGINT NOT NULL CHECK(amount_paise>0),
 topology_json TEXT NOT NULL,device_id TEXT,authorization_json TEXT,expires_at DOUBLE PRECISION NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('awaiting_authorization','queued','routing','debit_pending','credit_pending','unknown','succeeded','declined','reversed','expired','cancelled')),
 operation_id TEXT UNIQUE,financial_fault TEXT NOT NULL,delivery_fault_json TEXT NOT NULL,reason TEXT,
 lease_token TEXT,lease_until DOUBLE PRECISION,evidence_json TEXT,source_fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
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
 step BIGINT NOT NULL,generation BIGINT NOT NULL,sender TEXT NOT NULL,recipient TEXT NOT NULL,kind TEXT NOT NULL,
 document_json TEXT NOT NULL,document_hash TEXT NOT NULL,signature TEXT NOT NULL,key_id TEXT NOT NULL,expires_at DOUBLE PRECISION NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(payment_id,step,generation),UNIQUE(id,tenant_id,environment_id),
 FOREIGN KEY(payment_id,tenant_id,environment_id) REFERENCES upi_payments(id,tenant_id,environment_id),
 FOREIGN KEY(sender,tenant_id,environment_id) REFERENCES upi_participants(id,tenant_id,environment_id),
 FOREIGN KEY(recipient,tenant_id,environment_id) REFERENCES upi_participants(id,tenant_id,environment_id),
 FOREIGN KEY(key_id) REFERENCES upi_keys(id)
);
CREATE TABLE upi_deliveries(
 message_id TEXT PRIMARY KEY REFERENCES upi_envelopes(id),state TEXT NOT NULL CHECK(state IN ('ready','delivered','expired','rejected')),
 available_at DOUBLE PRECISION NOT NULL,attempts BIGINT NOT NULL DEFAULT 0,fault_applied BIGINT NOT NULL DEFAULT 0,error TEXT,updated_at TEXT NOT NULL
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
 message_id TEXT,event TEXT NOT NULL,details_json TEXT NOT NULL,virtual_time DOUBLE PRECISION NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(payment_id,tenant_id,environment_id) REFERENCES upi_payments(id,tenant_id,environment_id)
);
CREATE INDEX upi_payment_scope ON upi_payments(tenant_id,environment_id,created_at);
CREATE INDEX upi_envelope_payment ON upi_envelopes(payment_id,step,generation);
CREATE INDEX upi_delivery_due ON upi_deliveries(state,available_at);
CREATE TRIGGER upi_envelopes_no_update BEFORE UPDATE ON upi_envelopes FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER upi_envelopes_no_delete BEFORE DELETE ON upi_envelopes FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER upi_inbox_no_update BEFORE UPDATE ON upi_inbox FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER upi_inbox_no_delete BEFORE DELETE ON upi_inbox FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER upi_events_no_update BEFORE UPDATE ON upi_events FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER upi_events_no_delete BEFORE DELETE ON upi_events FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER upi_payments_no_update BEFORE UPDATE ON upi_payments FOR EACH ROW WHEN (OLD.evidence_json IS NOT NULL) EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER upi_payments_no_delete BEFORE DELETE ON upi_payments FOR EACH ROW WHEN (OLD.evidence_json IS NOT NULL) EXECUTE FUNCTION avenra_immutable_record();


-- 009_connections.sql
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
CREATE TRIGGER upi_adapter_bindings_no_update BEFORE UPDATE OF adapter_bindings_json ON upi_payments FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE INDEX participant_connection_scope ON participant_connections(tenant_id,environment_id,created_at);
CREATE INDEX participant_connection_check_scope ON participant_connection_checks(connection_id,created_at);
CREATE TRIGGER participant_connections_no_update BEFORE UPDATE ON participant_connections FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER participant_connections_no_delete BEFORE DELETE ON participant_connections FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER participant_connection_checks_no_update BEFORE UPDATE ON participant_connection_checks FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();
CREATE TRIGGER participant_connection_checks_no_delete BEFORE DELETE ON participant_connection_checks FOR EACH ROW EXECUTE FUNCTION avenra_immutable_record();


CREATE TABLE avenra_schema_version(version BIGINT PRIMARY KEY);
INSERT INTO avenra_schema_version VALUES(9);
