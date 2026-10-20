# Avenra engineering verification

Updated 20 October 2026 on this Apple Silicon Mac. All results below concern synthetic data and the selected supported local flows. They do not establish a production deployment, a full imported-source audit, UPI conformance or institutional certification.

## Current POC increment

Build `983ff3aa46d09871ac7b9309cc57503eae1d3aa56885ab76a9ae7367562837e2`
passes the same-app POC HTTP pack: **46 checks across eight demonstrations**.
All eight also pass when queued through the main console against its PostgreSQL
control store, retaining **92 run assertions** and authenticated evidence. The
original HTTP regression passes 38 checks, XML wire regression passes 75, and
lifecycle regression passes 107: **220 targeted regression checks**. These
overlapping checks are separate evidence sets, not independent certification.
The console reload preserved financial counts, a sampled native journal/balance
inventory and the original accounting/fault process identities.

Receipts: `.local/poc-verification.json`,
`.local/poc-current-deployment-verification.json` and
`.local/poc-console-reload-verification.json`. The current Scenario library can
queue every available POC demonstration. Twelve of thirteen broad scope areas
have stated demonstrations; this is not a percentage of production completion.
Specialized stored-value/offline, delegated, credit and cross-border journeys
remain unsupported. Full POC completion remains false.

The complete production acceptance matrix has **not** been requalified for this
increment. Production readiness correctly fails. In particular, independent
PSP/network cold restore, full-service/fleet security and operations, the included
x86 execution engine and official/institutional acceptance remain unqualified.
The separate ARM64 POC proves its observed local KVM assertions only. The failed
VM startup-race attempt and verifier/integration attempts are retained; successful
retests do not erase them.

## Previous qualified XML recovery build

Build `4da1086c0e88639ccf470ff81302a1e23598f91d57f6ce5de63ad10720688f30` passes its complete **31-suite matrix with 1,648 checks**, plus **71 deployed-runtime checks**: **1,719 checks in those two receipts**. The two XML packs contribute 150 checks, including real HTTP/mTLS/native effects, independent parsing/signatures and process death with actual lease expiry. The separate XML cold recovery pack adds 76 checks within that matrix, including exact envelope/signature preservation, original-ID credit recovery and refusal of an incompatible signed rollback. The full 350-file encrypted snapshot authenticates and restores with its exact manifest. Local readiness passes; institutional readiness remains false. This is the declared local synthetic scope, not completion of the full production objective.

The first retained XML attempt passed 65 checks and failed a verifier assertion because its operation ID was captured before authorization. The native engine had posted the original debit and credit exactly once. The verifier now captures the accepted ID after authorization; the failed attempt remains retained.

## Previous completed acceptance results

The current AA recovery candidate is build `2c7239fe80770433baa69dd8cdc24bbaa983e369a5f0230c001a0916030008f2`. Its complete **28-suite matrix passes 1,422 checks**, and the separate deployed-runtime pack passes **71 checks**: **1,493 checks in those two receipts**. The AA cold-topology suite passes 53 checks, including two complete relocations and original-job uncertainty during missing/altered routing. The full 350-file snapshot encrypted and authenticated-restored with an identical manifest. Local readiness passes; institutional readiness remains false. These receipts qualify the declared local scope; the full production objective remains open.

| Suite | Observed result | Retained receipt |
|---|---|---|
| Original HTTP flows and financial scenarios | 38 checks passed | `.local/verification.json` |
| UPI participant flows with SQLite and PostgreSQL control fixtures | 97 checks passed per store | `.local/upi-verification.json`, `.local/upi-postgres-verification.json` |
| UPI scoped HTTP and connection revision flow | 35 checks passed | `.local/upi-http-verification.json` |
| Separate synthetic bank services, mutual TLS, signatures and financial recovery | 37 checks passed | `.local/synthetic-bank-verification.json` |
| Banking behaviour with SQLite control fixtures | 63 checks passed | `.local/banking-verification.json` |
| Banking behaviour with PostgreSQL control fixtures | 63 checks passed | `.local/banking-postgres-verification.json` |
| Banking HTTP, callbacks, monitoring, provider/core adapters and access boundaries | 31 checks passed | `.local/banking-http-verification.json` |
| Multistep institution lab with SQLite fixtures | 49 checks passed | `.local/simulation-verification.json` |
| Multistep institution lab with PostgreSQL fixtures | 49 checks passed | `.local/simulation-postgres-verification.json` |
| Institution lab scoped HTTP/worker flow | 16 checks passed | `.local/simulation-http-verification.json` |
| Native lending and savings lifecycle | 27 checks passed | `.local/product-verification.json` |
| Real response loss and service-binding recovery guards | 9 checks passed | `.local/service-recovery-verification.json` |
| Identity and store unit tests | 9 tests passed | `.local/unit-verification.json` |
| ASGI serving and worker lifecycle | 9 checks passed | `.local/asgi-verification.json` |
| Control/accounting, private deployment/AA roles, exact operational history restoration and new-write UTC policy | 15 checks passed | `.local/backup-verification.json` |
| Encrypted AA/FIP/FIU deposit roles, bidirectional outage/retry/crash recovery and admission | 115 checks passed per store | `.local/aa-verification.json`, `.local/aa-postgres-verification.json` |
| Runtime deployment, TLS/SSO/permissions, limits, workers and enforced inactive private recovery | 71 checks passed | `.local/production-runtime-verification.json` |
| Signed application archives, offline runtimes, fenced activation, compatible code rollback, binding capability declarations and operator-crash recovery | 54 checks passed | `.local/release-verification.json` |
| Standalone SQLite checkpoints with live committed WAL and an uncommitted writer | 11 checks passed | `.local/checkpoint-verification.json` |
| Operational policies, incident continuity, scoped history and actual dependency/monitor-owner outages | 94 checks passed | `.local/operations-verification.json` |
| Reviewed additive migration against the real preserved predecessor, DDL-process death and compatible rollback | 20 checks passed | `.local/operations-upgrade-verification.json` |
| Signed coordinated cold recovery, new database identities, owned native/bank processes, authenticated physical routes, operator crashes and original-ID recovery | 71 checks passed | `.local/coordinated-recovery-verification.json` |
| Cold AA/FIP/FIU topology, original jobs/notices, expiry, process crashes and two successive relocations | 53 checks passed | `.local/aa-coordinated-recovery-verification.json` |
| Exact local mixed-tenant workload version 2, continuous clocks, actual worker kills and independent native accounting comparison | 27 checks passed | `.local/capacity-verification.json` |
| Native journal UTC write/read policy | 4 checks passed | `.local/accounting-clock-verification.json` |
| Authenticated encrypted restore including financial-data private identities/journals and the inactive restore guard | 350 files restored; manifests match | `.local/financial-data-archive-verification.json` |
| Additional service-volume/database restore | 7 checks passed | `.local/component-backup-verification.json` |
| Private database authentication | SCRAM authentication enabled; accounting role is not a superuser | `.local/database-auth-verification.json` |
| VM worker preflight | Blocked: Linux x86_64/KVM/TUN are unavailable | `.local/execution-verification.json` |

The application acceptance receipts include an owned-source fingerprint. Local readiness requires every applicable receipt and case pack to match the current build; a missing or stale receipt keeps that gate false. Whole-product `production_ready` and official/institutional profiles remain false while their requirements are open. These suites overlap in coverage; counts must not be interpreted as independent certifications or complete upstream test suites.

The previous bank-recovery checkpoint is build
`f55c0cf6041b399a3262e80ab6bc55399e43c9e6cc6a6c9d906912b789644036`.
Its recovery and signed-release packs pass 71 and 54 checks. The completed
27-suite matrix passes **1,369 checks**, and the separate deployed-runtime pack
passes **71 checks**: **1,440 automated checks in those two receipts**. The
completed 349-file full snapshot encrypted and authenticated-restored with an
identical manifest. The local readiness command exits 0; institutional readiness
exits 2 with `production_ready: false`. All receipts match this source build.

The previous bank checkpoint's matrix records a verified resume: 24 completed cases were retained only after
their source fingerprint, current case script hashes, passing receipts and exact
command/argument definition were checked. The legacy matrix definition was
independently recovered from the retained signed application, rather than
inferred from case names. Copies and hashes of the reused receipts remain in the
new attempt. The failed capacity case and subsequent cases executed again;
neither a stale build nor the interrupted benchmark was promoted to success.

The previous bank checkpoint's `avenra-local-capacity/2` run offered 1,040 payments, accepted 310 and
explicitly refused 730. Steady traffic completed 120/120 with admission p95
39.56 ms and completion p95 2.60 seconds. Protected traffic completed 60/60 under
competing-tenant pressure with admission p95 31.29 ms and completion p95 3.20
seconds. Generator misses and polling errors were zero; maximum observed
wall/monotonic drift was under one millisecond. These are the declared local
workload results, not institutional capacity or a general clock accuracy claim.

A prior version 1 run crossed actual clamshell/maintenance sleep and did not
pass. Its role grants and transport deadlines expired while the host was
suspended. Investigation also corrected the test observer's recovery call from
unsupported `POST .../status` to the actual `POST .../reconcile` route. Version 2
adds explicit clock-continuity admission and reports unexpected client responses.
The reference traffic and latency/error budgets were retained. A separate scoped
operator recovery resolved all 22 retained requests: 10 succeeded under original
identities, while 12 expired undispatched approvals declined without a debit.
Every original leg and native effect was checked; temporary recovery grants were
revoked. This did not convert the interrupted benchmark into a passing run.
Evidence remains in `.local/qa/capacity_3b5aedcb87eb406dbcb0/`.

The new recovery path restores two independently running mTLS synthetic banks at
authenticated new physical addresses. Original logical connection rows and
payment topology remain byte-identical. A physically unacknowledged credit
recovers exactly once after restoration and signing-key rotation; an actual bank
process kill after activation also recovers under the original routing decision.
Original installation paths are unavailable and observed original bank listeners
receive zero recovered traffic. Complete original bank/identity file hashes and
receipt rows match before/after. Changed keys/routes/actors, cross-bank routing,
non-loopback destinations and linked receipt databases are refused. Actual
operator death after the first bank registers is resumed without replacing that
surviving process. A genuine signed historical build lacking required binding
capabilities is refused without changing any restored control row or sequence.
Signed candidate capability inspection refuses unsupported versions and executable
Python expressions without executing them. Whole-service SSO/other-provider recovery
and full production qualification remain open.

Earlier retained attempts exposed macOS framework Python's executable rewrite;
the actual image/command is now observed through an isolated verified runtime
probe and pinned. Two test comparisons were corrected with independent source
checks: PostgreSQL row mappings must compare values, and SQLite backups need not
have the original physical page/header bytes. Before/after source file hashes
and full ordered receipt/schema hashes now prove preservation directly. Failed
attempts remain retained under `.local/qa/coordinated_recovery_*/verification.json`.

An intermediate build
`681899f4862497351ebd6ffd278b0baa62e01e5b5af0a75d09d166abc4cb8d64`
completed 27 suites/1,361 checks before the binding-capability gate was added.
Its earlier interrupted controller stopped after 18 passing suites; that
incomplete attempt is retained and was not promoted to acceptance. The complete
run used a separately tracked process and finished successfully.

The earlier coordinated-recovery checkpoint was build
`0f05f4ddc7352af2b4422d1da47030a945979a970e75580e6ae58c4b01f649d1`.
Its sequential **27-suite matrix passed 1,331 checks**, and the separate deployed
runtime suite passed **71 checks**: **1,402 automated checks in those two
receipts**. The completed 349-file native/operational/private-role backup also
encrypted and authenticated-restored with the identical manifest. The explicit
local readiness command exits 0; the institutional command exits 2 with
`production_ready: false`. The full production objective remains open.

The new 39-case recovery pack uses dedicated source and recovered native engines,
separately trusted application/recovery signatures, actual encrypted archives,
new restricted database roles, complete table/sequence comparisons, rebuilt
offline runtimes and original installation paths made unavailable. Operator
processes actually exit after the first database commit and after activation
publication. Original-decision replay completes each inactive attempt without
replacing financial identities. The same app confirms its physically
unacknowledged original transaction once, retains valid participant evidence and
completes a new payment. Both prepared original databases remain unchanged by
the recovered application.

A separately captured source has an actual registered callback destination but
no service settings file. Its recovered copy refuses activation before starting
native or notification processes. The recovery preflight checks retained
notification/integration/financial-data records in addition to settings. A valid
local-participant activation starts with no implicit development callback egress.
Other bank/AA/SSO/service bindings remain inactive until coordinated replacement
is implemented; the pack does not qualify those full-service topologies.

Two real defects were retained and fixed during this work. Strict recovery-set
verification found archive-created intermediate directories with non-private
modes: every ancestor is now created explicitly with mode 0700. Native process
ownership checks also exposed macOS `ps` command-column truncation, including
with wide output when another column followed it. The command is now read alone
between matching process birth stamps and checked against the directory-bound
executable, binary pin and actual listening PID. An earlier test assertion was
also corrected to observe uncertainty before starting a worker; automatic
original-ID recovery had already completed by its old assertion point. Failed
attempts remain under `.local/qa/coordinated_recovery_*/verification.json`.

At that earlier checkpoint the reference capacity run offered 1,040 payments, accepted 309 and
explicitly refused 731 under the published limits. All accepted identities
completed with original native effects. Steady traffic completed 120/120 with
admission p95 39.85 ms and completion p95 2.52 seconds; the protected tenant
completed 60/60 under competing-tenant pressure with admission p95 36.41 ms and
completion p95 3.23 seconds. This remains the exact local 180-second workload,
not an institutional capacity or multiple-host availability claim.

The tracked console was reloaded without restarting its original native services.
Financial/control counts and one original active native ledger's account/journal
hashes matched across the final reload. Its readiness exposes the loaded current
source and actual control/worker/accounting/operations observations. Evidence:
`.local/qa/coordinated-recovery-notification-reload-verification.json`.
See [COORDINATED_RECOVERY.md](docs/COORDINATED_RECOVERY.md) for commands, scope and
the still-open whole-service, off-host and institutional recovery requirements.

An earlier 19 October integrated checkpoint was build
`b60d6713947a617808f00f6ef41a4c2f57a542f2441ea3ffd1489eaddd40789a`.
Its sequential 21-suite matrix passed 1,008 checks, with 70 additional deployment
runtime checks passing for the same build (1,078 automated checks in those two
receipts). The separately retained 348-file encrypted recovery and browser
observations provide additional bounded evidence. The full production objective
remains active; this is not a completion declaration.

The earlier notification-recovery checkpoint was build
`d06c2ad2907df131210d89661a5b1d98ee0beb0a71f080d127460d338bc44bab`.
Its 21-suite matrix passed 1,090 checks, with 70 additional deployment-runtime
checks for the same build (1,160 automated checks in those two receipts). The
then-current 348-file completed backup also encrypted and authenticated-restored
successfully, including migrated role journals.

The earlier release/recovery checkpoint was build
`21161b4684321b5b6c71ebdc6433c76f67e1320e14ff29b6c92dcb3f60c8b370`.
Its sequential **23-suite matrix passed 1,150 checks**, including the 49 release
and 11 standalone-checkpoint cases. The deployed runtime suite passed another
70 checks for that same build: **1,220 automated checks in those two receipts**.
The completed 348-file control/accounting/private-role checkpoint also encrypted
and authenticated-restored successfully. The local readiness profile passes;
whole-product production readiness and all official/institutional profiles remain
false. No complete-goal or official-conformance claim is made.

The earlier operational checkpoint was build
`b32db6cfb0346bb5f25f3060f392edcaadd858f1943dcb2f4fcfe2ded106469a`.
Its sequential **26-suite matrix passed 1,292 checks**, and its separate deployed
runtime suite passed **70 checks**: **1,362 automated checks in those two
receipts**. The completed 348-file checkpoint also encrypted and authenticated-
restored with an identical manifest. Full native restoration compares every
operations table's ordered row hash and verifies all four retained policy
signatures, alongside the original financial balances, journal and evidence.
The explicit local readiness check passes. Whole-product production readiness
and all official/institutional profiles remain false; the full goal stays open.

The exact `avenra-local-capacity/1` run offered 1,040 synthetic payments,
accepted 310 and explicitly refused 730 under the published admission limits.
Every accepted identity completed with its original native accounting effects.
The steady cohort completed 120/120 payments with observed admission p95
38.99 ms and completion p95 2.56 seconds. The protected tenant completed 60/60
during competing-tenant pressure, with admission p95 33.61 ms and completion p95
3.12 seconds. Generator misses and poll errors were both zero. Both workers were
actually killed while the API remained available; the retained incident and
subsequent recovery were observed. This is a 180-second reference workload on
this host, not an institutional capacity or multiple-host availability result.
The compact latest receipt binds the complete private per-request evidence by
SHA-256; readiness validates that binding rather than relying on the summary.

Monitoring appears in the existing Engine status → Runtime controls screen.
An actual browser rehearsal on that build used a deliberately strict latency
threshold and real request observations. The same incident retained firing,
acknowledgement, recovery and resolution. Restoring the normal policy left zero
open incidents; native account/journal hashes did not change and no browser
errors were observed. Evidence is retained in
`.local/qa/operations-current-browser-verification.json` and
`.local/qa/operations-current-browser-recovered.png`.

Retained failed attempts exposed two operational defects. A pending incident
could count unknown telemetry toward its firing dwell; it now restarts that
dwell after an unknown sample. Both supported stores exercise pending and
recovery continuity. A large capacity diagnostic report also failed when
replacing a file through the bounded configuration reader. Complete evidence
now remains in its private attempt directory, while the latest receipt is a
small hash-bound summary. The general configuration size limit was preserved,
and the prior report was retained. Earlier matrix failures also retained a
preservation-allowlist mismatch and a deliberate fixed-source mismatch after
the continuity repair; they are not silently promoted into passing results.

The migration pack executes the actual signed predecessor installer/runtime,
not a relabelled current fixture. It observes absent operations tables, refuses
live-node migration, kills an operator inside transactional DDL, verifies full
rollback, retries under the original attributed request and activates current
code. Compatible predecessor code can then be selected while retaining the new
tables and the original native financial effect. General migrations, native
service/fleet releases and whole-system recovery remain unfinished.

The release cases build byte-identical signed archives from identical pinned
inputs, reject changed/extra/duplicate/missing/link/traversal payloads, prepare
fresh offline runtimes and verify dependency integrity. Actual API/worker nodes
hold database fences; active-node, stale-source, stale-CAS, incompatible-version
and altered-request activation attempts are refused. A distinct source build
resumes accepted participant identities; rollback preserves forward financial
history and recovers a physically unacknowledged native posting exactly once.
Operator processes actually exit between durable preparation, commit and
publication. Recovery retains aborted attempts or publishes the committed release
as appropriate. Private recovery preserves original release selection/pending
records and cannot autoactivate restored interrupted settings.

The full restore exposed a real transient-SQLite-file defect: a bank checkpoint
manifest could include shared-memory bytes while backup connections awaited
garbage collection. Those files could disappear before restoration. Bank,
issuer and AA snapshots now use a common explicitly closed standalone backup
primitive. Its independent immutable reader observes exact committed WAL rows,
excludes an open uncommitted write, and needs no sidecars; the original writer
and journal mode remain intact. Bank restore validates paths/private hashes
before copying and verifies original receipt counts. Native full restoration
passes after the fix. Earlier failed attempts remain retained; an additional
intermediate restore correctly rejected newly created non-private parent
directories, which are now explicitly created with private permissions.

The current code still preserves the original `upi.py`, `banking.py`,
`simulation.py` and `gateways.py` bytes. The preservation allowlist was extended
for the clarified scope document, private bank checkpoint adapter and two
existing API/operations guides updated for the new monitoring controls.
An initial regression also correctly detected an old still-loaded console; the
tracked console alone was restarted before fresh qualification. Artifact build
attempts retained the macOS rename and generated-interpreter-alias failures that
were fixed before successful installation. Every full matrix attempt retains
its manifest and logs under `.local/verification-attempts/`; release attempts
retain `.local/qa/releases_*/verification.json`.

See [RELEASES.md](docs/RELEASES.md) for the implemented operator path and the
remaining schema-changing, native-service, Linux/fleet, custody and coordinated
recovery requirements. The running console remains the existing local app.

The additional cases exercise actual stopped gateways/recipients, bounded retry
exhaustion/backoff, provider crash after recipient acceptance, physically
truncated outward acknowledgements, original-ID operator recovery in both
directions, read-only credential refusal, replay/conflict handling, immutable
attempt history, lease fencing and inactive journal restoration. Accepted source
work can prepare during a notice outage, while data release refuses unavailable
authoritative consent. Stale provider ACTIVE state cannot bypass a pause, and a
late approval after revocation cannot reactivate a grant. The role journal
transactionally migrates to version 2; main AA control schema remains version 1.

The running rehearsal upgrade preserved all four private identities and every
original notice payload/identity/attempt count. In the actual browser, an
explicitly staged **synthetic pre-dispatch hold** moved from failed to pending
to delivered under the same notice ID. One retry generation, one real HTTP 200
attempt and exactly one recipient acceptance were observed, with unchanged
native balances. This browser fixture does not claim fabricated historical
network failures; actual outages are separately exercised in the automated
pack. The attempt history rendered correctly and no browser errors were seen.
Evidence: `.local/qa/aa-notice-deployment-upgrade.json`,
`.local/qa/aa-notice-browser-verification.json`,
`.local/qa/aa-notice-browser-delivered.png` and
`.local/qa/aa-notice-browser-attempt.png`.

The encrypted financial-data implementation is integrated in the existing
Banking workspace. Its four actual TLS counterparty processes exercise provider
discovery/challenge linking, signed customer-approved consent, exact native
deposit statements, X25519/HKDF/AES-GCM and FIU-to-browser JWE review. The
independent Node/OpenSSL observer authenticates and decrypts actual generated
payloads and verifies the published fixed key-derivation vector. Negative cases
cover authority, recipient, wire shape, stale timestamps, altered ciphertext,
low-order/expired keys, scope, frequency, pause/revocation, delinking and XML
entities. Actual post-commit response truncation, provider restart, partial-source
failure/resume and inactive private-role recovery execute without changing native
payment balances. A full native restore also observes a fresh UTC timestamp.

Browser verification linked three provider accounts, requested and explicitly
approved synthetic customer consent, completed an encrypted session and decrypted
three statements in the actual browser. Displayed balances matched the native
fixture (250.00, 10000.00 and 250.00 INR); no browser errors were observed.
Evidence: `.local/qa/aa-browser-verification.json` and
`.local/qa/aa-recipient-browser-review.png`. This is manual browser evidence in
addition to the automated suites, not official interoperability certification.

One retained UPI HTTP attempt exceeded message validity during a delayed run.
Its reversal was already authoritative; reconciliation delivered a new
notification generation with the original financial leg identities and states.
The expired attempt remains retained. The unchanged suite then passed all 35
checks. `.local/qa/upi-delayed-acceptance-recovery.json` records that additional
four-check recovery observation. A provenance-only source-preservation failure
was also retained before the explicit registry/script integration allowlist was
updated; the original four financial modules remain byte-identical.

Full AA role/error/timing coverage, additional FI schemas/curves, newer inter-AA
contracts, managed registry/key custody, extended notice-outage recovery and
independent participant acceptance remain unqualified. Existing source
inconsistencies remain visible. Historical native timestamp anomalies are
preserved in `.local/qa/accounting-clock-history.json`, not silently repaired.
See `docs/FINANCIAL_DATA.md` and `docs/PRODUCTION_COMPLETION_MATRIX.md` for the
remaining whole-product requirements.

## What was exercised

Configurable institutions/accounts use actual opening postings. Concurrent duplicate acceptance and processing preserve one financial operation. Debit and credit have independently observed journal identities. Actual TCP response-byte loss leaves financial outcomes unknown until original effects are confirmed. Definitive beneficiary rejection reverses suspense; uncertainty does not invent a refund. Partial refunds reserve the original amount and reject over-refund.

Mandate approval, scheduling, occurrence deduplication and revocation; dispute transitions; balanced settlement positions and retained reconciliation breaks; consent approval, scope, expiry/revocation and native-ledger data access; and isolated banking scenario/evidence creation were exercised. Accounting pagination includes 111 test account addresses and all 106 distinct journal references beyond the first page.

The source-built product service processed client creation; an interest-bearing loan product/application/approval/disbursement/repayment; and savings product/application/approval/activation/deposit/withdrawal. Repayment checks preserve principal/interest allocation instead of assuming every payment goes entirely to principal. A real savings-deposit response was dropped after commit; reconciliation using the original native idempotency key recovered success without a second deposit. A changed service binding was refused.

Confirmed payments reached the actual monitoring ingestion, event routing, rule/typology and evaluation pipeline. The adapter retrieved matching native reports, retained rule versions, verified environment scope and signed the observed evidence. Response bytes were dropped for both analysis ingestions; native inbox observations recovered both acknowledgements without new message identities. Local threshold-policy decisions are separate from this native monitoring pipeline.

The provider contract service imported a scoped OpenAPI example and returned the expected mock response. Executable extensions/external references were refused. An HTTP receiver independently verified callback HMAC/timestamp/event identity; its actual HTTP 204 acknowledgement was retained. Read-only/revoked service-key and cross-environment access checks passed.

OIDC tests used a local RSA/JWKS issuer. They exercised issuer/audience/expiry/workspace/permissions, algorithm confusion/unsigned-token rejection, authorization-code PKCE/nonce verification, scoped sessions, browser-state substitution/replay rejection and remote plaintext configuration rejection. A real institutional identity-provider deployment has not been exercised.

## Restore scope

Control and accounting archives were restored into separate databases. A separately started accounting engine returned the original selected environment's balances and journal. Control record counts, audit chains and retained evidence signatures matched the backup. Roles and private recovery configuration are retained with the current backup; active databases were not replaced.

The additional service check quiesced writers, stopped owned services, archived their persistent volumes and restored isolated database copies. Selected client, loan, loan transaction, savings and savings transaction records matched. Monitoring configuration, raw histories, evaluations and event-history records matched. All provider contract collections matched. The message-broker volume was exported; broker replay/consumer recovery and complete institutional failover were not qualified. These are local restores, not measured off-host RPO/RTO or HA acceptance.

## Browser verification

The banking workspace displayed configurable institutions/accounts. A browser-submitted ₹10 synthetic payment reached a succeeded state with distinct confirmed debit/credit journal IDs. Its monitoring report rendered retained evidence with a valid signature. A browser-published recovery plan ran in a fresh ledger, passed six assertions and downloaded evidence whose digest/HMAC were independently verified. Screenshots are under `.local/qa/banking-browser-payment.png` and `.local/qa/banking-browser-monitoring.png`; the scenario screenshot and downloaded evidence are retained in the same QA folder.

Browser testing caught a real form defect: the optional “No external callback” selection was blocked by required-field validation. That field is now optional and the browser payment completed. The report view presents rule results and a review summary, with raw evidence available separately.

## Failed attempts and corrections

Failed checks were retained rather than counted as passes. Relevant failures included: a plain-text contract-import acknowledgement being parsed as JSON; the native contract-detail response wrapper; normalization of singular OpenAPI examples; required native client/legal-form and savings payment-type fields; a repayment assertion that ignored interest allocation; and an initially unreachable monitoring recovery port on the internal-only container network. These boundaries were corrected and their subsequent suites passed.

The first banking source build exhausted its JVM/container budget. A bounded retry succeeded; its build receipt explicitly records that the complete upstream test suite was not run. Native default product credentials were rotated into owner-only local configuration after its password policy was observed.

The native monitoring router dropped its matching map on a cold cache through loop-variable shadowing. A recorded patch against the pinned image corrected the initial lookup, after which the native evaluation report was observed. Original SPDX attribution, patch hashes and image provenance remain under `LICENSES/monitoring/`.

The role-export backup step initially used the wrong database option for its CLI. The option was corrected, then the complete isolated restore check passed. Earlier failed snapshot directories remain under `.local/backups/`. HTTP, product and service-recovery attempts are retained under `.local/verification-attempts/`.

Public contract intake retained original bytes and hashes. The published FIP 2.2.0 document declares 2.1.0 in its metadata; the selected FIP interoperability document fails schema parsing. Both are quarantined. Contract metadata intake is not an implemented AA wire adapter or conformance result.

## Earlier native-engine coverage and remaining limits

The initial build's native accounting/fault tests and selected execution-helper results are retained in `docs/history/initial-verification.md`. Selected execution helpers passed serially; an initial parallel tracing assertion failed. No VM build, boot, isolation or recovery claim follows from those helper tests.

Included source availability is recorded separately from service enablement. The additional contract test packs and monitoring rule-authoring source are not running capabilities. Several monitoring images run under architecture emulation on this Mac; no production performance conclusion follows.

The full core objective still needs complete selected UPI wire/security and specialized profiles, independent bank/AA interoperability acceptance, qualified execution workers and a portable full production deployment. Independent security/dependency assessment, broader sustained capacity/failover, complete service rebinding/recovery, encrypted off-host retention and institutional operational acceptance remain. Shared workload controls and the exact local reference workload are already exercised; they do not establish institutional capacity. The product-to-payment bridge is an optional broader banking extension, not a required blocker for the core UPI/NPCI/RBI-focused sandbox. Local HMAC evidence is relative to the installation's own key, not independent attestation. Read `docs/PRODUCTION_COMPLETION_MATRIX.md` and `.local/release-readiness.json` for the explicit remaining scope.

## Participant and ASGI qualification

Run `scripts/verify_upi.py`, `scripts/verify_upi.py --postgres`, `scripts/verify_upi_http.py` and `scripts/verify_asgi.py` with the private runtime. These use actual financial services, exact independent journal assertions, a real worker SIGKILL, negative authorization/contract cases, virtual expiry, 21-step journey plans, competing worker processes and API restart. Their receipts and attempts retain the application fingerprint and explicit synthetic scope. Existing banking, simulation, identity, service-recovery and restore qualification remains required after changes to shared financial/serving code.

The request server now uses ASGI; the remaining hardened deployment gap concerns the installed TLS, identity, service trust, security review and operational environment. Passing local participant cases does not close official UPI compatibility or institutional acceptance gaps.

## Connection revision qualification

The participant suites now include connection revision replay/conflict, raw-secret and endpoint rejection, scope enforcement, signed configuration checks, refused external activation, pinned adapters during rebinding, signed authorization bindings and database immutability. The retained native runs passed 97 checks per store; the HTTP run passed 35 checks. Existing accounting, banking, simulation, product, recovery, ASGI and identity/store suites passed for the same application fingerprint. Isolated backup restoration includes the three connection tables and their retained signatures.

Browser verification exercised local revision creation, checking and activation; reactivation with an earlier build's check was refused until revalidation. An external draft with an example endpoint and secret reference retained failed requirements and remained inactive. The observation and control readback are retained in `.local/qa/connection-browser-verification.json`, with the requirements screenshot beside it. These checks do not execute an external bank connector, resolve a credential or establish official conformance.

## Synthetic bank cryptography and recovery

Two separate loopback bank processes executed the synthetic bank-leg contract with actual mutually authenticated HTTPS and independent Ed25519 request/response signatures. The current native suite passed 37 checks. It observed TLS 1.3, refused missing/expired client certificates, a foreign trust root and a mismatched hostname, and rejected altered signatures, expired commands, changed replay payloads and cross-environment requests. It also rejected a revoked signing identity even when its TLS certificate remained valid.

The financial journey presented a merchant collect request, committed separate remitter debit and beneficiary credit, deliberately lost the actual HTTPS credit response after commit, rotated the signing identity and recovered the original credit without a second financial effect. Eight concurrent authenticated financial replays produced one native posting. A partial refund reversed the bank roles; insufficient funds and a definitive beneficiary rejection exercised decline and compensating reversal. A stopped beneficiary left the accepted payment uncertain until restoration of its private identity and receipt checkpoint allowed recovery. Exact native postings, signed transport traces and the workspace audit chain verified.

The control/accounting backup suite passed nine checks, including isolated restoration of two bank worker bundles and four client identities with matching hashes, bank authority bindings, receipt counts and private permissions. This is local component restoration, not a measured off-host disaster-recovery result. All existing participant, HTTP, accounting, banking, simulation, product, service recovery, ASGI and identity/store checks passed for application fingerprint `4eda156b408afa5a37a5a8471ec564bba8d4d7db1c5dfa5d8d12543f3be918dc`; the explicit local readiness profile passed and `production_ready` remains false.

The current browser check selected the retained synthetic-bank environment, opened its active beneficiary connection and executed **Check configuration**. It displayed **Synthetic connection passed over TLSv1.3** and passed the observed mutual-TLS/signed-probe check. The screenshot is `.local/qa/synthetic-bank-tls-browser.jpg`; the observation is `.local/qa/synthetic-bank-browser-verification.json`.

The first native attempt failed because a shared transport-message kind discarded later receipts. Records now use the individual transport request identity; the failed attempt remains retained. These are software-held local test keys, not HSM custody or official UPI PIN/Common Library cryptography. The console/native-accounting loopback links retain their existing configuration; the new transport does not imply encryption of every link or disk. See `docs/SYNTHETIC_BANKS.md` for the executable scope and operating commands.

## Current integrated runtime acceptance — 2026-10-19

For application fingerprint
`dee7d45329152d3fd7f8af1cec977a6074094aaa8a435bd9aa08e10d88718283`,
the deployed synthetic runtime pack passed 70 checks. Two real API processes,
separate worker processes, an HTTPS identity provider and a mutually authenticated
accounting gateway exercised the code/PKCE/JWKS flow, token audience separation,
grant/session revocation, subject/environment roles, same-person checking refusal,
shared quotas, queue backpressure, response deadlines with retained physical
permits, worker failure/restart and private deployment recovery. The runtime
database role completed a native financial payment while table creation/alteration,
schema-version rewriting, audit deletion and temporary-table creation were refused.
Incorrect client/PKCE proof, replayed codes and malformed private recovery content
were refused. Failed attempts remain beside passing receipts.

The existing 18-suite regression matrix passed 853 checks for that same build:
identity/store 9, API/worker 9, original financial/HTTP 38, banking 63 per store and
HTTP 31, simulation 49 per store and HTTP 16, UPI 97 per store and HTTP 35,
synthetic banks 37, lifecycle 107 per store, products 27, native service recovery 9
and full control/accounting restore 10. The restore now compares runtime tables
and stages private deployment files without activating recovered endpoints.
A completed 158-file backup was encrypted and authenticated restoration matched
its manifest. These are local checks, not off-host or HA recovery measurements.

The source-preservation assertion still checks that the original banking,
participant, simulation and accounting gateway modules are unchanged. Shared
serving/identity/persistence and UI registration points have authorized additive
upgrades, covered by the full regression matrix.

Browser acceptance in the existing app opened Engine status → Runtime controls,
created the **Runtime controls acceptance** synthetic environment, saved its
request policy and reopened it to observe persisted values. A malformed subject
grant was refused with an inline error and no grant was created. The current
loopback console remains HTTP/local-operator mode; hardened TLS/SSO deployment
settings are qualified in the private rehearsal rather than silently imposed on
the existing console. Screenshots remain in `.local/qa/runtime-controls-*.png`.

Receipts: `.local/production-runtime-verification.json`,
`.local/acceptance-matrix.json`, `.local/backup-verification.json` and
`.local/fidelity-archive-verification.json`. The explicit local readiness profile
passes; whole-product `production_ready` remains false. See
`docs/DEPLOYMENT_RUNTIME.md` and `docs/PRODUCTION_COMPLETION_MATRIX.md` for the
implemented boundary and outstanding full-scope requirements.
