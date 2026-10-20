# Avenra operations for this installation

These commands manage the local engineering installation. They are not a portable production deployment procedure. All funds and identities used by the installed flows are synthetic.

## Private state and service ownership

`.local/` contains private connection files, signing keys, service credentials, logs, caches, reports and backups. Keep it excluded from version control. Connection/configuration files require owner-only permissions. Do not paste their contents into chat, issue descriptions or public logs.

The active control connection is `.local/data/control.dsn`. The old `.local/data/avenra.db` is a preserved migration source. The accounting runtime uses `.local/data/accounting.dsn`; its native process receives that connection through its environment rather than command-line arguments. `.local/data/database-admin.dsn` is reserved for owned local administration and qualification scripts.

The private database uses authenticated loopback/local connections. The accounting runtime is a non-superuser role owning its dedicated accounting database. The controller uses its own database role. These local controls do not establish security against an administrator of the host.

## Start and stop

```sh
python3 scripts/local.py start
python3 scripts/local.py status
python3 scripts/local.py restart-console
python3 scripts/local.py stop
```

The native launcher restarts only tracked Avenra processes. An occupied untracked port is refused. `stop --database` additionally stops the private native database; it preserves all data.

The additional service stack uses the private container profile and socket, without changing the global Docker context:

```sh
.local/runtime/bin/python scripts/services.py status
.local/runtime/bin/python scripts/services.py start-contracts
.local/runtime/bin/python scripts/services.py start-products
```

Monitoring uses `.local/deploy/monitoring.json`. The helper in `scripts/services.py` resolves the owned container socket and private client configuration; use it when operating that deployment. `scripts/setup_monitoring.py` materializes the pinned private qualification deployment and routing patch; it requires the retained reference under `.local/references/monitoring-stack/`. `scripts/bind_monitoring.py` installs a restricted history reader and the local synthetic binding. These setup scripts have not been qualified as an unattended production installer.

Native contract/monitoring interfaces are private test services; local service authentication settings are unsuitable for public exposure. Do not expose those ports directly to a shared network.

## Financial recovery

Engine status → Runtime controls contains scoped monitoring policies, current
incidents and retained event history. Acknowledge records review, while actual
observed recovery determines resolution. [The operational guide](docs/OPERATIONS_MONITORING.md)
describes the supported signals, incident response and exact local capacity
rehearsal. No external notification destination is configured by these controls.

Reconcile uncertain interbank operations through their original operation endpoint. Keep the original request key, body and leg identities. A timeout is not evidence of a declined transfer.

Product command reconciliation retains the original native idempotency key. A changed binding is blocked. Investigate older uncertain commands without a saved binding rather than silently dispatching them to the current configuration. Inspect outbox and service logs for dead-letter/unavailable dependencies.

Monitoring can prove an accepted message from a restricted native inbox after response loss. It does not advance to a completed evaluation until the matching native report is retained. Reports do not change payment authorization.

## Acceptance commands

Run shared-service restart/recovery checks separately from HTTP suites. They intentionally pause the owned console or native services.

```sh
.local/runtime/bin/python scripts/verify.py --restart
.local/runtime/bin/python scripts/verify_recovery.py --postgres
.local/runtime/bin/python scripts/verify_banking.py
.local/runtime/bin/python scripts/verify_banking.py --postgres
.local/runtime/bin/python scripts/verify_banking_http.py
.local/runtime/bin/python scripts/verify_products.py
.local/runtime/bin/python scripts/verify_service_recovery.py
.local/runtime/bin/python scripts/verify_units.py
```

The response-loss service check temporarily changes private local bindings to owned fault proxies and restores the original configuration in its cleanup. Use it only while local test operations can be paused.

## Backups and isolated restores

```sh
.local/runtime/bin/python scripts/backup_restore.py --verify-restore
.local/runtime/bin/python scripts/backup_components.py
```

The first command pauses the console, exports control/accounting stores, roles and private recovery configuration, restores copies into new databases and compares evidence, audit integrity, balances and journals through a separately started accounting engine. It never replaces the active databases. Retained restore databases can be inspected by an administrator.

The second command stops application writers, then service databases, archives the owned persistent volumes, restores isolated copies and compares banking-product records, monitoring configuration/history/evaluations and provider contract collections. Original volumes are preserved. Successful temporary restore volumes are removed after their archives and logs have been retained; failed copies are retained for investigation.

The message-broker volume is archived, but broker replay/consumer recovery is not qualified by the database restore check. End-to-end institutional recovery, RPO/RTO, failover and capacity must still be measured separately.

Backups contain signing keys and private credentials. The local directories are owner-protected; encrypted off-host storage, retention and recovery-key custody are not configured. Do not upload these archives without the appropriate encryption and access policy.

## Identity and external connections

Local mode permits same-origin operator sessions on this trusted host. Institutional mode is configured using owner-only `identity.json` with the approved issuer, JWKS, authorization/token endpoints, client/API audience, external HTTPS origin and claim mapping. Its code/PKCE/JWKS validation is unit-tested against a local signed issuer; no real institutional identity-provider deployment is configured.

Service bindings are operator files, not tenant-supplied URLs. Remote service transports require verified HTTPS. Current external bank UAT and participant-specific mutual-TLS/wire adapters are not implemented. Bank-issued certificates, credentials and network access cannot be replaced with public internet material.

## VM qualification and release gates

```sh
.local/runtime/bin/python scripts/qualify_execution.py --preflight
.local/runtime/bin/python scripts/release_readiness.py
```

The execution check intentionally fails on this Mac because Linux x86_64/KVM/TUN are unavailable. On a suitable worker, complete the included worker deployment and authentication before running the lifecycle check. A successful lifecycle check alone does not prove tenant isolation, egress control or safe arbitrary-code execution. The application never falls back to executing guest workloads on the host.

The readiness command defaults to institutional hosting and returns exit code 2 while that target is unqualified. `--profile local` returns success only for the supported local acceptance boundary. A passing local suite therefore cannot silently pass a production release check.

The readiness report separates local acceptance from institutional hosting, bank UAT, AA interoperability, isolated execution and live-rail qualification. A green local suite does not turn those profiles into production-ready capabilities.

## ASGI API and separate workers

The local process manager launches `avenra.asgi` on loopback. Its default combined mode includes a durable worker; `--mode api` disables that embedded worker. For separated operation, run `python -m avenra.asgi --data /absolute/private/data --mode api` and `python -m avenra.worker --data /absolute/private/data`. Separate workers require the owner-readable PostgreSQL connection file and share the installation evidence key. Each API port has an installation lock. Accepted requests remain in PostgreSQL while API/worker processes restart.

ASGI request parsing, application concurrency/body limits, host/origin checks and graceful lifespan shutdown are exercised locally. Deploying this behind institutional TLS/SSO, authenticating internal services, setting capacity/SLOs and qualifying HA/DR remain deployment work. Do not expose the local development identity mode to an external network.

Schema 008 is additive except for an executor discriminator on existing financial operations. Participant-owned operations must be reconciled through `/banking/upi/payments/{id}/reconcile`. Their generic banking reconcile route deliberately refuses to bypass the participant state machine. Preserve installation keys, control/accounting journals and all participant message/inbox/evidence records during restore.

Schema 009 adds immutable participant connection revisions, configuration-check receipts, active bindings and immutable per-payment adapter snapshots. Existing intents retain their original local path; new intents pin the selected revisions. Take a control backup before upgrade. Restoration includes all three connection tables and verifies their check signatures. External drafts contain secret references only and do not establish connectivity. See [participant connections](docs/PARTICIPANT_CONNECTIONS.md).

Synthetic TLS services use private `participant-secrets/` identity bundles and `synthetic-banks/` service/receipt directories beneath the installation data directory. Bank authority bindings stay stable across client credential epochs; signing-key revocation is applied by the service's trusted-client registry. Local backup captures these private components, and restoration verifies hashes, authority bindings, permissions and receipt counts. Do not export the private key directories. Public signing keys and signed financial traces are retained in payment evidence. See [synthetic bank operations](docs/SYNTHETIC_BANKS.md) for lifecycle commands, seven-day test identity validity and remaining institutional security limits.
