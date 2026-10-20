# Avenra local verification

Verified on 17 October 2026 on this Apple Silicon Mac. This records the first working local build, using included, adapted engine source and synthetic INR data.

| Boundary | Observed result |
|---|---|
| Browser → payment API → accounting engine → UI | A submitted ₹1,250 payment reached a confirmed transaction and rendered as succeeded |
| Real response loss → manual reconciliation | A ₹100.01 payment became unconfirmed after response bytes were dropped, then reconciled to its original ledger effect |
| Browser → scenario API → fresh ledger → evidence | A console-created response-loss scenario passed all seven assertions; the evidence JSON downloaded successfully |
| HTTP and recovery checks | 45 checks passed; machine-readable report in `.local/verification.json` |
| Concurrent duplicate requests | Eight HTTP submissions resolved to one intent and one financial effect |
| Payload conflict | Reusing a request key with a changed amount returned HTTP 409 |
| Workspace/environment scope | Foreign workspace reads, foreign accounts and mismatched environment ownership were rejected |
| Browser session boundaries | Unauthenticated reads, foreign origins, unexpected hosts and cookie mutations without CSRF were rejected; a second tab preserved the session and CSRF token |
| Local service restart | Financial identity, ledger transaction, balances and signed evidence survived; replay added no debit |
| Abrupt controller exit | A separate controller exited immediately after intent/outbox acceptance; another process recovered it with exactly one transaction and correct balances |
| Accounting reprovisioning | An existing ledger's environment ownership was checked before reuse |
| Evidence integrity | Independently recomputed SHA-256 and HMAC matched; changing the manifest changed its digest |
| Original application unit tests | Three tests passed for lease fencing, audit mutation/detection and strict monetary input |
| Accounting engine selection | Race-enabled tests passed for the v2 API controllers and selected accounting machine/compiler/VM packages |
| Network fault engine | Its race-enabled Go test suite passed; external infrastructure E2E tests were not exercised |
| Execution source workspace | All local workspace manifests resolved after namespace adaptation |
| Selected execution helpers | 23 unit tests and one documentation test passed with serial test execution |

The default parallel execution-helper run initially failed one tracing relationship assertion. That test passed in isolation, and the complete selected set passed serially. Parallel-test reliability remains an execution qualification item. These helper checks do not prove that a VM boots, isolates workloads, snapshots or recovers correctly.

The initial accounting start exposed a generated descriptor corrupted by a module-path text change. The build now regenerates protocol bindings from the adapted source. Subsequent startup, native tests and real financial flows passed. A renamed HTTP header also needed canonical casing in the fault engine; its subsequent suite passed.

## Source and build coverage

Four pinned source trees were materialised: 3,591 execution files, 1,138 accounting files, 86 fault-engine files and 454 contract-engine files. These are source/project file counts, not audited-line counts. Original hashes, licences, revisions and adaptation records are retained under `LICENSES/`. Generated accounting bindings have a separate compiler/generator record.

The accounting and fault executables were compiled from the included source and are running on loopback. The console and original application documentation use Avenra branding. Required source notices and technical dependency identifiers remain. Engine services are not fetched from repository checkouts during normal operation.

The contract engine is source-only in this build. VM execution is disabled because the required Linux/KVM worker has not been provisioned or qualified. Consent/data, lending, risk and live participant adapters are not configured. No live funds or bank credentials were used.

## Evidence and limits

The browser screenshot, exported response-recovery report, browser checks and private controller-crash fixture are retained under `.local/qa/`. Native engine logs and test logs are under `.local/`. These paths are excluded from version control. The private container profile used during setup is stopped; the local console and native financial services remain running.

This is selective validation of the supported local flows. It is not a full imported-source audit, a full execution-engine test pass, an assessment of production isolation or an institutional conformance result. The default engine APIs and private database assume a trusted local host. Local HMAC verification establishes integrity relative to this installation's key, not independent attestation. Production qualification remains outstanding as described in the README.
