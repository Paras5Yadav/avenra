# Operational monitoring in the existing console

Engine status → Runtime controls now includes operational policies, current
incidents, retained history and acknowledgements. These controls observe the
same environment and durable worker used by the payment lab. They do not create
another app or another balance authority.

## Policy and observations

A runtime administrator publishes a complete bounded monitoring policy. Versions
are immutable, installation-signed and bound to the submitting actor and request
key. Publishing monitoring alone adds request observations; it does not turn on
request admission restrictions. Existing admission policies remain separate.

The observer collects actual worker freshness, accounting availability, due-work
age/depth, unconfirmed operations by layer, failed records by system, server
errors/deadlines, latency and rate/concurrency/queue refusals. Deliberately
future-scheduled jobs do not count as overdue work. Counts from different layers
are not added together as if each represented a different financial intent.

Latency is nearest-rank p95 of at most 5,000 recent completed requests. The API
discloses truncation and sample size. Below the configured minimum sample size,
latency is unknown, not zero. Refusal counters occupy a fixed 4,096-second ring
per environment/code with one-second resolution; history beyond that observation
window is represented by retained incident transitions, not per-request refusal
records. Current observations replace one row per configured environment.

API and worker processes compete for a database-fenced monitor lease. An API can
therefore observe the loss of all financial workers. Results from an old owner,
old policy or older observation cannot change current incident state. A paged
scope cursor avoids starvation; readiness checks both leadership and freshness
for every configured scope. At most 1,000 monitoring scopes are supported by this
implementation; that bound is not a measured capacity guarantee.

Worker readiness also depends on the actual processing thread successfully
claiming work. A dead thread or repeated receipt-store exception withdraws its
worker role even if the process heartbeat thread is still alive. A recoverable
store exception retries the original leases and identities. A fatal thread exit
needs an explicit restart or replacement process.

## Incidents and response

Signals enter pending/firing only after their configured dwell, and recover only
after the recovery dwell. Gaps in observations interrupt continuity. Unknown
telemetry cannot clear a firing incident. In particular, an empty latency sample
cannot prove latency recovery.

Acknowledge records who reviewed an incident. It does not resolve the incident,
restart a job or decide a financial outcome. Request-key replay retains the
original acknowledgement; a changed actor/incident conflicts. Transitions and
acknowledgements are immutable history. Current incidents remain visible above
the paginated history even when many newer resolved incidents exist. History
cursors are authenticated and bound to the tenant, environment and resource.

| Signal | Response using existing controls |
| --- | --- |
| Worker unavailable | Inspect the named readiness component and owned worker logs. Restore a matching worker. Preserve accepted identities and leases. |
| Accounting unavailable | Restore the original dependency and trust configuration. Reconcile unconfirmed effects under the original operation IDs. |
| Queue lag/depth or admission pressure | Inspect due work, worker health and the declared workload. Reduce new admission or restore capacity; retain original status and recovery access. |
| Unconfirmed operations | Inspect original financial legs, participant messages and native evidence. Use supported original-ID status/recovery endpoints. |
| Failed work | Inspect the original outbox/delivery/notification record and failure cause. Correct the cause before the supported retry. |
| Request errors/latency | Correlate deadlines, physical dispatch occupancy and dependency observations with eventual domain outcomes. Retrying with a new payment key can create another payment. |

The observer sends no external notifications and performs no financial actions.
Institutional alert routing, escalation ownership, telemetry retention, log
rotation, additional dependencies and independent operational acceptance remain
open. Incident history is retained; no automatic incident purge is implemented.

## Scoped API

All routes extend the existing authenticated tenant/environment prefix.

| Method/path | Contract |
| --- | --- |
| `GET /banking/runtime/operations` | Policy, freshness, current metrics, current incidents and 50 history entries; optional authenticated `cursor` query parameter. |
| `POST /banking/runtime/operations/policies` | Every field from `defaults` plus a stable `request_key`; requires runtime administration. |
| `GET /banking/runtime/operations/incidents/{id}/events` | 50 immutable transitions/acknowledgements; optional resource-bound `cursor`. |
| `POST /banking/runtime/operations/incidents/{id}/acknowledge` | Exactly `request_key`; attributed to the authenticated administrator. |

The existing runtime observation/administration permissions and environment
grants apply. Secret key material is never accepted through these routes.

## Rehearsals and capacity boundary

```sh
.local/runtime/bin/python scripts/verify_operations.py
.local/runtime/bin/python scripts/verify_operations_upgrade.py
.local/runtime/bin/python scripts/verify_capacity.py
```

The operational pack separates explicitly labelled synthetic metric vectors
(state transitions and pagination) from actual HTTPS/mTLS processes, stopped
workers, stopped accounting gateway, killed monitor owner and native financial
recovery. Acknowledgement must not hide unknown money or a stopped dependency.

The capacity reference is `avenra-local-capacity/2`: two tenants, two HTTPS API
nodes and two financial workers. Its phases are 60 seconds at 1 payment/second
per tenant; 60 seconds at 12/second for an aggressor and 1/second for the other
tenant; 20 seconds with both workers actually killed; and 40 seconds of recovery
at 1/second per tenant. Each payment is 25 synthetic paise. The aggressor alone
has a 16-pending-work admission limit during its pressure phase.

Traffic is scheduled independently of response time, with bounded client
concurrency. Generator backlog is reported. Steady and protected-tenant cohorts
must meet admission p95 ≤ 2 seconds, observed completion p95 ≤ 15 seconds and
unsuccessful fraction ≤ 1%. Worker-outage latency is reported separately. All
accepted identities must eventually finish with valid financial evidence; every
native balance and journal leg is checked against an independent client book.
Overload refusals are explicit and cannot be counted as successful payments.

`--smoke` uses shorter phases solely to debug the driver. Its receipt explicitly
cannot qualify the reference workload. Passing either run proves only the exact
observed host/profile, not institutional TPS, fleet availability or official UPI
conformance. Release readiness requires a fresh current-build reference receipt.

Version 2 retains the offered load and latency/error budgets and adds an explicit
five-second maximum wall/monotonic-clock drift. Host suspension or a large wall
clock adjustment invalidates the continuous run and is retained as a failed
attempt. It is never repaired by extending grants, removing expiry rules or
discarding incomplete requests. Explicit recovery uses `POST .../reconcile`;
`GET .../status` is observational. Unexpected status/recovery responses are
recorded as client errors instead of silently looping on a missing route.

An interrupted matrix can resume through
`scripts/verify_acceptance.py --resume <private-acceptance-manifest>`. Only the
consecutive passing prefix with the same application build, matrix command/
argument definition, case script hashes and passing receipts is reused. Failed
and subsequent cases run again. Copies of the reused receipts and the original
manifest hash remain attached to the new attempt; a stale build or definition is
refused. Historical manifests lacking that definition need independently checked
source provenance before they can be used as resume inputs.

Operations tables use the reviewed additive migration in [RELEASES.md](RELEASES.md).
Full backups compare their original ordered row hashes and policy signatures
after restoration, alongside existing native accounting/evidence checks.
