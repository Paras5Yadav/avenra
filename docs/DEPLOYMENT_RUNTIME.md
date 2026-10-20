# Runtime upgrade in the existing Avenra product

The current console, workspace/environment API, accounting, participant engine
and durable worker remain the product. Engine status → Runtime controls opens
the additional controls in that console. It is not another financial app.

The local operator installation retains its existing access and financial
behaviour. Publishing an environment request policy is an explicit operation.
Institutional mode and private deployment configuration enable stronger access
rules for a deployment; no configuration flag establishes qualification.

## What executes

- A private synthetic HTTPS OIDC issuer exercises confidential authorization
  code flow, S256 PKCE, browser state/nonce, one-use codes, signed tokens, issuer,
  audience and JWKS checks. Four test roles model institutional users. It is a
  trusted-code test counterparty and does not hold financial balances.
- Sessions preserve the authenticated subject across sign-ins. A second session
  of the same person cannot check that person's own financial proposal. Logout
  revokes the local session; subject grants can expire or be revoked.
- Granular permissions distinguish participant payment creation, authorization,
  recovery, adjustments, fixture management, raw banking simulation, mandates,
  cases, checking, settlement and runtime administration. Explicit environment
  grants also restrict catalogue discovery. Unknown writes fail closed under
  granular enforcement. Local operator access remains trusted.
- Signed immutable environment policy versions govern shared token buckets,
  concurrent requests and pending-work admission through the control database.
  The policy is shared across API nodes; it is not a process-local counter.
  Queue saturation preserves original status/recovery access. Other request and
  authorization limits still apply to recovery requests.
- Dispatch deadlines return an uncertain HTTP outcome. A dispatch thread keeps
  its physical concurrency permit until it actually finishes. Reconciliation
  continues to use the original financial identity. Eventual domain outcomes
  and response deadlines are recorded separately.
- `/health/live` is a lightweight process signal. `/health/ready` requires the
  control store, a recent worker with the same application fingerprint, and the
  accounting dependency, plus current operational observations when configured.
  Named results distinguish each component. A worker heartbeat withdraws the
  worker role when its actual processing thread is dead or cannot claim work.
- HTTPS ingress supports explicit certificate trust and optional required
  client certificates. The engine transport adapter preserves the accounting
  interface while using a verified, mutually authenticated TLS connection.
- A private accounting gateway provides TLS for an engine without native TLS.
  Its final hop is loopback HTTP. A deployment must protect that hop within the
  engine host/network namespace and keep the raw engine port unreachable from
  other nodes. Local fault-injection paths still use their existing loopback
  transport. The gateway is not evidence of encryption on every service link.
- A fresh database installer creates separate migration and runtime identities.
  The runtime identity has no schema creation or table ownership and cannot
  rewrite schema versions, alter tables, or delete retained audit history.
  Normal serving refuses missing schemas instead of performing migrations.
- Private deployment recovery captures TLS/issuer files and consistent SQLite
  issuer state. Restore verifies files into an **inactive staging directory**;
  it does not silently activate old endpoints or credentials. Normal full
  backups also include runtime policy/session/grant/worker tables. Completed
  backup archives can use the existing authenticated archive encryption tool.
- Scoped operational policies, durable incidents, attributed acknowledgements,
  authenticated history cursors and monitor-owner failover execute inside these
  Runtime controls. See [OPERATIONS_MONITORING.md](OPERATIONS_MONITORING.md).

## Private configuration and installation

`deployment.json` is owner-only. It accepts the `avenra-deployment/1` format,
database mode/pool limits, API role/concurrency/deadline/TLS policy, institutional
identity enforcement and bounded session age, accounting TLS identity references,
and an optional pinned application fingerprint. API and worker startup check a
pinned release. Secret references resolve only inside `deployment-secrets/`;
keys are not submitted through tenant URLs or stored in public reports.

Use `scripts/deployment.py new-database --data <new-private-directory>` to
install a fresh database with distinct roles. It preserves existing deployments.
Keep `migration.dsn` offline from API/worker nodes after installation; only
`control.dsn` is needed for serving. `issue-identity` and `synthetic-issuer`
provide explicit private fixture provisioning. They print metadata only.

Synthetic certificates expire after seven days. Issue a new version rather than
overwriting an identity. This is test PKI; independent key custody, revocation
infrastructure and institutional certificate lifecycle acceptance remain open.
No system certificate trust store is modified.

For a deployed rehearsal, the application uses the existing entry points:
`python -m avenra.asgi --data <directory> --port <port> --mode api` and
`python -m avenra.worker --data <directory>`. The synthetic identity provider and
private service gateway are internal counterparties, not replacement consoles.
Only loopback listeners are used by the automated rehearsal.

## Observed acceptance

Run `.local/runtime/bin/python scripts/verify_production_runtime.py` from the
repository root. It installs fresh roles/database, starts two real API nodes,
separate workers, an HTTPS issuer and mTLS accounting gateway, and probes the
actual processes. No mocked JWT verification or simulated financial balance
authority is substituted. A controlled network proxy supplies real response
latency for deadline and admission checks.

The case pack covers TLS trust/client/hostname failures, code/client/PKCE replay
restrictions, ID-token versus API audience, role/environment isolation, grant
and session revocation, same-subject checking refusal, exact native balances,
database privilege denial, shared quotas/backpressure, physical dispatch bounds,
dependency readiness and worker stop/restart, recovery-file integrity and audit
verification. Failed attempts remain retained. Receipts bind the application
fingerprint and case-pack hash; changed code requires a fresh qualification.

The report is `.local/production-runtime-verification.json`. Release readiness
requires this evidence in addition to existing financial/lifecycle acceptance.
It separately reports the locally exercised runtime scope and keeps whole-product
production readiness false while remaining requirements are open.

`scripts/verify_acceptance.py` runs the 27 identity, API/worker,
payment/banking/simulation/UPI, synthetic-bank, lifecycle, product, service
recovery, financial-data, signed-release, standalone-checkpoint, operational
outage, additive migration, coordinated cold recovery, reference-capacity, native-clock and full-restore
suites sequentially. Each receipt must match the
fixed application fingerprint and be freshly written by its subprocess.
Its manifest and per-case logs remain under `.local/verification-attempts/`.

Remaining work stays in `PRODUCTION_COMPLETION_MATRIX.md`: complete selected
wire/product profiles, AA interoperability, all-service trust/key custody,
native/fleet releases and general schema-changing migrations, broader workload
and failure coverage, alert routing/retention/whole-system recovery, Linux/KVM execution and
independent assessment. This upgrade does not establish official UPI
compatibility or RBI/NPCI acceptance, and it enables no live payment connector.

The signed application release path and its bounded deployment evidence are
documented in [RELEASES.md](RELEASES.md). Compatible code upgrades and rollback
preserve financial history; no automatic schema downgrade is performed.
