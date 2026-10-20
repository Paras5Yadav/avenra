# Avenra

Avenra is a locally operated financial testing platform. It includes configurable institutions and customers, durable interbank operations, banking product integration, transaction monitoring, provider API mocks, signed callbacks, encrypted financial-data exchange and retained evidence.

**This installation is an engineering build with verified synthetic flows. It is not yet a production-qualified bank or UPI sandbox.** There is no live payment connector. Current release gates are recorded by `scripts/release_readiness.py`; implemented capabilities and remaining work are listed in [BANKING_IMPLEMENTATION.md](BANKING_IMPLEMENTATION.md).

## Open this installation

```sh
python3 scripts/local.py start
python3 scripts/local.py status
```

Open <http://127.0.0.1:4827/> and select **Banking workspace**. Create a new environment with the **banking** fixture profile to start without predefined customers. The older **UPI development** environment and six original scenarios retain their original sample accounts so their existing evidence remains usable. Display names are configurable synthetic fixtures.

The current control database is PostgreSQL, core schema version 9, with additive lifecycle schema 2, runtime-control schema 1 and financial-data schema 1. The old SQLite database is a preserved migration source, not the running control authority. A private migration receipt records 432 preserved records across 35 tables at migration time; subsequent migrations and operations add to that state.

**Banking workspace → Financial data → AA encrypted data exchange** opens the new controls in this same app. Select **Encrypted financial-data rehearsal** for the configured synthetic providers and recipients. Signed consent, encrypted native deposit statements, recipient verification and browser decryption execute through real role processes. Existing environments and the older consent tools remain available. Setup, recovery, supported scope and unresolved official requirements are documented in [FINANCIAL_DATA.md](docs/FINANCIAL_DATA.md).

**Participant deliveries** now exposes retained notification attempts and recovery of original notices. Application delivery observations also recover exhausted consent/notification jobs under their original identities. Real outage, acknowledgement loss, process-crash and stale-consent cases are qualified separately; these controls do not establish complete official interoperability or whole-product production readiness.

## Implemented capabilities

| Capability | Current implementation |
|---|---|
| Institutions and accounts | Scoped virtual banks, participant roles, customers, merchants, local payment addresses, opening postings and account restrictions |
| Interbank payments | Separately persisted payer debit and beneficiary credit through clearing; stable leg identities and message observations |
| Failure recovery | Real response-byte loss; unknown outcomes; confirmation of original effects; no new financial identity on retry |
| Refunds and reversals | Linked adjustments to the original payer, reserved refundable amounts, duplicate and over-refund protection |
| Mandates | Explicit synthetic approval, occurrence identity, scheduled execution, expiry and revocation |
| Disputes and settlement | Case lifecycle, expected participant positions, independently supplied observations and retained reconciliation discrepancies |
| Consent testing | Local approval, scope, expiry, revocation, access tokens and ledger-backed financial data; synthetic protocol only |
| Encrypted AA role testing | Selected public AA/FIP/FIU deposit contracts; mTLS/JWS, challenge linking, signed consent, X25519/HKDF/AES-GCM, native XML statements, encrypted browser review, frequency/expiry/revocation and durable recovery; official interoperability remains unqualified |
| Banking products | Source-built banking service; client creation, loan products/applications/approval/disbursement/repayment, savings products/accounts/deposits/withdrawals |
| Product recovery | Original command key and service binding retained; explicit uncertain-command reconciliation; native duplicate protection verified |
| Monitoring | Confirmed payments mapped to the pinned analysis profile; native rule results, alerts, versioned evidence and lost-acknowledgement recovery |
| Local risk policies | Immutable threshold-policy versions; independent local alert/block decisions before debit |
| Provider API testing | Actual contract service with environment namespaces, bounded OpenAPI 3 imports and mock invocation |
| Notifications | Durable signed HTTP callbacks, stable event identity, delivery attempts, retries and dead-letter state |
| Identity | Scoped read/write service keys with expiry/revocation; institutional OIDC code/PKCE/JWKS support and negative identity tests |
| Runtime controls | Existing-console policies, subject/environment permissions, shared request/admission budgets, observed dependency readiness and worker/request metrics |
| Operational incidents | Same-console monitoring policies, real worker/accounting failure signals, immutable transitions/acknowledgements, current incidents and authenticated history navigation |
| Deployed synthetic security | Real HTTPS issuer, stable subject/session checks, mTLS API/accounting gateway, restricted database runtime roles and actual two-node/worker qualification |
| Controlled application releases | Signed deterministic bundles, exact offline wheel locks, runtime integrity, shared-database activation fences, one reviewed additive operations migration, compatible forward-history code rollback and operator-crash recovery |
| Scenarios | Original six scenarios, single-payment banking plans and a bounded multistep institution lab with virtual business time, authenticated synthetic messages and signed evidence |
| Data recovery | Control/accounting archive restores; isolated banking-product, monitoring and provider-database restore comparisons |

Managed deployments can now capture and rebuild a signed cold recovery set
containing the application, offline runtime inputs, pinned native binaries and
financial/private checkpoints. Restored deployments remain guarded until explicit
new-database and service binding. See [coordinated recovery](docs/COORDINATED_RECOVERY.md)
for the supported local/TLS synthetic participant profile, authenticated bank
address replacement, AA/FIP/FIU topology recovery and the remaining whole-service work.

The accounting engine owns simulated payment balances. The banking product service owns its loan and savings records. These are separate financial domains: a loan disbursement is **not automatically bridged into an interbank payment account**. That bridge is an optional broader banking extension, not a required blocker for the core UPI/NPCI/RBI-focused sandbox.

Monitoring uses analysis messages shaped for its native interface. They are not UPI wire messages. Monitoring reports are post-payment observations and do not authorize a payment. Local threshold policies are a separate mechanism.

## Institution lab

Open **Banking workspace → Institution lab**. Install the reviewed synthetic profile, publish the supplied 15-step plan, run it in a fresh ledger and inspect/download evidence. The plan exercises message replay, real credit-response loss, recovery, partial refund, exact balances, consent expiry and settlement reconciliation. See [the operation library and boundaries](docs/INSTITUTION_LAB.md). Official UPI/AA interfaces and institution acceptance remain unqualified.

## Running services

| Service | Local endpoint | Current scope |
|---|---|---|
| Console/API | `127.0.0.1:4827` | Synthetic financial workspace |
| Accounting | `127.0.0.1:4830` | Authoritative simulated payment journal |
| Fault control | `127.0.0.1:4831` | Per-request TCP fault injection |
| Provider contracts | `127.0.0.1:4840` | Imported API examples and mock execution |
| Banking products | `127.0.0.1:4841` | Configured synthetic product tenant |
| Monitoring ingestion/admin | `127.0.0.1:4842`, `4843` | Private local analysis deployment |
| Control/accounting database | `127.0.0.1:55437` | Private authenticated database installation |
| Monitoring recovery reader | `127.0.0.1:55439` | Restricted read-only ingestion confirmation |

The native launcher manages only its tracked console, accounting and fault processes. Additional services run in the private local container profile. They can be managed using `scripts/services.py` and the private monitoring deployment. See [OPERATIONS.md](OPERATIONS.md) for restart and restore commands and limitations.

## Source and builds

Adapted source is included under `engines/`. Pinned revisions, original licence/copyright notices, required technical identifiers, source hashes, image receipts and adaptations remain under `LICENSES/`. Product-facing branding uses Avenra. A source import, image startup or health response is not a conformance or security qualification.

The accounting and fault engines run as native source-built executables. The banking product service was compiled from the included source. Provider and monitoring services run pinned local images with recorded digests; their included source is available separately. A cold-cache monitoring routing fix is recorded and built by `scripts/build_monitor_router.py`.

For source preparation and native builds:

```sh
python3 scripts/prepare_engines.py
python3 scripts/build_engines.py
.local/runtime/bin/python scripts/build_banking_core.py
```

The installed Python runtime uses [requirements.txt](requirements.txt). The current launcher and database paths target this Mac. The monitoring bootstrap also uses a private pinned deployment reference retained under `.local/references/`; a portable production deployment pack is not delivered by that local bootstrap.

[Controlled application releases](docs/RELEASES.md) add signed deterministic
bundles, complete offline dependency locks, verified runtime preparation,
database-fenced schema-preserving activation and forward-history code rollback.
These operator tools use the same application. They preserve the current local
launcher; native-service upgrades, general schema-changing migrations and Linux/fleet
qualification remain separate work.

[Operational monitoring](docs/OPERATIONS_MONITORING.md) adds durable incidents and
response guidance inside Runtime controls. The reference capacity rehearsal uses
actual competing tenants, killed worker processes and native journal comparison;
its report states the exact local workload and never implies official UPI TPS.

The included execution subsystem requires Linux x86_64, KVM, TUN and its full worker deployment. This Apple Silicon Mac cannot qualify it. `scripts/qualify_execution.py` reuses the included API client for future Linux lifecycle checks. Its preflight currently records a hardware blocker; guest code is never run as a host process.

The existing Scenario library now also has eight executable POC demonstrations,
with a button to queue them together and results in Runs & evidence. A distinct
ARM64/KVM POC backend uses actual ephemeral Linux guests on a dedicated local
worker. It demonstrates authenticated execution, isolation, limits and cleanup;
it does not qualify the included x86 engine or a general production worker fleet.
See [POC scope and limits](docs/POC_ACCEPTANCE.md) and run
`scripts/verify_poc.py` with the installation's Python runtime for its HTTP pack.
The full synthetic POC remains incomplete while specialized journeys are missing.

## Verification and release status

```sh
.local/runtime/bin/python scripts/verify_banking.py
.local/runtime/bin/python scripts/verify_banking.py --postgres
.local/runtime/bin/python scripts/verify_banking_http.py
.local/runtime/bin/python scripts/verify_products.py
.local/runtime/bin/python scripts/verify_units.py
.local/runtime/bin/python scripts/verify_production_runtime.py
.local/runtime/bin/python scripts/verify_acceptance.py
.local/runtime/bin/python scripts/release_readiness.py
```

Recovery and backup commands intentionally stop/restart owned local services; run them separately as described in [OPERATIONS.md](OPERATIONS.md). [VERIFICATION.md](VERIFICATION.md) records observed coverage, failed attempts and qualification limits. [API_REFERENCE.md](API_REFERENCE.md) documents supported routes and units.

The readiness command defaults to institutional hosting and currently exits nonzero because that profile is unqualified. Use `--profile local` to check the supported local acceptance boundary.

The existing participant connection API also offers opt-in versioned synthetic XML validation, scoped wire export/inspection and an independent parser/signature oracle. See [the XML contract and exact execution boundary](docs/UPI_WIRE_CONTRACT.md). Its two storage-specific acceptance packs are mandatory local release gates.

Production hosting still needs a qualified identity/role deployment, hardened ingress and service authentication, independent security review, capacity/failover qualification, encrypted off-host backups, operational ownership and a portable deployment pack. Bank-connected UAT additionally needs implemented participant-specific wire/security contracts, bank-issued credentials/certificates and approved connectivity. Official AA interoperability and VM isolation remain unqualified. Local evidence HMACs establish integrity relative to this installation's key, not independent attestation.

## Participant payment lab

Open Banking workspace → UPI participants to create a configurable fixture, enrol and link devices, prepare a payment or merchant collect, explicitly authorize it and inspect each actor, financial leg and retained result. Institution lab → Publish UPI journey provides a 21-step recovery/refund/expiry plan with independent expected balances. See [the participant engine guide](docs/UPI_PARTICIPANT_ENGINE.md) for the exact supported synthetic contract and explicit official compatibility gaps.

The local launcher uses ASGI request serving. Durable workers can run separately with `python -m avenra.worker --data /absolute/private/data` alongside `python -m avenra.asgi --data /absolute/private/data --mode api`. Institutional production qualification remains incomplete.

Engine status → Runtime controls opens the additive runtime panel in the current
app. The current loopback console retains local operator access and HTTP;
stronger deployment settings are exercised in private local fixtures. See
[the runtime deployment guide](docs/DEPLOYMENT_RUNTIME.md) for what is implemented
and [the full completion inventory](docs/PRODUCTION_COMPLETION_MATRIX.md) for
remaining work. Synthetic SSO/TLS test passes do not enable an official bank
connection or establish whole-product production readiness.
