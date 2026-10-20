# Encrypted financial-data testing in Avenra

Open **Banking workspace → Financial data → AA encrypted data exchange**.
The existing accounts, payments, UPI participants, local consent tools and
scenario library remain available. This adds role-specific data exchange to the
same application and its durable worker; it does not introduce another console.

The installed **Encrypted financial-data rehearsal** environment has two
synthetic providers, two information users and native deposit accounts. Other
environments keep their existing state. Only an operator can install private
participant identities and registered endpoints; tenant input cannot supply an
arbitrary destination or private key.

## What executes

1. A provider discovers the configured synthetic customer's deposit accounts.
   Linking uses a real, bounded provider challenge, persisted attempt limits and
   one-use verification. An authenticated synthetic customer channel supplies
   the fixture token; no real SMS or bank verification is claimed.
2. An information user signs its consent request. Avenra retains the original
   request identity and a pending handle. Explicit synthetic customer approval
   selects linked accounts and produces signed master/provider consent grants.
3. The information user persists its wrapped ephemeral private key before
   dispatch. Avenra validates recipient, consent signature, account ownership,
   time range, frequency, key validity and provider bindings before admitting a
   durable aggregate session.
4. Providers read the native financial journal and balances. Integer paise
   convert exactly to decimal amounts. A journal/volume discrepancy refuses the
   statement. Each document validates against the pinned deposit XML schema;
   absent filtered transactions do not become invented rows.
5. Each provider account uses fresh X25519 material and nonce. HKDF-SHA256 derives
   AES-256-GCM material from the shared secret and XORed nonces. Providers return
   authenticated ciphertext. The aggregator checks the complete encrypted
   account inventory and never decrypts financial information.
6. The information user authenticates, decrypts and schema/scope-checks every
   document. A browser-generated public key receives an RSA-OAEP-256/A256GCM JWE
   review envelope. Its nonextractable private key stays in browser memory;
   plaintext statement review decrypts in the browser.
7. Paused/revoked/expired consents and delinked accounts block reads, including
   replay of a previously successful fetch. Out-of-order state notices resolve
   against current authoritative consent. VIEW retains no recipient result;
   STORE retains a wrapped result under the consent data-life deadline.

Role connections use actual loopback HTTPS with client certificates, registered
API secrets and RSA message signatures. Counterparties are separate trusted
processes with their own journals and wrapping keys. They run under the same
local operating-system account: this does **not** establish OS-level key custody
or VM isolation. The internal gateway's final hop to the current console remains
loopback HTTP in this local deployment.

## Recovery and observability

Consent delivery, provider requests, fetch polling and notices use durable
outbox jobs. A physically truncated response after provider acceptance stays
unconfirmed; recovery reuses the original wire identity and provider session.
Readiness requires every requested account, rather than callback arrival order.
A provider restart retains its accepted sessions and encrypted output.

**Participant deliveries** opens the role's persisted notification journal in
the same console. Independent delivery and data-preparation workers prevent a
notification outage from blocking accepted preparation work. Each notice keeps
its original identity and immutable payload; dispatch refreshes only its
transport timestamp. Defaults are 12 automatic attempts, exponential backoff
with jitter capped at 60 seconds, an 8-second request deadline and a 30-second
fenced lease. The private operator profile can select bounded limits; the
acceptance deployment uses shorter explicit limits to exercise actual outages.

Exhausted or permanently refused notices remain visible with their attempt
history. **Retry original notice** starts an attributed, idempotent recovery
generation. A process crash after recipient acceptance preserves an unconfirmed
attempt; lease recovery reuses the original notice, and recipient replay
protection prevents duplicate acceptance. Late worker results are recorded
separately and cannot overwrite a newer lease or a closed attempt.

The application's own consent/notification jobs also use bounded backoff and
persisted attempt budgets. **Application delivery observations → Retry original
delivery** requeues the original outbox record, request body and participant
binding. The previous error, attempt budget and authenticated actor remain in
immutable audit evidence. Duplicate operator requests reuse their first receipt.
Data-session failures continue to use session recovery, rather than this notice
retry endpoint. Data requests with uncertain provider outcomes retain their
original identities and retry until their session/key deadline.

Provider grants and financial-data release recheck the current authoritative
consent. Unavailable authority refuses release temporarily. A stale local ACTIVE
state cannot override a customer pause; original approval delivery after a revoke
cannot reactivate the provider grant. This is the selected synthetic profile's
explicit authorization policy, not a claim that every official timing rule has
been reproduced.

Counterparty journal schema 2 adds scheduling, immutable attempts/retries and
late-result observations. Its transactional legacy migration preserves original
notices and attempt counts and exposes previously exhausted work. The main
control financial-data schema remains version 1. Recovery snapshots include the
new role tables; migrated journals require compatible software to run again.

An explicit **Resume original session** action retries failed source legs while
preserving the aggregate session, recipient key, completed sibling sessions and
original failed provider attempts. A changed private participant binding refuses
recovery until its original configuration is restored. Expired keys need a new
authorized session, rather than silent key replacement.

Full backups include consistent SQLite checkpoints of role journals, private
configuration, wrapping material and TLS identities. Recovery verifies every
file before creating inactive staging. It does not activate old endpoint
addresses or prove coherent whole-system/off-host disaster recovery.
The database snapshot also retains the effective session timezone and applies
it to each isolated restored database. Acceptance observes a new native write
after restoration, as well as comparing the original journal and balances.

## Repeatable installation and tests

Use the project's private runtime, with the existing native accounting and
control services running:

```sh
.local/runtime/bin/python scripts/install_aa_contracts.py
.local/runtime/bin/python scripts/verify_aa.py
.local/runtime/bin/python scripts/verify_aa.py --postgres
.local/runtime/bin/python scripts/aa_demo.py install
```

The installer accepts only the selected document bytes. A changed upstream file
requires explicit version review. A retained rehearsal uses `aa_demo.py start`,
`status` or `stop`; install refuses to replace it. The operator installation and
synthetic counterparties stay on loopback. No live bank connector is enabled.

The acceptance pack runs real HTTP/mTLS role processes, native accounting,
SQLite/PostgreSQL admission, independently written Node/OpenSSL decryption and
the published fixed crypto vector. It covers altered identities/messages/data,
recipient separation, key/consent expiry, concurrent one-time admission,
post-commit response loss, process restart, failed-leg recovery, challenge
lockout, empty filters, encrypted retention and inactive recovery integrity.
Source and case-pack hashes bind retained receipts. The upstream certification
suite has **not** been executed or claimed as passed. The browser journey is
also observed separately in the current console.

## Supported contract scope and unresolved requirements

The immutable provenance is in `LICENSES/financial-data-profile.json` and
`LICENSES/protocol-profiles.json`. The selected synthetic profile uses AA 2.1.0,
advertised FIP 2.2.0, FIU 2.0.0 and deposit schema 2.0.0 with X25519. The FIP
document itself declares 2.1.0; Avenra retains that discrepancy. Public error
schema inconsistencies and the malformed FIP interoperability document also
remain unresolved. Stricter local shape validation and bounded synthetic
timing/error rules are not evidence that those official inconsistencies have
been resolved.

Remaining work includes complete selected-role/error/timing requirements,
other FI schemas and encryption profiles, newer inter-AA contracts, managed
participant registry/key rotation/custody, independent implementations and
accepted participant test cases. Independent Node decryption establishes the
tested cryptographic construction, not full official interoperability.
Declared notice paths now exercise both directions of actual outage/restart,
retry exhaustion, recipient-commit crashes, physical acknowledgement truncation,
late approvals/revokes, lease fencing and inactive restoration. Sustained mixed
traffic, larger backlogs, all supported outage permutations, actionable alerts
and complete institutional operational acceptance remain to qualify.

The current implementation does not establish a finished institutional sandbox,
RBI admission, NPCI certification, bank UAT acceptance or authority for live
payments. The full original objective remains tracked in
`PRODUCTION_COMPLETION_MATRIX.md`.

## Native journal clock policy

The owned accounting database and its accounting-role sessions now default to
UTC, matching its zone-less journal columns. A real native posting qualifies
the policy against the current UTC clock. Previously stored anomalous journal
timestamps were preserved, along with their transaction identities and amounts;
they were not silently rewritten. Historic statement/date assertions must use
that retained anomaly evidence rather than treating old entries as repaired.
