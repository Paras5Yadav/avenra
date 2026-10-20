# Avenra

A locally operated financial lab with adapted engine source included in this repository. The console controls synthetic INR payments, ledger-backed assertions, network fault injection and retained test evidence.

This build provides a working local testing foundation. It is not a live UPI switch, an institutional certification service or a production deployment.

## Run locally

On this Mac, the engines are built and the private database is initialised. From this folder:

```sh
python3 scripts/local.py start
```

Open **http://127.0.0.1:4827**. The console creates its operator session on the same origin. The default workspace contains a development environment with a customer, merchant and treasury account. All balances are synthetic.

```sh
python3 scripts/local.py status
python3 scripts/local.py stop
python3 scripts/local.py stop --database
```

The launcher manages only tracked processes belonging to this installation. The database uses a separate data directory and port from other local databases. Data is preserved when services stop.

For a new machine, install Python 3.12 or later, Go 1.27, a compatible protocol compiler and the local database toolchain. The current launcher targets this Mac's database installation. It needs an adapter before use on other operating systems. Prepare and build the included source with:

```sh
python3 scripts/prepare_engines.py
python3 scripts/build_engines.py
```

Pinned source download locations, original terms, revisions, hashes and adaptation records live in [LICENSES](LICENSES/). First-time dependency resolution needs network access. Normal console operation uses loopback services.

## What works

| Capability | Behaviour |
|---|---|
| Workspace and environment scopes | Separate ledger per environment; scoped API keys; synthetic fixtures |
| Payments | Durable intent before dispatch; integer paise; payload conflict detection; persisted outbox |
| Accounting | Included engine owns postings and balances; no independently mutable balance copy in the console |
| Recovery | A real per-intent TCP fault drops response bytes; reconciliation reuses the original financial identity |
| Notifications | Local sink deduplicates repeated notifications; this is not an external bank callback integration |
| Scenarios | Success, replay/conflict, insufficient funds, lost response, duplicate notification and concurrent replay |
| Evidence | Expected/observed assertions, append-only records, SHA-256 and local HMAC integrity verification |
| Console | Payment submission, reconciliation, account balances, journal, scenario runs, evidence export and service status |

Each scenario gets a separate fixed fixture and ledger. It does not spend the interactive environment's synthetic funds. A repeated run request with the same key resolves to the same run. A fresh key creates another fixture.

Payment and run lists show the latest 100 records. Dashboard totals explicitly refer to that window. Ledger entries are retained by the accounting engine.

## Source present, capability pending

The isolated execution engine is included and its workspace metadata resolves. This Apple Silicon host does not supply the qualified Linux/KVM worker needed for VM execution. The console therefore reports execution as unavailable and exposes no host-code-execution fallback. Helper-unit test results are separate from VM isolation evidence.

The API contract engine's source is included. Its service and operator-controlled test packs are not yet qualified or enabled. Consent/data sharing, lending, fraud monitoring, institutional UAT adapters and live payment access are not implemented in this build. The console shows those capabilities as not configured.

The India lab assertions are an owned synthetic profile. They do not claim compliance with a participant's confidential protocol, settlement system or current institutional certification pack.

## Local API

The first start creates `.local/data/credentials.json` with a workspace-scoped development API key. It is readable only by the local account and is excluded from version control. Treat the entire `.local/` directory as private. The browser uses a separate HttpOnly session cookie and CSRF token; it never receives this API key.

The API shape is:

```text
GET  /api/catalog
GET  /api/tenants/{tenant}/environments/{environment}/overview
GET  /api/tenants/{tenant}/environments/{environment}/accounts
POST /api/tenants/{tenant}/environments/{environment}/payments
POST /api/tenants/{tenant}/environments/{environment}/payments/{payment}/reconcile
POST /api/tenants/{tenant}/environments/{environment}/runs
GET  /api/tenants/{tenant}/environments/{environment}/runs/{run}
GET  /api/tenants/{tenant}/environments/{environment}/runs/{run}/evidence
```

Payment body: `payer_id`, `payee_id`, integer `amount_paise`, `request_key`, and `fault` (`none` or `lost_response`). Run body: `scenario_key` and `request_key`. Authentication uses the `Authorization: Bearer …` header for development clients.

An HTTP acceptance or an unconfirmed response is not proof of posting. Check the persisted payment state and reconcile using the original intent. Reconciliation can post an original request if it never reached the ledger; it cannot be treated as a read-only query.

## Verification and operating boundary

```sh
python3 scripts/verify.py
python3 -m unittest discover -s tests -v
```

Additional targeted recovery checks are `python3 scripts/verify_restart.py` and `python3 scripts/verify_recovery.py`. The restart check briefly stops this installation's tracked services. The crash check uses a separate private controller fixture. Read [VERIFICATION.md](VERIFICATION.md) for the recorded coverage and outstanding qualification.

Verification uses real local engines and synthetic data. Reports and service logs are kept under `.local/`. Final evidence JSON can be downloaded through the console. Keep the installation's evidence key with retained reports; losing it prevents local HMAC verification.

The operator session, loopback binding, origin checks and private database assume a trusted single-user host. The database and engine APIs are not hardened for hostile local users or remote tenancy. Logical workspaces do not establish operating-system isolation. Local HMAC evidence is not independent attestation.

Production requires a qualified Linux execution fleet, workload/egress isolation, participant-specific contracts, external identity and authorisation, secret management, encrypted connections, durable backups and independently tested recovery, retention, availability and operational controls. No live credentials or customer information are needed for this local build.
