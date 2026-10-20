# Avenra implementation status

Updated 18 October 2026. The original proposal is retained in `docs/history/initial-banking_implementation.md`. This document describes the running implementation and its remaining engineering work.

## Implemented and exercised

| Domain | Main implementation | Evidence |
|---|---|---|
| Configurable financial fixtures | `avenra/banking.py`, fixture profile migration | Institution/account creation, empty environments and actual opening postings |
| Multi-leg financial operations | `avenra/banking.py` | Separate debit/credit identities, concurrent replay, real debit/credit response loss, linked refunds and reversals |
| Mandates, disputes, cycles, consent | `avenra/banking.py` | Scheduling/revocation, case transitions, reconciliation discrepancies and scoped ledger data |
| Banking plans and evidence | `avenra/bank_cases.py` | Fresh run fixtures, independent expected balances, bounded plans and signed immutable final run evidence |
| Participant payment engine | `avenra/upi.py`, `avenra/upi_api.py`, `avenra/static/upi.js` | Device proof/account linking, merchant collect and QR subset, ordered signed actor delivery, separate financial phases, refunds, expiry, actual response loss and killed-worker recovery |
| ASGI serving | `avenra/asgi.py`, `avenra/worker.py` | Real ingress/framing/auth checks; independent API and competing worker processes, graceful shutdown and API restart |
| Multistep institution lab | `avenra/simulation.py`, `avenra/static/simulation.js` | Versioned profiles/plans, synthetic authenticated role messages, virtual business time, financial and consent/mandate/dispute/settlement steps, durable receipts and interruption/concurrency checks |
| Shared control persistence | `avenra/postgres_store.py`, migrations through 008 | Existing-state migration, PostgreSQL banking tests, crash/restart and restore checks |
| Product service | `avenra/services.py` | Native loan/savings lifecycle, stable command keys, service-binding guard and lost-response recovery |
| Monitoring | `avenra/monitoring.py` | Native ingestion, rule evaluation, alert reports, signed scoped evidence and recovery from dropped response bytes |
| Provider mocks | `avenra/services.py` | Environment namespace, real import/invocation, denied external references and executable extensions |
| Callbacks | `avenra/callbacks.py` | HMAC verification by an HTTP receiver, stable event identity and observed acknowledgement status |
| Institutional identity code | `avenra/identity.py` | RSA/JWKS validation, code/PKCE/nonce flow, scoped sessions and rejection cases |
| Console | `avenra/static/banking.js` | Configurable banking forms, confirmed payment legs, monitoring reports, scenario evidence and service status |

Trusted domain modules share the Avenra application process. Financial and analysis engines run behind adapters. Durable outbox records connect acceptance to execution. This installation does not establish a horizontally scaled production deployment.

## Remaining engineering and qualification

| Area | Work still required |
|---|---|
| Complete UPI participant simulation | Implement a selected current wire/security profile; official role-specific state machines, complete device/credential and product flows, network response-code/timing mapping and authorized participant acceptance packs. Selected synthetic device/collect/QR and asynchronous actor flows now execute |
| Bank-connected UAT | Implement/qualify the selected bank adapter; obtain its issued test identity, certificates, endpoints and approved connectivity; execute bank acceptance cases |
| Optional product/payment bridge | Broader banking extension: connect product effects to payment flows with stable identities and reconciliation. It is outside the required core UPI/NPCI/RBI sandbox scope |
| Official AA | Implement current role-specific APIs, detached signatures, consent artefact validation, key exchange/encryption, FI schema validation and participant interoperability; resolve quarantined public contract issues |
| Scenario breadth | A bounded multistep library is implemented in `avenra/simulation.py`; selected participant-specific message/state-machine case packs remain unfinished. Product/payment bridging is optional broader scope |
| VM execution | Build/deploy Linux x86_64 workers with KVM/TUN, connect run-scoped credentials and scheduler, prove lifecycle/egress/tenant isolation and recovery |
| Additional source-only modules | Connect and qualify the included contract test packs and monitoring rule-authoring subsystem where required |
| Production operations | Portable deployment, production SSO/RBAC, service authentication/TLS, quotas, independent security/dependency review, performance/SLOs, HA/failover, encrypted off-host backup and message-broker recovery |
| Live rails | No live UPI connector exists. It requires a separate qualified participant integration and institutional authorization |

Obtaining public documents or importing additional repositories does not complete those capabilities. RBI Regulatory Sandbox admission is a separate institutional process; it is not a property enabled by this application.

## Financial and recovery boundaries

Amounts in owned payment APIs use integer paise. Commands forwarded to the product service use that service's native units and schema. The control database stores requests, identities, leases, messages and evidence, not another editable financial balance.

A payment response loss leaves its effect unconfirmed. Recovery observes or reuses the original leg key. A definitive beneficiary rejection creates a separate reversal from suspense. Refund reservations prevent concurrent over-refund. Settlement preparation computes obligations; independently supplied observations determine reconciliation.

Product commands persist their body/fingerprint and original service binding before dispatch. A command is fenced as unknown before its HTTP call. Reconciliation retains the original native idempotency key; a changed service binding is refused. Older uncertain records without a saved binding require investigation.

Monitoring confirms native inbox content before resubmission, verifies the returned report identity/profile and seals the observed result. Its reports remain distinct from local pre-debit threshold decisions. Workspace/environment checks constrain all exposed financial and report routes.

Required source attribution and technical identifiers are retained in `LICENSES/` and adapted engine code. The public product name is Avenra.

The executable UPI participant contract and its remaining official requirements are documented in [UPI_PARTICIPANT_ENGINE.md](docs/UPI_PARTICIPANT_ENGINE.md). The original UPI-focused objective does not require a complete lending, deposit or investment product catalogue. Official UPI compatibility and deployment qualification remain incomplete independently of those broader modules.

Connection revisions and the executable local participant adapter are described in [PARTICIPANT_CONNECTIONS.md](docs/PARTICIPANT_CONNECTIONS.md). New intents pin their adapter choices; future configuration changes cannot redirect recovery. External connection records remain drafts because their wire adapter, authoritative specifications and acceptance are unfinished. Stored endpoint/secret references do not establish connectivity.

The executable [synthetic bank services](docs/SYNTHETIC_BANKS.md) add separate scoped bank processes, actual mutual TLS, signed requests/responses, rotating/revocable private test identities, native-ledger financial execution and checkpoint recovery. Four bank-role adapters can be left running for the console. Logical PSP/network actors and the authoritative native ledger remain shared lab infrastructure. This capability supplies concrete synthetic integration evidence, not an official UPI wire profile or institutional production qualification.
