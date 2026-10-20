# Coordinated application and native recovery

These operator tools recover the existing Avenra application. They do not create
another console, change the payment balance authority or enable a live rail.
The supported activation profile is a cold, managed deployment with local
participants, optional owned mTLS synthetic banks, independent PSP/network
inboxes and AA/FIP/FIU listeners, an explicitly bound owned synthetic HTTPS
issuer/API, and dedicated loopback accounting/fault services. External identity
providers, monitoring and other service bindings still require coordinated
replacement before activation. Combined AA/HTTPS identity recovery remains open.

## Captured state and trust

`scripts/recovery.py capture` requires the actual selected signed application
artifact, a distinct offline migration identity and drained API/worker pools.
The selected release must contain the inactive-startup guard. A merely staged
artifact is not a substitute for the database's selected release. Interrupted
release publication must be recovered first.

Stop the source native accounting service and all of its clients. Capture checks
that its database has no other client backends. The exclusive control release
fence prevents cooperating application nodes from starting. Table write locks
and exported snapshots keep each database dump and its complete row/sequence
inventory on the same retained state. Native accounting must have the qualified
UTC policy. Capture is an offline operation; it does not coordinate arbitrary
nonparticipating writers or other service processes. The relevant database
mechanisms are described in the PostgreSQL 16 documentation for
[table locks](https://www.postgresql.org/docs/16/sql-lock.html),
[snapshot export](https://www.postgresql.org/docs/16/functions-admin.html) and
[pg_dump snapshots](https://www.postgresql.org/docs/16/app-pgdump.html).

Stop all synthetic bank processes too. Current bank launchers hold an exclusive
service lease for their whole lifetime, including request drain. Capture acquires
every bank lease before creating a set and retains them through the checkpoint.
A still-running bank is refused even when the native accounting process has
already stopped. This fences cooperating current launchers; arbitrary older or
nonparticipating processes are not qualified by the existence of a lock file.

Stop every AA ingress, FIP and FIU too. Each cooperating launcher holds its
process lease through shutdown; capture obtains every role lease for the whole
checkpoint. Independent per-file SQLite backups alone do not prove topology
quiescence. Role databases are backed up with committed WAL and explicit close;
runtime leases, process registrations and logs are not restored as source owners.

Stop the owned synthetic identity issuer too. Its provisioner and launcher hold
an installation-wide lease through provisioning and the whole listener lifetime,
including shutdown. Capture holds that lease through the checkpoint and refuses
a serving issuer before creating a destination. Provisioning also takes the
installation configuration lock, so its new identities cannot race capture or
release admission. The current owned profile has one serving issuer per deployment.
The root runtime lease is excluded from recovery payloads.
Nested deployment-secret and recovery copy parents are created one component at
a time with owner-only permissions, independent of the process umask. Existing
linked or non-private parents are refused rather than silently changed.

Copied issuer credentials refuse ordinary startup while staging, and continue
to refuse it after bank/native activation with `RECOVERY_IDENTITY_REQUIRED`.
The explicit owned-issuer recovery path below supplies identity bindings for
recovered sign-in. Signed candidates must declare `issuer_runtime` support whenever owned
issuer configurations are present. A genuine routing-compatible predecessor
without that fence is refused without changing control rows or release selection.
These fences cover cooperating current tools; they do not qualify arbitrary
older or nonparticipating identity-provider processes.

The cold-recovery pack requires a genuine preserved pre-fence release reference
in private `.local/release-baselines/before-issuer-fence.json`. Its fields are
`artifact_directory`, `trust_file` and `runtime_directory`: the independently
signed retained artifact, its external public trust and its verified offline
runtime. That predecessor must support the retained route/participant versions
while lacking `issuer_runtime`. Preserve the actual historical bytes and their
provenance; a current build relabelled as an older release cannot provide this
evidence. Generated artifacts, trust inputs and references stay outside Git.

Independent PSP/network inbox processes also hold the lifetime fence. Capture
retains their private identities, sender registries, versioned credentials and
durable receipts under `independent-participants`. Stop every captured service;
activation requires authenticated replacement routes for all of them. Follow
[the participant recovery guide](PARTICIPANT_RECOVERY.md) after starting the
restored native, bank and AA services and before activation.

The set includes both database dumps, the exact application and supplied offline
wheels, pinned accounting/fault executable bytes, original evidence authority,
private deployment settings, and existing private bank/financial-data file
checkpoints. It records complete user-table row hashes and sequence state.
Original credentials remain private recovery inputs; they are never printed.
Cluster roles/globals are not imported. The target receives new restricted roles
and newly named databases instead.

An Ed25519 recovery signature binds every payload file, database inventory,
selected artifact and exact platform/base-interpreter hash. Verification requires
an independently supplied recovery public key and the independently supplied
application public key. A key carried inside a set never establishes trust.
The keys may be managed separately. The local self-issued keys do not establish
institutional custody.

Inputs must be private regular files without links. The declared set budget is
10,000 files and 20 GiB in total, at most 2 GiB per recovery file. Database
comparison supports at most 4,096 tables and two million rows per table. These
are implementation bounds, not measured capacity guarantees. The existing
authenticated archive tool can encrypt a completed set before transfer.

## Operator sequence

Use private, owner-only parents. Commands below contain placeholders for the
explicitly reviewed source, trust and destination paths. Keep request UUIDs and
attributed operator names for retries. The normal local source launcher remains
available; it is not silently converted into a managed deployment by these tools.

```sh
.local/runtime/bin/python scripts/recovery.py capture \
  --data <managed-source-data> --destination <new-private-set> \
  --artifact <selected-artifact-directory> --release-trust <application-public-key> \
  --signing-key <recovery-private-key> --accounting-dsn <private-accounting-dsn-file> \
  --native-directory <pinned-native-binary-directory> --pg-bin <postgresql-16-bin> \
  --request-id <capture-uuid> --actor <operator>
.local/runtime/bin/python scripts/recovery.py verify \
  --source <completed-set> --recovery-trust <recovery-public-key> \
  --release-trust <application-public-key>
.local/runtime/bin/python scripts/fidelity_archive.py encrypt \
  --source <completed-set> --destination <new-encrypted-archive> \
  --key-file <separately-retained-private-recovery-key>
.local/runtime/bin/python scripts/fidelity_archive.py restore \
  --source <encrypted-archive> --destination <new-decrypted-set> \
  --key-file <original-private-recovery-key>
.local/runtime/bin/python scripts/recovery.py stage \
  --source <verified-or-decrypted-set> --destination <new-private-target> \
  --recovery-trust <recovery-public-key> --release-trust <application-public-key>
.local/runtime/bin/python scripts/recovery.py import-databases \
  --directory <private-target> --admin-dsn <private-target-admin-dsn-file> \
  --pg-bin <postgresql-16-bin> --recovery-trust <recovery-public-key> \
  --release-trust <application-public-key> --request-id <import-uuid> --actor <operator>
.local/runtime/bin/python scripts/recovery.py native-start \
  --directory <private-target> --accounting-port <unused-port> --fault-port <unused-port> \
  --recovery-trust <recovery-public-key> --release-trust <application-public-key>
```

If the set contains synthetic banks, prepare a private JSON object mapping each
captured worker ID to a distinct new unprivileged port. Supply every captured
bank; do not reuse original bank ports or the restored native ports. The worker
IDs are in `payload/synthetic-banks/manifest.json`. Then run:

```sh
.local/runtime/bin/python scripts/recovery.py bank-start \
  --directory <private-target> --ports <private-worker-port-map.json> \
  --recovery-trust <recovery-public-key> --release-trust <application-public-key> \
  --request-id <bank-start-uuid> --actor <operator>
```

The declaration is bounded to 32 banks/64 role authorities and a 60,000-byte
routing document. These are admission bounds, not demonstrated maximum capacity.
The observed rehearsal uses two banks. Local-only sets do not need `bank-start`.
If the set contains AA roles, prepare a private JSON object mapping each
`<environment>/ingress` and `<environment>/<peer-id>` to a distinct new port.
The captured `profile.json` names each peer. Include every captured environment
and listener. Select a new API port too and retain it for the later API command:

```sh
.local/runtime/bin/python scripts/recovery.py aa-start \
  --directory <private-target> --ports <private-aa-port-map.json> \
  --api-origin http://127.0.0.1:<new-api-port> \
  --recovery-trust <recovery-public-key> --release-trust <application-public-key> \
  --request-id <aa-start-uuid> --actor <operator>
```

The AA plan admits at most 32 listeners, 2,000 immutable file pins and a
60,000-byte document. The observed topology has one AA ingress, two FIPs and one
FIU; these admission limits do not establish capacity. Use new unprivileged
ports disjoint from original and recovered native/bank listeners. After all
required native/bank/AA listeners are established, activate:

```sh
.local/runtime/bin/python scripts/recovery.py activate \
  --directory <private-target> --accounting-origin http://127.0.0.1:<accounting-port> \
  --fault-origin http://127.0.0.1:<fault-port> \
  --recovery-trust <recovery-public-key> --release-trust <application-public-key> \
  --request-id <activation-uuid> --actor <operator>
```

Staging validates the entire set before creating a target. It creates an inactive
guard before copying source identities, installs the independently signed
application read-only and rebuilds a new runtime from its supplied locked wheels.
The trusted Avenra operator tooling and its pinned dependencies must be available
before verification/staging. An exact qualified base Python/platform and PostgreSQL tools remain prerequisites;
the set does not contain an entire operating-system image. Startup through App
and the managed launcher refuses a guarded directory before connecting to source
credentials. The older private-deployment file restore now enforces this guard
too, including restorations without a pending release transition.

Imports allocate fresh migration/runtime/native roles and new databases under a
durable, actor/request/target-bound attempt. Each database import is one
transaction. A retry compares every retained row and sequence: an exact completed
database is reused, an empty planned database is imported, and differing nonempty
state is refused. No existing database is dropped or overwritten. A crash after
the first database commit leaves the complete two-database attempt inactive until
the original retry completes. Source names and secrets are not rebound implicitly.

`native-start` launches only the copied, reverified native binaries against the
new accounting database. It uses unused unprivileged loopback ports, private logs
and an authenticated ownership receipt. This host profile requires `ps` and
`lsof` to verify the actual process identity and listening PID. It performs no
automatic native schema upgrade. Activation checks the owned listeners, binary
hashes and imported credential hashes; pointing at an arbitrary healthy original
service is refused.

Activation keeps the same selected release and original financial identities.
It relocates application/runtime paths, installs explicit new engine endpoints
and expires copied process/monitor liveness. It never decides an uncertain
financial outcome. A signed installation receipt precedes removal of the inactive
guard. A crash there replays only the original decision; concurrently changed
bindings retain the guard. External service, SSO/transport and participant
bindings other than the supported owned synthetic banks/AA roles require additional
coordinated replacement and are refused rather than reused against the source.
The check includes retained callback destinations, accepted external commands and
financial-data records even when their settings files are empty. Retained AA
rows must each belong to a captured, recovered tenant/environment. An activated
recovery has no implicit development callback origin; new notification egress
needs an explicitly approved destination policy.

## Recovered bank authority and process ownership

`bank-start` first checks every materialized bank/identity file against the
externally signed source checkpoint. Linked receipt databases, changed keys and
non-private parents are refused before a bank starts. Logical bank endpoints,
connection configuration hashes, request payloads, payment topology, account
scope and financial leg identities are not rewritten.

An installation-authenticated `bank-recovery.json` separately binds the new
physical HTTPS addresses, original role authority hashes, exact original server
certificate/keys, new native accounting connection, recovery set, actor/request
and target directory/database identity. TLS certificate verification and signed
scope checks still apply. A route is not permission for non-loopback egress or
for another bank's authority. Activation pins the exact routing file in the
deployment policy; removing/changing it refuses new application/client startup
instead of falling back to an original endpoint.

Banks run as isolated Python processes from the verified offline application
runtime. The controller observes that runtime's actual OS executable image and
pins its path/hash and command form. This accounts for framework Python's exec
behavior on macOS without accepting an arbitrary Python process. Each child
self-registers its PID, birth/command identity, route hash and directory before
the controller publishes readiness. The controller checks the actual listening
PID and performs real mTLS/signed probes for both role identities.

If an operator dies after a bank starts, an identical attributed retry reuses
the authenticated surviving child and starts the remaining banks. A changed
actor, request, port map or native binding is refused. After activation, the same
original `bank-start` command may restart a dead owned bank while the same
recovery/application remains selected. Bank receipt records and native journal
identities continue forward. Signing-client rotation/revocation retains the
logical authority and is supported; server CA/certificate replacement and wider
service/fleet upgrades require separate qualification.

After accepted activation, use the existing managed entry points:

```sh
.local/runtime/bin/python scripts/releases.py run --data <private-target>/data \
  --trust <application-public-key> --mode api --port <unused-api-port>
.local/runtime/bin/python scripts/releases.py run --data <private-target>/data \
  --trust <application-public-key> --mode worker
.local/runtime/bin/python scripts/recovery.py bank-stop --directory <private-target>
.local/runtime/bin/python scripts/recovery.py aa-stop --directory <private-target>
.local/runtime/bin/python scripts/recovery.py native-stop --directory <private-target>
```

Stop/drain the API and financial workers before stopping banks/native dependencies.
`bank-stop` uses authenticated self-registration and exact directory/runtime/
executable/process identity. It refuses an unrelated or reused PID.
`native-stop` verifies its receipt and each directory-bound original process
identity. It refuses a changed/reused PID and never stops unrelated services.
Retain failed sets, partial target directories and attempt logs for review.

## Recovered financial-data authority

`aa-start` verifies the signed checkpoint, imported target identities, offline
application/runtime and every staged participant byte before first startup.
Its authenticated `aa-recovery.json` separates physical HTTPS destinations from
the original profiles, peer configurations, signing/wrapping keys and TLS
authorities. Accepted AA job, consent, session, request and notice identities
are retained. Original `process-spec.json` paths are preserved as historical
input; the controller never executes their original arguments or source paths.

Both directions retain mTLS, API-key and JWS checks. Clients resolve the new
address against their complete original configuration and pin the captured TLS
leaf certificate. FIP statements use the newly owned accounting service.
Original registries and consent binding hashes remain unchanged. A missing or
changed activated route prevents startup/dispatch; there is no source fallback.

Copied roles run from the verified isolated runtime, self-register their exact
process identity and expose owned listeners. Readiness checks use real mTLS and
signed role replies; ingress checks its original TLS authority. An interrupted
operator retry reuses surviving authenticated processes. Changed actors, request
identities, ports, target databases or API upstreams cannot reuse that intent.
`aa-stop` refuses reused PIDs; an identical `aa-start` can restart a dead owned
role after activation while the original recovery/application remains selected.

Participant processing, notice retries and expiry cleanup stay paused during
staging and interrupted activation. After activation, recovered background work
also waits for a signed heartbeat through the registered replacement ingress
to the AA API. Start the managed API on the exact `--api-origin` port, then its
financial worker. An unsigned ingress error cannot establish an authoritative
participant rejection and remains an unconfirmed transport result. Missing or
altered local routing also leaves an accepted AA job unconfirmed with its original
identity, payload and binding; it is not recorded as an authoritative peer refusal. Authorization
and session expiry dates are never extended by recovery.

An activated recovered deployment can itself be cold-captured and restored.
Only authenticated new-target route files contribute physical capability pins;
copied prior-recovery pins are not mistaken for new bindings. The rehearsal
performs two successive relocations and independently decrypts the original
data session after both, while earlier paths and listeners remain unavailable.

## Evidence and remaining scope

Run `.local/runtime/bin/python scripts/verify_coordinated_recovery.py`.
The rehearsal uses a dedicated native source database/process, actual participant
payment through two actual mTLS bank processes, physically lost response, cold capture, separate recovery/application
trust, real archive encryption, fresh database roles/imports and rebuilt native
and application processes. Original installation paths become unavailable before
activation. It verifies exact balances/journal, original-ID uncertainty recovery,
valid retained evidence, a new post-restore payment and unchanged source database.
Operator processes actually die after the first database commit, after the first
bank registers, and after activation publication. The original bank addresses
are occupied by observed refusal listeners; no recovered traffic reaches them.
An uncertain original bank credit recovers once after address replacement and
client-key rotation, with unchanged accepted connection/payment bindings. A
bank is actually killed after activation and original-ID recovery continues
after its attributed restart. Exact source file hashes and complete receipt rows
are compared before/after, separately from SQLite backup page layouts.
The actual preserved signed predecessor is refused before release selection
because it lacks the required declared binding capabilities. Every restored
control row/sequence and the selected release remain unchanged by that refusal.
Negative cases cover trust, modified bytes, symbolic/hard links,
inactive startup, wrong actors, substituted source credentials, arbitrary native
endpoints, changed bindings and runtime database privileges.

Run `.local/runtime/bin/python scripts/verify_aa_recovery.py` for the AA topology.
It generates actual signed consents and encrypted native statements, loses a
provider reply after commit, and kills a provider after the AA accepts a notice.
Cold capture, encrypted transfer, new roles/databases, owned replacement
listeners and actual operator death precede original-ID recovery through the
same application. Independent Node/OpenSSL decryption, original expiry,
bidirectional notice delivery, active provider SIGKILL/restart, a second full
recovery generation, source byte/row preservation and observed earlier-listener
traffic checks are required. A real signed predecessor without AA routing
capability is refused. Receipts are retained even when a case fails.

Receipts bind application and case-pack hashes. The full acceptance matrix and
local release gate require this receipt in addition to the existing full backup
and deployed-runtime checks. Reported phase durations are observations of this
exact cold local rehearsal. They do not establish institutional RPO/RTO.

Whole-service topology recovery remains open: standalone operator bootstrap,
external SSO, combined AA/HTTPS identity and other service rebinding, remaining AA profiles and key/registry
lifecycle, monitoring/product/provider releases, broker replay,
off-host delivery/retention, cluster/host restoration, native upgrades and
independent operational acceptance. The full production objective remains open.

The selected signed application must explicitly declare its configured engine, bank and AA
routing versions in `avenra/deployment_capabilities.py`. Selection, pending-release
recovery, managed startup, bank startup and recovery activation check the
applicable declarations. The operator parses bounded literals without importing
a candidate module. A legacy build without the necessary declarations cannot be
selected merely because its database schema matches; ordinary deployments that
do not configure those bindings retain their existing compatible release path.

The opt-in XML participant profiles use this same recovery path. Run
`python scripts/verify_coordinated_recovery.py --xml` for their separate case
pack mode and `.local/coordinated-upi-wire-recovery-verification.json` receipt.
The rehearsal preserves the original signed envelopes and validation proofs,
replaces physical bank addresses and confirms the original uncertain credit
once. It also attempts a genuine signed older build that supports the physical
binding formats but lacks the retained XML contract declarations. That release
is refused without changing any control row, sequence or release pointer.
Both legacy and XML modes are mandatory local gates; their receipts record the
mode and cannot substitute for each other.

An explicitly labelled, signed current negative candidate separately retains
every route and issuer-fence declaration while omitting the retained XML
contracts. Its real release-selection refusal isolates the contract gate from
the new issuer guard; it is never presented as a historical predecessor.

## Independent PSP and network recovery

Run `.local/runtime/bin/python scripts/verify_coordinated_recovery.py --independent`
for the separate `avenra-cold-independent-recovery/1` profile. It exercises five
owned mTLS inbox processes, authenticated replacement addresses, unchanged
logical authorities, original signed receipts, key revocation and rotation,
bank/network process loss and recovery, and two complete cold generations.
The inboxes validate messages; financial postings remain authoritative in the
accounting engine. There is no fallback to an embedded receiver when a required
independent participant is unavailable.

The latest receipt is `.local/coordinated-peer-recovery-verification.json`.
It is a mandatory local release-readiness gate alongside the legacy and XML-only
profiles. Reports declare their exact qualification, protocol and participant
modes. Readiness requires the current application and case-pack hashes, passing
individual assertions, required cold-recovery observations and completed timing
metadata. The independent profile also requires two distinct recovery-set IDs.

Every summary links by SHA-256 to its full retained `verification.json` in the
private QA case. A modified, missing, externally located or linked record cannot
satisfy the gate. A top-level passing flag cannot override failed assertions or
a different retained attempt. These checks establish the integrity and scope of
local engineering records; they do not constitute independent attestation,
official UPI compatibility or institutional recovery acceptance.

These large rehearsals now use the shared storage admission described in
[Qualification storage](QUALIFICATION_STORAGE.md). Storage refusal does not
remove earlier case evidence or start a partial database/runtime rehearsal.

## Owned synthetic HTTPS identity recovery

The `avenra-synthetic-issuer-recovery/1` contract replaces the physical issuer
and API addresses while preserving the original logical issuer, subjects,
registered client/audience, private signing/TLS authority, passwords and session
policy. Original `identity.json` and `issuer.json` bytes stay unchanged. An
installation-authenticated route binds the captured immutable files, recovery
set, selected signed artifact and every newly imported database credential.
The captured API TLS and granular-authorization settings cannot be downgraded
to bypass activation. External identity providers are not admitted by this profile.

After native and captured bank services start, run:

```sh
.local/runtime/bin/python scripts/recovery.py identity-start \
  --directory <recovery-directory> --issuer-port <new-issuer-port> \
  --api-origin https://127.0.0.1:<new-api-port> \
  --recovery-trust <external-recovery-public-key> \
  --release-trust <external-release-public-key> \
  --request-id <identity-start-uuid> --actor <operator>
```

Both ports must be explicit, unprivileged, distinct and absent from all preceding
issuer/API origins and the recovered service listeners. Retry the exact same
ports, actor and request. The verified offline runtime self-registers its process
birth identity and executable; the controller checks actual listener ownership,
TLS certificate pin, JWKS and a fresh challenge signed by the original key.
Operator death after startup can reuse that surviving authenticated process.
Use `identity-stop --directory <recovery-directory>` to drain that owned issuer.
An identical `identity-start` can restart it after actual process death.

Before activation, the issuer serves bounded JWKS/challenge probes and refuses
authorization, login and token issuance. After activation, fresh code/S256 PKCE,
state and nonce flows use the approved replacement HTTPS endpoints. Token/JWKS
transport validates the captured CA, hostname and exact leaf **before sending
client credentials**. It does not follow redirects or fall back to old addresses.
The API launcher accepts only the registered recovered callback origin. Missing
or changed routes, database pins, immutable identity files or policy refuse use.

The logical `iss` value stays unchanged, so `issuer + sub`, approval identity,
existing session identities and expiry limits survive. Old interaction/code
redirect bindings are refused; they are never rewritten or extended. Explicit
subject/environment grants, revocation and same-person checking continue to
apply after fresh sign-in. Signed candidate admission, recovery and launch also
require `issuer_routes`; schema compatibility alone cannot admit older code.

This is an explicit **static synthetic binding**. The replacement origin refuses
`/.well-known/openid-configuration`; it does not pretend to dynamically discover
the original logical issuer. Stable-origin discovery, DNS/TLS failover, external
IdP coordination and institutional acceptance remain open. See
[OIDC Core issuer validation](https://openid.net/specs/openid-connect-core-1_0.html#IDTokenValidation)
and [Discovery validation](https://openid.net/specs/openid-connect-discovery-1_0.html#ProviderConfigurationValidation).

Run `.local/runtime/bin/python scripts/verify_identity_recovery.py` for the
mandatory `avenra-cold-identity-recovery/1` gate and
`.local/identity-coordinated-recovery-verification.json` receipt. The pack uses
real native services, mTLS XML banks and HTTPS identity/API processes, full
database row/sequence comparisons, operator/issuer death, original grants and
sessions, a genuine routing-incompatible signed predecessor, and two cold
generations with preceding paths/listeners unavailable. A wrong leaf signed by
the original CA must receive no confidential request bytes. Both recovery-set
IDs, current application/case-pack hashes, required observations and the intact
private case record are mandatory. Preserve a genuine pre-route signed release
in `.local/release-baselines/before-issuer-routes.json` using the same three
artifact/trust/runtime fields as the issuer-fence baseline.
