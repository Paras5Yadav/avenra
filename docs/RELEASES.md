# Controlled application releases

These tools deploy the same Avenra console, APIs and durable worker from a signed
application artifact. Financial state and private deployment identities remain
outside the artifact. Lending/payment bridging is an optional extension, outside
the core UPI/NPCI/RBI-focused production objective.

## Artifact and trust

`scripts/releases.py build` packages owned application code, static assets,
operation/verification scripts, documentation, required notices and a supplied
offline wheel set. It locks **every supplied distribution**, including transitive
dependencies, to an exact version and SHA-256. Installation additionally checks
the original requirements and extras and runs `pip check`. It performs no package
index lookup or source build during runtime preparation.

The canonical manifest binds the complete file inventory, application fingerprint,
exact CPython patch/platform target, dependency metadata, current schema inventory
and external accounting/fault binary hashes. Those binaries are separately
deployed dependencies; the artifact does not install or upgrade them. The
manifest has an Ed25519 signature. The trust key is supplied outside the archive.
A key included in an archive never establishes trust.

For identical source bytes, supplied wheels, external native-binary inputs,
target, release label and signing key, archive output is deterministic. This is
reproducible **packaging of those inputs**. Rebuilding native engines, Python or
third-party wheels from source reproducibly remains separate work.

No recursive installation directory is selected. Database passwords, TLS keys,
issuer state, financial records and local evidence are excluded. Required
upstream notices remain in `LICENSES/`; dependency wheels retain their own
licence files. Licence/legal review of a distributable release remains required.

Signature, inventory, target and path checks happen before extraction. Traversal,
links, duplicate/extra/missing entries and changed payloads are refused. Installed
payloads are read-only and fully rechecked before selection and managed launch.
Runtime preparation uses a new private virtual environment. Its file hashes and
resolved interpreter hashes are retained and checked before selection/launch.
The launcher uses isolated Python imports and disables bytecode writes. Caller
working directories and user import paths cannot substitute application code.

## Schema-preserving activation and rollback

Managed deployments require the existing new-database installer and its distinct
PostgreSQL migration/runtime roles. The migration identity is used by the
offline release operator only. API/worker processes continue using `control.dsn`.

The current schema inventory is control 9, lifecycle 2, runtime 1, AA 1 and
operations 1. The preserved predecessor inventory without operations is also
recognized for compatible code rollback. Ordinary activation does not migrate
schemas. It refuses an absent required schema, an unknown inventory or a changed
operations checksum; it never downgrades or restores an earlier financial
database. One reviewed additive migration is provided below. General schema
evolution still needs reviewed compatibility plans and its own qualification.

Runtime pool connections in this build hold a shared PostgreSQL advisory lock.
An activation needs the exclusive lock, so all managed API/worker processes must
be drained and stopped. Opening a replacement connection rechecks the shared
database's selected release. An absent or stale deployment selection is refused.
An installation using older code without this fence must first be quiesced and
onboarded under this build; the lock is a cooperating application boundary,
not a mechanism that fences arbitrary nonparticipating software.

Activation compares the expected current artifact, validates the target/runtime
and schema inventory, and commits the selected artifact plus an immutable,
attributed, request-identified history row in one transaction. Runtime roles have
read access only to release state. A replay returns its original generation; a
changed payload or a superseded request cannot start a new transition.

A durable private pending record precedes the database commit. The database is
authoritative. If the operator dies before commit, recovery preserves the prior
release and retains the aborted attempt. If it dies after commit, recovery
publishes that committed artifact and configuration without repeating financial
operations. Pending recovery and database/filesystem disagreement prevent
managed startup. Concurrent deployment-configuration edits are refused rather
than overwritten.

Private deployment recovery includes the selected-release and pending-transition
records. They restore with an enforced inactive startup guard and their original
contents. The [coordinated recovery tools](COORDINATED_RECOVERY.md) additionally
capture the selected application, offline wheels, pinned native binaries and both
database inventories. They rebuild the selected local/TLS synthetic participant
profile under new database identities and explicit service bindings. Captured
banks restart from the verified offline application with authenticated new
physical addresses while original logical bindings remain unchanged. The signed
bundle now also contains the root API and operator guides. Standalone operator
bootstrap, SSO/other-provider service rebinding, host/cluster recovery and fleet disaster recovery remain
open; the private file checkpoint alone does not establish them.

Rollback selects a previously committed compatible artifact. Financial history,
accepted requests, original message/effect identities, notices and evidence move
forward. Committed payments do not disappear because code is rolled back.
Uncertain outcomes still reconcile under their original native identity.

Explicit engine/recovered-bank bindings also require matching version declarations
from the signed candidate's `avenra/deployment_capabilities.py`. These bounded
declarations are inspected as literal data without executing candidate code.
Selection, interrupted-transition recovery and managed launch refuse missing or
unsupported capabilities. This prevents selecting an older schema-compatible
build that would ignore the configured physical routing or fail on the recovered
deployment policy. The refusal precedes release-catalog mutation. Recovered bank
service activation applies the same check. Normal deployments without those
binding policies retain the previously qualified compatible rollback behavior.

Every retained participant connection revision also contributes its exact
adapter/contract-hash requirement, even after it becomes inactive. New XML
contracts are declared under `participant_profiles` in the same signed literal.
Selection refuses a candidate lacking any retained contract before changing
release state. Recovery publication, launch and runtime database admission check
the same requirement. The byte-pinned original participant contracts retain
their existing compatible upgrade/rollback path; reusing an old adapter name
with a changed hash does not obtain that exemption.

## Operator commands

Run these from the repository root using the private runtime. Paths below are
examples of new private directories. `expected` is an artifact manifest hash,
not a release label or source fingerprint. A transition needs a fresh UUID and
an attributed operator name. Inspect current state before choosing `expected`.

```sh
mkdir -p .local/releases/operator .local/releases/wheels
.local/runtime/bin/python scripts/releases.py new-key \
  --private .local/releases/operator/signing.raw \
  --public .local/releases/operator/trust.raw
.local/runtime/bin/python -m pip download --only-binary=:all: \
  --requirement requirements.txt --dest .local/releases/wheels
.local/runtime/bin/python scripts/releases.py build \
  --wheels .local/releases/wheels --key .local/releases/operator/signing.raw \
  --label <release-label> --output .local/releases/<release-label>.tar.gz
.local/runtime/bin/python scripts/releases.py inspect \
  --archive .local/releases/<release-label>.tar.gz \
  --trust .local/releases/operator/trust.raw
.local/runtime/bin/python scripts/releases.py install \
  --archive .local/releases/<release-label>.tar.gz \
  --trust .local/releases/operator/trust.raw --store .local/releases/artifacts
.local/runtime/bin/python scripts/releases.py prepare-runtime \
  --artifact .local/releases/artifacts/<artifact-id> \
  --trust .local/releases/operator/trust.raw --runtime .local/releases/runtime-<artifact-id>
.local/runtime/bin/python scripts/releases.py status --data <deployment-data>
```

After draining all API/worker nodes for that deployment:

```sh
.local/runtime/bin/python scripts/releases.py activate \
  --data <deployment-data> --artifact .local/releases/artifacts/<artifact-id> \
  --trust .local/releases/operator/trust.raw --runtime .local/releases/runtime-<artifact-id> \
  --expected <current-artifact-id-or-none> --request-id <uuid> --actor <operator>
.local/runtime/bin/python scripts/releases.py run \
  --data <deployment-data> --trust .local/releases/operator/trust.raw --mode api --port <port>
.local/runtime/bin/python scripts/releases.py run \
  --data <deployment-data> --trust .local/releases/operator/trust.raw --mode worker
```

Use `rollback` with the same fields to select a previously committed artifact;
use `recover --data ... --trust ...` for a retained interrupted transition.
No command prints passwords, TLS private material or signing keys. Keep the
signing key separate from serving nodes and retain public trust out of band.
Synthetic local signing is not institutional key custody.

## Additive operations migration

For an existing managed predecessor deployment, drain and stop all API/worker
nodes, retain a completed backup, verify the target artifact and run:

```sh
.local/runtime/bin/python scripts/releases.py upgrade-operations \
  --data <deployment-data> --artifact .local/releases/artifacts/<artifact-id> \
  --trust .local/releases/operator/trust.raw \
  --expected <current-artifact-id> --request-id <uuid> --actor <operator>
```

This command applies only the reviewed operations-schema recipe whose checksum
matches the signed artifact. It cannot execute uploaded SQL. The offline
migration identity takes the existing exclusive database release fence. Live
nodes, stale release expectations and interrupted release publication are
refused. DDL, installation history and restricted runtime grants commit together.
An exact request/actor replay is idempotent; changed content is rejected.

Migration leaves core financial rows and the selected release unchanged. After
success, use ordinary activation for the new artifact. Compatible old code can
be selected again while the additional operations tables remain retained; it
does not offer the new monitoring capability. There is no destructive downgrade.
New databases install the current schema directly. The existing local operator
installation retains its explicit local migration behavior; restricted serving
identities cannot install a missing schema.

`scripts/verify_operations_upgrade.py` runs an actual preserved signed predecessor,
completes a native payment, kills an operator inside the real DDL transaction,
checks rollback/retry, activates new code, and rolls back old code while retaining
the same native payment, balances and operational tables. The baseline artifact,
external public trust and its prepared offline runtime must be preserved in the
private `.local/release-baselines/before-operations.json` fixture. A current build
cannot be relabelled as a predecessor to supply this evidence.

## Qualification and remaining deployment work

`scripts/verify_releases.py` builds byte-identical archives, rejects tampering,
prepares two offline runtimes and installs a fresh restricted deployment. It
runs actual packaged API/worker processes, completes participant payments,
carries accepted work into another source build, refuses stale nodes and
incompatible schemas, and rolls code back around a physically lost accounting
response. Separate operator processes are killed at real preparation/commit
boundaries and recovered. Failed attempts are retained under `.local/qa/`;
the latest receipt is `.local/release-verification.json`.

SQLite bank, issuer and AA role checkpoints now use a shared standalone backup
primitive. It captures committed WAL contents through SQLite's backup API,
switches only the **copied** database to standalone journal mode, verifies its
structure, closes both connections explicitly and synchronizes the file. It
retains no transient WAL/shared-memory dependency. The original source stays in
its existing mode and can retain an uncommitted transaction. Bank restoration
validates private file hashes and paths before materialization and verifies
receipt counts through immutable read-only connections. The separate
`scripts/verify_checkpoints.py` pack exercises those actual WAL/writer boundaries;
native bank/issuer/AA integration and full restoration remain separate evidence.

Qualification is for the observed exact Mac/CPython target. Linux packaging,
signed container/native-engine releases, actual multi-node upgrade orchestration,
general schema-changing migrations beyond the closed operations recipe,
supply-chain/security review, fleet rollback,
availability objectives and institutional operational acceptance remain open.
The normal current-app launcher is preserved; it is not silently switched to a
qualified release artifact by creating a rehearsal. This module adds the
operator release path to the existing product and enables no live payment rail.

Implementation references: [pip secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/)
and [PostgreSQL advisory locks](https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS).
