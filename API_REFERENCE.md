# Avenra supported API surface

This reference describes the implemented synthetic profiles. It is not a UPI network specification or an institutional interoperability claim.

## Authentication and scope

The base URL is `http://127.0.0.1:4827`. Financial routes use `/api/tenants/{tenant_id}/environments/{environment_id}`. A service bearer key is scoped to one workspace with read/write permissions, optional expiry and revocation. Institutional mode requires explicit expiry on service keys. Browser sessions require the matching origin and CSRF token for mutations.

`GET /api/catalog` returns only permitted workspaces/environments. `GET /api/health` reports process readiness. It does not establish that every financial dependency or production capability is qualified.

Create an empty banking environment with `POST /api/tenants/{tenant_id}/environments` and body `{"name":"My banking test","fixture_profile":"banking"}`. Wait for its overview status to become active before provisioning accounts. The legacy profile retains the original three sample accounts.

## Banking endpoints

Append the following paths to the scoped environment base. Request bodies are JSON objects with a 65,536-byte limit, except the explicitly versioned raw XML ingress documented below, which has an 8,192-byte limit.

| Method/path | Purpose and required fields |
|---|---|
| `GET /banking` | Latest banking records, accounts, balances and implemented capability flags |
| `POST /banking/banks` | `name`, `code`, `request_key`; optional supported participant `roles` |
| `POST /banking/customers` | `name`, `kind` (`customer` or `merchant`), `request_key` |
| `POST /banking/accounts` | `bank_id`, `customer_id`, local `vpa`, integer `opening_paise`, `request_key` |
| `POST /banking/accounts/{id}/state` | `state`: `active` or `blocked` |
| `POST /banking/resolve` | Local `vpa`; resolution is confined to this environment |
| `POST /banking/operations` | `payer_id`/`payee_id` or corresponding `payer_vpa`/`payee_vpa`, `amount_paise`, `request_key`; optional `fault`, `callback_id` |
| `GET /banking/operations/{id}` | Original operation, legs, messages and local policy decisions |
| `POST /banking/operations/{id}/reconcile` | Observe/recover the original financial identities; body `{}` |
| `POST /banking/operations/{id}/refund` | `amount_paise`, new `request_key`; direction is fixed to original payer |
| `POST /banking/operations/{id}/reversal` | Linked adjustment with its own amount and request key |
| `POST /banking/rules` | `name`, `threshold_paise`, `action` (`alert` or `block`), optional boolean `enabled`; publishes a version |
| `POST /banking/mandates` | `payer_id`, `payee_id`, `maximum_paise`, Unix `starts_at`/`expires_at`, `request_key` |
| `POST /banking/mandates/{id}/approve` | Explicit `synthetic_authorization: true` |
| `POST /banking/mandates/{id}/execute` | Stable `occurrence`, `amount_paise`, Unix `due_at` |
| `POST /banking/mandates/{id}/revoke` | Cancel future unexecuted occurrences; body `{}` |
| `POST /banking/disputes` | `operation_id`, `reason`, `request_key` |
| `POST /banking/disputes/{id}/state` | Reviewed state transition and `resolution` |
| `POST /banking/cycles` | `name`, `request_key`; assigns eligible confirmed operations once |
| `POST /banking/cycles/{id}/reconcile` | Independent `positions` object mapping institution IDs to signed integer paise |
| `POST /banking/consents` | `customer_id`, owned `account_ids`, `purpose`, Unix `expires_at`, `request_key` |
| `POST /banking/consents/{id}/approve` | Explicit synthetic authorization; returns local access token |
| `POST /banking/consents/{id}/revoke` | Invalidate local access; body `{}` |
| `GET /banking/consents/{id}/data` | Requires `X-Avenra-Consent`; returns authorized balances/history |
| `POST /banking/cases` | `name` and bounded scenario `plan`; publishes an immutable version |
| `POST /banking/test-runs` | `case_id`, `request_key`; creates a fresh financial fixture |
| `GET /banking/test-runs/{id}` | Run state and signed evidence when complete |
| `GET /banking/test-runs/{id}/evidence` | Download evidence JSON |

A simultaneous account ID and VPA must resolve to the same account. Reusing a request key with different financial content returns `409 IDEMPOTENCY_CONFLICT`. Owned payment amounts are integer paise, up to 100,000,000 paise per operation in this profile. Floating amounts and booleans are rejected.

Supported banking faults are `none`, `lost_debit_response`, `lost_credit_response`, `reject_credit` and `reject_authorization`. These are synthetic participant conditions; the response-loss cases use the actual local TCP fault engine.

The bounded banking plan fields are `payer_opening_paise`, `payee_opening_paise`, `amount_paise`, `fault`, `reconcile`, `expected_state`, `expected_payer_paise` and `expected_payee_paise`. Extra executable/script fields are rejected. This is a single-operation plan, not a general programmable banking workflow.

## Banking product adapter

`POST /banking/core-commands` accepts `action`, native `body`, `request_key`, and numeric `resource_id` for an existing resource. Supported actions are client creation; loan product creation, application, approval, disbursement and repayment; savings product creation, application, approval, activation, deposit and withdrawal.

**Product command bodies use the native product API's units and validation. They do not reinterpret values as paise.** For example, native `transactionAmount: 100` in an INR savings deposit represents ₹100. Owned interbank `amount_paise: 100` represents ₹1.

Use `GET /banking/core-commands/{id}` to observe `pending`, `unknown`, `succeeded` or `rejected`. Unknown outcomes require `POST /banking/core-commands/{id}/reconcile` with `{}`. This retains the original idempotency identity and rejects a changed service binding. It is not a new product transaction.

`GET /banking/core/{resource}/{optional numeric id}` supports clients, loans, loan-products, savings, savings-products, offices and currencies. Each environment needs its own operator-configured native tenant binding. Arbitrary native endpoints are not exposed.

## Analysis and provider contracts

Confirmed banking operations are queued for monitoring when their workspace has a configured simulation binding. `POST /banking/analysis` with `operation_id` obtains the same durable analysis identity on replay. `GET /banking/analysis/{id}` returns state and signed report evidence. A succeeded ingestion acknowledgement alone is insufficient: the implementation requires the matching report and successful native rule results.

`POST /banking/contracts` accepts an OpenAPI 3 `specification` with examples. Imports are namespaced to the environment. External schema/example references and executable dispatch extensions are rejected. `GET /banking/contracts` lists scoped contracts. `POST /banking/contracts/{native_id}/invoke` accepts bounded `method`, `path` and optional `body`. Mock responses do not post financial effects.

## Signed callbacks

Register `name`, operator-approved `url` and `request_key` at `POST /banking/callbacks`. The response returns its local signing secret. Supply the destination ID as `callback_id` when creating an operation. A terminal operation and its notification intent are persisted together.

Delivery headers are `X-Avenra-Event`, `X-Avenra-Timestamp` and `X-Avenra-Signature`. The signature is `v1=` plus hexadecimal HMAC-SHA256 using the UTF-8 signing secret over `event_id + "." + timestamp + "." + raw_body`. Verify the raw UTF-8 JSON body, check a suitable timestamp window and persist a receiver inbox keyed by event ID and payload hash. Network retries can redeliver the same event; the receiver must deduplicate it. Failed deliveries retain attempts and eventually enter dead-letter state.

## Limits

Runtime policies, permissions and operational incidents use the scoped
`/banking/runtime` family in [the monitoring API](docs/OPERATIONS_MONITORING.md#scoped-api).
Current incidents stay visible independently of paginated incident/event history.
Acknowledgement records operator review and does not resolve financial uncertainty.

Lists generally expose the latest 100 control records. Financial account reads and consent history traverse native accounting pages with a bounded capacity limit and explicit failure rather than silently omitting later pages. Institutional AA/UPI wire APIs, bank UAT connectivity and guest execution are not exposed as implemented capabilities.


## Institution experiment API

All paths below extend the authenticated workspace/environment prefix with `/banking/simulation`. Existing read/write authorization applies. The participant envelope is a synthetic Avenra contract; it is not an official UPI or AA wire format.

| Method/path | Contract |
| --- | --- |
| `GET /` | Profiles, plans, run summaries, scoped message receipts, supported operations, example plan and explicit external qualification gaps |
| `POST /profiles` | Optional `profile_key`; only the reviewed synthetic `document` can execute. Empty body installs the default profile idempotently |
| `POST /plans` | `profile_id`, `name`, `plan`; publish an immutable plan version |
| `POST /runs` | `plan_id`, `request_key`; allocate a fresh fixture and durable work item |
| `GET /runs/{id}` | State, step receipts, blocking reason and sealed evidence when available |
| `POST /runs/{id}/resume` | Empty object; enqueue recovery of an unfinished run. Sealed runs return 409 |
| `GET /runs/{id}/evidence` | Download the retained signed evidence; pending evidence returns 409 |
| `POST /messages/sign` | `profile_id`, `sender`, `recipient`, `message_key`, `kind`, `payload`, optional `issued_at` and `lifetime_seconds`; return the scoped lab envelope and signature |
| `POST /messages` | Exactly `profile_id`, `envelope`, `signature`; validate the synthetic participant contract and retain the result |
| `POST /clock` | `seconds`, `request_key`; advance an existing isolated-fixture business clock idempotently |

See [Institution lab](docs/INSTITUTION_LAB.md) for aliases, closed operation arguments, fixture limits, exact expectation semantics, message replay behaviour and qualification boundaries. Role message acceptance and financial command execution are separate operations.

## Participant payment APIs

The `/banking/upi` scoped route family implements device enrolment/linking, balance inquiry, merchant QR parsing, separately authorized participant payments/collect/refunds, status recovery, public keys, key rotation/revocation and retained message/evidence reads. Full request fields and exact scope are in [the participant engine API table](docs/UPI_PARTICIPANT_ENGINE.md#scoped-api). These are Avenra lab interfaces; they are not the current official UPI wire APIs.

The same route family now exposes `/wire/contract` and `/wire/messages[/{message_id}]` for a versioned synthetic XML contract. Raw XML inspection preserves original receipt identities and performs no financial dispatch. Opt-in XML connection profiles drive the existing durable worker through schema and signature checks. See [the wire API and exact limits](docs/UPI_WIRE_CONTRACT.md).

The `/banking/upi/connections` routes create immutable connection revisions, retain signed configuration checks and bind the executable local adapter for new intents. See [participant connection contracts](docs/PARTICIPANT_CONNECTIONS.md#api). External records accept preparation metadata only; they perform no external requests, do not resolve credentials and cannot be activated. Payment adapter choices are immutable after preparation.

`avenra-tls-synthetic-bank/1` is a separate executable adapter for remitter/beneficiary roles. It requires a provisioned private lab identity, an exact loopback HTTPS origin and a scoped secret reference. Validation performs a signed mutual-TLS probe; financial legs retain their native identities and signed bank transport traces. The private synthetic service accepts only the declared `/v1/commands` contract, not a general HTTP proxy. See [synthetic bank service boundaries](docs/SYNTHETIC_BANKS.md).
