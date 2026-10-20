# Integrated UPI lifecycle upgrade

This upgrade adds functionality inside the existing Avenra application at
`http://127.0.0.1:4827/`. It uses the existing UI, scoped API, control database,
workers, accounting engine, participant topology, adapter snapshots and financial
recovery. It does not create a second application. Existing financial domain
modules and engine sources are preserved.

Open **Banking workspace → UPI participants → Lifecycle workflows**. The original
payment, ledger, scenario, connection and evidence controls remain available.
The lifecycle profile is an additional environment setting. Existing environments
remain on their original behaviour until enabled. Already prepared intents retain
their previous authorization/recovery path even if the profile is enabled later.

## Executable additions

| Capability | Implemented behaviour |
| --- | --- |
| Account discovery | A proved active device sees only its customer's active accounts. Discovery cannot enumerate other customers. |
| Synthetic credential setup/reset | Explicit simulated issuer verification, scoped expiring challenges, attempt limits, one-time completion and superseded-reset rejection. PIN verifiers use independently salted scrypt hashes. Raw PINs and device proofs are excluded from metadata and evidence. |
| Credential enforcement | Wrong attempts persist, lockout/cooling use the business clock, approval binds the exact intent and credential epoch, and direct core authorization cannot bypass an enabled lifecycle policy. Reset before a first debit invalidates that unattempted approval. |
| Transaction budgets | Per-payment amount and daily amount/count policies. Atomic reservations include pending and uncertain operations. Concurrent retries retain one reservation. Definitive failed/reversed outcomes stop consuming that reservation. |
| Recurring participant payments | Merchant agreement creation, explicit payer approval, pause/resume/revocation, validity, interval, maximum amount and unique occurrences. Each due execution becomes an ordinary signed participant payment with its original financial identities. |
| Notifications and scheduling | Signed local pre-notification/result records, bounded scheduler batches and retry backoff. These are local durable notices; no external SMS or official notification contract is claimed. |
| Mandate recovery | Revocation prevents an unattempted first debit. It does not discard an already uncertain debit/credit or change its recovery identity. Completion waits for financial evidence. |
| Complaints | Original payment association, categories, investigation history, deadline escalation and evidence-backed resolution proposals. A timeout cannot be resolved by inventing a decline or refund. |
| Two-principal approval | A separate authenticated principal checks complaint resolutions and synthetic report releases. Identity derives from the authenticated API credential/session or institutional subject, never a body-supplied actor name. Local service principals exercise the workflow; institutional human-role acceptance remains unqualified. |
| Settlement cycles | Monotonic cutoffs, one allocation per completed lifecycle payment, signed reports with original native leg identities, unresolved records and subsequent allocation of late completions. Positions are independently checked against exact native accounting results. |
| Reconciliation | Caller-supplied independent positions produce retained missing/extra/amount exceptions. Mismatches block release. A later observation invalidates an earlier pending approval proposal. |
| Report export | Versioned JSON evidence and CSV with exact integer-paise amounts. Releases do not transfer funds in an actual settlement account. |
| Qualification inventory | Each declared synthetic requirement links to executed cases and explicit pass/fail/not-run status. Evidence binds source, case-pack and profile hashes. Unknown official requirements and unsupported product variants remain visible. |
| Recovery files | Extension tables participate in the existing full control backup/restore count checks. A separate utility encrypts completed snapshots with AES-256-GCM and verifies authentication before writing restored files. |

The authoritative accounting engine remains the single balance authority. New
`fidelity_*` tables hold policies, credential verifiers, approvals, scheduling,
observations and evidence. They do not maintain a second balance.

## Simulator assumptions

`avenra_fidelity/profile.py` publishes the immutable
`avenra-lifecycle-lab/1` contract. Its six-digit test PIN, 120-second challenge,
three failed attempts, 300-second lock, 60-second reset cooling, fixed-offset
daily budget, complaint deadline and recurring intervals are **Avenra simulator
choices**. They are not represented as current official UPI rules or limits.

The supplied contract does not implement actual SIM/SMS verification, a bank's
credential issuer, official PIN/Common Library processing, HSM custody, current
official UPI wire messages/codes or a bank's settlement file format. Those must
have their own selected version, implementation and independently checked cases.
Current interface names and CSV formats are Avenra formats.

The public [certification FAQ](https://www.npcisupport.org.in/portal/en/kb/certification/3-cz-certification-testing-tool-for-upi-aeps-imps-faqs)
was checked on 18 October 2026: it places TSD downloads on the certification
dashboard and UAT progression with the certification contact. The
[operations FAQ](https://www.npcisupport.org.in/portal/en/kb/upi/upi) identifies
bank-facing operating material. These pages are source provenance for access
routes, not a complete current technical contract. No undisclosed official
behaviour has been inferred or marked conformant.

## API additions

All endpoints remain under the current authenticated workspace/environment path:

`/api/tenants/{tenant}/environments/{environment}/banking/fidelity`

| Method | Suffix | Purpose |
| --- | --- | --- |
| GET | empty / `coverage` | Scoped overview or current-source requirement evidence |
| POST | `profile` | Enable the additional synthetic lifecycle policy with `{}` |
| POST | `discover` | Proved customer account discovery |
| POST | `challenges` | Request a scoped setup/reset challenge |
| POST | `challenges/{id}/complete` | Complete a challenge with a new synthetic PIN |
| POST | `payments/{id}/authorize` | Verify the linked device and synthetic PIN, reserve policy budget, approve/reject the exact existing participant intent |
| POST | `mandates` | Create an immutable recurring agreement body |
| POST | `mandates/{id}/approve`, `pause`, `resume`, `revoke` | Credential-authorized lifecycle actions |
| POST | `mandates/{id}/schedule` | Schedule a bounded unique occurrence |
| POST | `occurrences/{id}/dispatch` | Request due execution; original identities persist |
| POST | `complaints` | Open a scoped complaint |
| GET | `complaints/{id}` | Retained investigation and original financial observations |
| POST | `complaints/{id}/investigate`, `propose` | Investigate or propose an evidence-backed outcome |
| POST | `cycles` | Prepare a signed synthetic cycle |
| GET | `cycles/{id}`, `cycles/{id}/csv` | JSON observation/report or CSV download |
| POST | `cycles/{id}/reconcile`, `propose` | Retain independent positions or propose matched release |
| POST | `proposals/{id}/approve` | A separate authenticated principal checks the unchanged proposal |

The existing authentication, permissions, CSRF, host/origin, strict JSON and body
budgets apply. There is no public endpoint to upload a passing qualification flag.

## Acceptance and demo

Run both native suites with the installation runtime:

```sh
.local/runtime/bin/python scripts/verify_fidelity.py
.local/runtime/bin/python scripts/verify_fidelity.py --postgres
```

They start actual private bank processes over mutual TLS and use the native
financial journal. They exercise restart, actual lost response bytes, duplicate
and concurrent approval, reset/revocation before debit, recovery after revocation,
independent reconciliation, real authenticated HTTP callers and isolated restore.
The result includes source and case-pack hashes. Failed attempts remain under
`.local/verification-attempts/`. Existing acceptance suites remain mandatory.

The optional installer adds a new rehearsal environment **inside the current app**:

```sh
.local/runtime/bin/python scripts/fidelity_demo.py install
.local/runtime/bin/python scripts/fidelity_demo.py status
```

It creates two synthetic bank services, a collect with real response loss, a
partial refund, recurring execution, a resolved complaint and a matched released
synthetic cycle. Temporary demo API credentials are revoked after use. The test
credential values in that installer are synthetic fixture inputs, never suitable
for a bank or shared deployment. Device proofs remain out of the report.

`scripts/fidelity_demo.py stop` stops only the demo's managed bank processes. It
preserves the new environment and evidence. It does not stop the main application
or any previously running synthetic bank services.

## Encrypted recovery artifacts

First produce and verify the normal quiesced recovery snapshot. Do not point the
archiver at a running database's files.

```sh
.local/runtime/bin/python scripts/backup_restore.py --verify-restore
.local/runtime/bin/python scripts/fidelity_archive.py encrypt \
  --source /absolute/path/to/completed/snapshot \
  --destination /absolute/private/path/snapshot.avenra.enc \
  --key-file /absolute/private/path/recovery.key
.local/runtime/bin/python scripts/fidelity_archive.py restore \
  --source /absolute/private/path/snapshot.avenra.enc \
  --destination /absolute/private/path/new-recovery-directory \
  --key-file /absolute/private/path/recovery.key
```

Keep the recovery key separately from the encrypted artifact. Existing archives
and recovery directories are never overwritten. A wrong key or changed ciphertext
fails authentication before any recovery directory is created. Only regular,
bounded, safe paths are extracted with private permissions; restored file hashes
must match the authenticated manifest. A partial extraction is marked unusable.

Extraction restores files, not infrastructure. Recovery configuration can still
reference the original databases: import into explicitly selected clean recovery
databases and configure their new connections before launching services. No
off-host upload, independent custody, measured disaster-recovery objective or
cluster failover follows from successful local encryption/restoration.

## Preserved behaviour and remaining scope

The original financial engine, banking workflows, participant routing and
simulation executor are unchanged. Integration points register the additional
module, API, scheduler and UI controls; qualification hashes now include its
sources. Source snapshots and the preservation check identify those edits.

This is implemented synthetic functionality, not a completed reproduction of
every official participant/version. The visible remaining scopes include the
selected official wire/security contract, independent bank interoperability,
institutional hosting/security/SSO, capacity and HA qualification, off-host
recovery, qualified VM execution, AA interoperability and specialized UPI product
profiles. Supplying credentials alone cannot complete their missing software or
prove equivalence. Existing synthetic adapters remain for regression testing when
qualified official adapters are introduced.

## Observed verification, 18 October 2026

The released local source fingerprint for this upgrade is
`c45131ebcfe2828b50ddb139f0296ad196785dd3a92a5f4fcca2e63a90ae35fa`.

| Verification | Observed result |
| --- | --- |
| Integrated lifecycle, SQLite | 107 checks passed |
| Integrated lifecycle, PostgreSQL | 107 checks passed |
| Existing participant engine | 97 checks passed per control backend |
| Existing participant HTTP | 35 checks passed |
| Separate synthetic banks and cryptography | 37 checks passed |
| Original HTTP/payment scenarios | 38 checks passed |
| Existing banking | 63 checks passed per control backend; 31 HTTP checks |
| Existing multistep simulation | 49 checks passed per control backend; 16 HTTP checks |
| Existing product lifecycle | 27 checks passed |
| Existing service recovery, serving and identity/store | 9 checks/tests passed in each suite |
| Full local control/accounting/private-bank restore | 9 checks passed, including all lifecycle-table counts |
| Actual completed snapshot encryption | 155 files encrypted/restored with matching authenticated manifest |
| Existing application browser | Device enrolment/verification/linking; lifecycle form; complaint creation, investigation and proposal; visible self-approval rejection; authenticated separate-principal approval and resolved readback |

The schema upgrade from lifecycle version one to two was exercised on isolated
restored databases. Profile activation now follows the original audit sequence,
including a test where an older intent and activation have identical wall-clock
timestamps. Older accepted intents retain their original behaviour.

The first discovery check exposed an incorrect metadata join; the first
PostgreSQL installation exposed whitespace before a dialect-converted statement.
Both were corrected. Browser testing exposed an error toast hidden behind the
modal; lifecycle errors are now inline and the larger workflow panel has scoped
layout rules. A demo cleanup audit initially rejected a missing entity identity;
its two temporary API credentials were subsequently revoked and the corrected
cleanup retains a non-null audit identity. Failed attempts and their corrections
are retained, not counted as successful runs.

The local release profile passes for this source. `production_ready` remains
false. These suites overlap and do not represent independent certification or
complete official test coverage. Browser evidence is retained under
`.local/qa/lifecycle-browser-verification.json` and
`.local/qa/lifecycle-integrated-panel.jpg`.
