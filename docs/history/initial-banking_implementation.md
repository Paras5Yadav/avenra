# Avenra banking sandbox implementation plan

Prepared 17 October 2026. This is a proposed implementation against the current local code. It does not enable new banking services, VM execution, bank connectivity or live payments.

Avenra should become a stateful financial testing platform in which customers, merchants, banks and payment-service roles can be configured, faults can be introduced at named financial steps, and results can be checked against independently defined expectations. A realistic simulator uses synthetic money while executing real persistence, accounting, delivery and recovery code.

## Existing foundation and the specific gap

The current app persists payment intents and an outbox, posts synthetic INR through the accounting engine, introduces a lost-response fault, reconciles the original identity, and retains scenario evidence. Each environment has its own ledger. Six fixed scenario types and three fixed account roles are supported.

`avenra/app.py` currently combines fixture creation, payment orchestration and the scenario catalog. `process_payment()` performs one payer-to-payee posting. It does not model a payer PSP, payee PSP, remitter bank, beneficiary bank or independently observable financial legs. `avenra/schema.sql` has one aggregate payment state, not a bank-message/leg model. `avenra/store.py` supports the initial SQLite schema only; an explicit migration mechanism is required before changing persisted data.

Keep the current working profile as a regression baseline. Add a new versioned simulation profile alongside it. Existing environments and evidence must retain their original meaning.

## Three deployment profiles

| Profile | What runs | Source of financial truth | What is needed beyond local development |
| --- | --- | --- | --- |
| Local simulation | Virtual institutions, payment roles, configurable scenarios and synthetic accounts | The selected local financial backend | Implemented behaviour and published test coverage |
| Partner UAT | The approved bank or provider interface replaces a simulated role | Partner UAT for its external outcomes; local records retain requests and observations | Agreed role, current interface pack, test identities, certificates, connectivity and test cases |
| Live service | A separately governed payment application and approved connector | Applicable bank/network systems and explicitly defined local accounting | Applicable institutional arrangements, qualification and operating controls |

Use distinct deployment configuration, keys, databases and endpoint registries. A request parameter must not promote a simulation environment to live access. RBI regulatory-sandbox participation is a separate process from running this software.

## Domain and component boundaries

| Avenra capability | Responsibility | Integration decision |
| --- | --- | --- |
| Environment and institution registry | Banks, PSP roles, customers, merchants, accounts, VPAs, account restrictions, approved callback registrations and fixture versions | New owned domain modules |
| Payment protocol engine | Role-specific parsing, validation, routing, message correlation and lifecycle rules | New owned engine; each supported interface has its own versioned profile |
| Financial backend adapter | Posting, supported reservations, balances, account restrictions and effect lookup | Reuse the current accounting backend for the synthetic profile; qualify additional backends separately |
| Banking product adapter | Savings/deposits, loan products, schedules, accruals, repayments and product accounting | Integrate the selected bank-core service behind a bounded adapter |
| API contract adapter | Surrounding bank/provider mocks, schema checks and contract tests | Integrate the selected contract/mock service; keep financial behaviour in the domain engine |
| Monitoring adapter | Transaction event mapping, versioned rules, monitoring results and alerts | Integrate the selected monitoring service; record whether a rule observes or blocks |
| Financial data adapter | Consent lifecycle, account discovery/linking, permitted financial-data exchange and notifications | Implement a separate AA/FIP/FIU profile using the applicable published specifications |
| Run controller | Versioned scenario plans, persisted timers, fixtures, retries and cleanup | Extend the existing durable controller before adding another workflow product |
| Execution adapter | Uploaded integration clients and executable tests inside qualified VMs | Connect and qualify the included execution subsystem on Linux/KVM |
| Evidence evaluator | Expected-versus-observed outcomes, financial invariants, protocol observations and export | Extend the existing evidence pipeline using trusted financial observations |

Model these as separate responsibilities. They do not all need independent microservices in the first release. Trusted domain modules can initially share the application process, while engines and privileged execution workers remain behind adapters. Add a message broker when fan-out and measured operating needs justify it; a broker does not replace the outbox.

## First implementation: configurable virtual institutions

Replace the fixed fixture as the only provisioning option with a validated, versioned fixture document. Retain the current fixed fixture as version 1 for existing scenarios.

The document needs institution identifiers, supported payment roles, synthetic customers and merchants, account definitions, local VPA mappings, opening postings, restrictions, limits, deterministic seed and a business-time policy. Fixture input must not supply a mutable account balance column. Opening funds are authorized, idempotent journal postings through the financial backend.

For the first new fixture, create Avenra Test Bank A and Avenra Test Bank B, a payer account in A, a merchant account in B, two PSP roles and dedicated clearing/suspense accounts. Institution identity and account routing must be independent of display names.

Every domain object carries tenant and environment scope. A VPA resolves only within its environment and profile. Reusing a name in another environment does not reuse an account or financial state. Creating a fresh run makes a new fixture; resetting a test must not delete an earlier run's journal or evidence.

Extend the portal with virtual institutions, fixture publishing, customers/merchants and a fixture preview that shows opening postings before activation. Fixture activation needs the same durable identity and recovery behaviour as a payment.

## Payment engine: separate intent, legs and observations

Start with a clearly labelled Avenra synthetic JSON profile. It is useful for local developer testing without implying that its messages are an official UPI participant interface.

Add a profile registry containing role, interface/schema version, supported operations, message mappings, timeouts, limits, cryptographic requirements, applicability dates and approved test cases. Partner profiles require the actual authorized specifications. Do not infer the current private participant contract from a public product booklet or an older example.

An initial interbank simulation follows this sequence:

1. Accept a client request, persist its scoped identity, canonical financial fingerprint and pending work.
2. Resolve payer/payee institutions and VPAs under the pinned profile.
3. Apply simulated authorization and account/product restrictions. A synthetic authorization result must not collect a real customer's UPI PIN.
4. Create the payer-side financial effect using its own durable step identity.
5. Record and deliver the simulated network/beneficiary instruction.
6. Apply the beneficiary-side effect with a different durable step identity.
7. Reconcile observed results and produce the aggregate outcome under the profile's rules.
8. Deliver authenticated notifications and retain the complete evidence chain.

The aggregate payment has a business identity. Each debit, credit, permitted reversal, refund and message has a separate stable identity linked to that payment. Record financial leg state separately from whether a response was received. Illustrative internal states are pending, submitted, confirmed, rejected and indeterminate; actual wire values and transitions belong to the selected protocol profile.

One atomic payer-to-payee ledger transfer cannot reproduce a debit succeeding while a beneficiary credit remains unresolved. In the new synthetic model, use reviewed balanced clearing/suspense postings for intermediate states. Return money from suspense only under the applicable recovery rule and verified credit state. A late credit racing with reversal must be detected and resolved; a timeout alone must never authorize a refund.

Each ledger posting remains atomic inside the accounting backend. A database transaction in Avenra cannot atomically commit the separate accounting engine or a partner bank. Persist step identity and dispatch intent first, then reconcile the same external operation after an uncertain response. Never retry an uncertain effect under a new identity. Enforce available funds and reservations in the chosen authoritative backend, rather than a second mutable balance copy in the controller.

## New persisted records

Design schema migrations for the following records before adding endpoints:

- Institution, participant role, customer, merchant, account policy and environment-scoped VPA mapping.
- Fixture version, opening-funding operation and provisioning step.
- Protocol profile/version and published case catalog.
- Payment intent, financial leg, external operation identity, canonical fingerprint and backend reference.
- Message envelope, payload digest, sender/recipient, correlation ID and inbox deduplication record.
- Registered callback destination, signature profile, delivery attempt, next retry and dead-letter state.
- Durable timer/fault instruction and its admitted target step.
- Parent-linked refund/reversal, mandate and dispute records as their modules are added.
- Settlement cycle, expected participant positions, imported observations and reconciliation exception.
- Run configuration, independent assertions, component/rule/schema versions and evidence references.

Do not add all features to one payment-state string. A message delivery failure, a financial decline, an unresolved debit and a final credit are distinct facts.

Add tested forward migrations and a backup/restore procedure. Keep the current SQLite store for the local profile until a deliberate migration is ready. Before a shared multiworker deployment, move control-plane persistence to a qualified PostgreSQL schema and test unique constraints, leases, concurrency, recovery and the audit sequence. The existing accounting database and the app's control store have different responsibilities.

## Realistic faults and programmable scenarios

Extend transport faults to named, scoped connections. Add domain-level fault instructions for a rejected authorization, blocked account, invalid beneficiary, conflicting payload, delayed debit result, credit failure, lost credit acknowledgement, late success and out-of-order notifications. Faults must have deterministic targets and persistent application records.

Publish scenarios as validated JSON/YAML plans containing fixture version, profile version, allowed operations, fault placement, time policy and independently authored expectations. Initially permit only a bounded operation vocabulary. Do not execute arbitrary scenario Python or JavaScript in the controller process.

Add durable timers and a per-run business clock where the backend supports it. Preserve actual wall-clock observations. Accelerating one loan or mandate test must not change the shared host's system clock or another tenant's business date.

Let a developer register a local integration client and an approved callback destination. The portal should show each participant's timeline, sent/received messages, bank-leg outcome and authoritative posting references. Trace output assists diagnosis; trusted backend observations determine the verdict.

## Add the broader banking modules

| Module | Implementation requirement | Evidence needed |
| --- | --- | --- |
| Refunds and reversals | Linked operations with explicit authorization, duplicate protection, eligibility rules and concurrency-safe cumulative amount constraints | Retries cause no extra effect; late credit/reversal races are resolved; partial refunds cannot exceed the permitted original amount |
| Mandates | Versioned lifecycle, simulated approval, scheduling, amendments, revocation, expiry and execution limits for the selected profile | A revoked/expired mandate cannot execute; scheduling survives restart; duplicates cannot debit twice |
| Disputes | Case lifecycle, linked original transaction, evidence requests, deadlines and profile-specific adjustments | Dispute state does not silently rewrite the original financial transaction; each authorized adjustment has its own journal reference |
| Settlement and reconciliation | Synthetic cycles, participant obligations, report import/export, breaks and repair workflow | Customer credit, clearing position and settlement completion are independently observable; missing/duplicate items produce exceptions |
| Bank and NBFC products | Selected core's supported account/loan actions and product configuration, plus disbursement/repayment adapters | Product/account balances reconcile in that core; schedule/accrual tests use the supported business-date mechanism |
| Risk monitoring | Map Avenra events into the selected monitoring format and capture rule/configuration versions | Monitoring results are reproducible; blocking policy, timeouts and unavailable-engine behaviour are explicit |
| Financial data sharing | Separate AA/FIP/FIU roles, consent scope/lifecycle, discovery/linking, encrypted data exchange and notifications | No access after revocation/expiry or outside scope; history agrees with its selected source of financial truth |
| Surrounding provider APIs | Import pinned contracts/examples and register run-scoped mock endpoints | Contract tests pass while financial invariants are checked separately |

Not every module is a prerequisite for the first useful banking sandbox. Each becomes available only with published supported coverage. Transaction availability and limits are profile/version driven; older examples must not silently enable operations disallowed in the selected current profile.

Choose one authoritative owner per financial fact. A bank-core-managed loan or deposit stays authoritative in that core. The simulation ledger may own separate settlement/clearing accounts, but must not independently mutate a duplicate copy of the core's customer balance. Calls across two financial backends require explicit identity, uncertain-outcome handling and reconciliation.

## VM and external participant execution

Keep the current built-in trusted tests operational while the execution layer is qualified. VMs are needed when this product executes participant-supplied clients or scripts; they do not supply the payment domain model.

Use a compatible Linux/x86_64 KVM worker for the included runtime profile. Build and pin the required worker, guest, kernel and runtime artifacts. Verify create, boot, ready, command execution, network policy, termination, cleanup and recovery before connecting the production-facing execution adapter. Test suspend/restore separately if offered. Manifest resolution and helper tests are insufficient acceptance.

Expose only run-scoped simulated services to a guest. Never provide the controller's management credentials, bank signing keys, direct database credentials or a host container socket. Any future UAT access passes through a dedicated approved connector. Participant stdout or a file saying 'passed' cannot replace the independent evaluator.

Mobile onboarding, device binding and credential/SDK behaviour need a separate configured test application or partner test build and an applicable interface pack. The desktop payment API alone does not test these mobile properties.

## Concrete code work

These paths describe proposed code, not code already present:

| Current code | Proposed change |
| --- | --- |
| `avenra/app.py` fixed `FIXTURE` and `SCENARIOS` | Extract fixture provisioning and a versioned scenario/profile registry; preserve the existing profile |
| `avenra/app.py` `process_payment()` | Keep the current direct-transfer path for legacy runs; route the new profile through payment, participant and financial-leg modules |
| `avenra/schema.sql` and `avenra/store.py` | Introduce explicit migrations, new scoped records and durable message/timer handling |
| `avenra/gateways.py` | Separate financial, bank-core, contract, monitoring, financial-data, participant and execution adapters; advertise qualified capabilities |
| `avenra/scenarios.py` | Preserve the six regression cases and add a bounded scenario-plan executor plus independent financial invariants |
| `avenra/server.py` | Add scoped APIs for fixture publishing, institution/customer/merchant management, message/leg timelines and new operations |
| `avenra/static/` | Add configuration screens, participant timelines, supported-coverage views and reconciliation exceptions |
| `scripts/local.py` | Add explicit optional service profiles and dependency readiness; do not mark source-only engines as ready |
| `scripts/verify.py` and `tests/` | Add end-to-end cases for each delivered capability against actual selected local engines |

Proposed owned modules include `avenra/fixtures.py`, `avenra/institutions.py`, `avenra/profiles.py`, `avenra/payments/`, `avenra/participants/`, `avenra/scenario_plans.py` and `avenra/migrations/`. Preserve required upstream notices and adaptation records in `LICENSES/` when incorporating additional source.

## Delivery order and acceptance

| Increment | Deliverable | Completion evidence |
| --- | --- | --- |
| 1 | Configurable fixtures, two virtual banks, participant/VPA routing and API-created test customers/merchants | API and portal provisioning; isolated opening postings; duplicate-safe recovery; old six scenarios still pass |
| 2 | Multi-leg synthetic payment lifecycle and independent virtual bank responses | Successful interbank payment; decline; debit accepted/credit unresolved; lost credit acknowledgement; late response and restart recovery; correct balances and suspense |
| 3 | External test-client integration, authenticated callbacks, durable timers and declarative custom scenarios | A client outside the console completes a run; duplicate/out-of-order callbacks, invalid destinations and replay/conflict cases are exercised |
| 4 | Linked refunds/reversals, supported mandates, disputes and settlement/reconciliation packs | Published supported-case coverage; concurrent refund limits; mandate revocation; settlement breaks; recovery and evidence checks |
| 5 | Qualified API-contract, monitoring, banking-product and financial-data adapters | Each adapter has pinned configuration, mapping tests, end-to-end scenarios and a clear financial authority; cross-module loan/data/payment fixture agrees |
| 6 | Qualified VM execution and a shared-service operating profile | Real VM lifecycle/isolation tests plus scoped access, durable storage, backup/restore, tenant-boundary, capacity and outage tests |
| 7 | A named partner UAT role/profile | Authorized current contracts and test packs; approved test connectivity; partner outcomes and required acceptance evidence |

Production operations work runs alongside these increments. The local operator session is not an institutional identity system. A shared deployment needs external identity, role-based authorization, scoped service identities, managed secrets and signing, encrypted transport, controlled network access, observability, redaction, retained evidence, tested backups and recovery, and declared service targets. A production-quality synthetic sandbox can remain entirely synthetic; live payment processing is a separate deployment and qualification effort.

The first expanded release should claim only the supported synthetic interbank profile. A module is not complete because a source tree was copied, a container started, or a card appeared in the portal. Completion requires observable behaviour, meaningful failure/recovery tests and retained results.

## Primary interface and institutional references

- [NPCI UPI product booklet](https://www.npci.org.in/PDF/npci/upi/Product-Booklet.pdf): public ecosystem roles; not the current full participant interface contract.
- [NPCI UPI certification-zone user manual](https://www.npci.org.in/PDF/npci/certifications-zone/upi/Annexure-I_UserManual.pdf): a public example of substituting simulated counterparty roles. Its referenced technical version must not be treated as a current protocol baseline.
- [NPCI UPI FAQ](https://www.npci.org.in/what-we-do/upi/faqs): bank and TPAP onboarding routes; partner-specific current requirements must be obtained for the intended role.
- [NPCI UPI circular register](https://www.npci.org.in/circulars/upi): changes to consider when qualifying a current profile.
- [ReBIT Account Aggregator API specifications](https://api.rebit.org.in/): use the selected current API and financial-data catalog, including applicable interoperability changes; pin exact artifacts per profile.
- [Sahamati participation and UAT information](https://sahamati.org.in/faq/): role-specific implementation and access to shared UAT services.
- [RBI Regulatory Sandbox overview](https://fintech.rbi.org.in/FT_RegSandbox.aspx): controlled live testing under RBI's separate program.
