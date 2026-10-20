# Configuration reference

[Documentation](../README.md)

Every `AVENRA_*` environment variable listed here is a supported operator
setting. Variables that are not listed are not read by release binaries.
"Unset" means the built-in default applies. Variables marked **required** make
the service fail at startup when missing.

Settings that exist only so tests can shorten a timer or flip a scenario are
test hooks. They are read only by binaries built with the `test-hooks` Cargo
feature (`avenra-runtime-common-core/test-hooks`), which the local kind image
enables and release images never do. They are intentionally undocumented, and
`scripts/source_guards/env_test_hook_boundary.py` fails CI if code names a
variable listed here in a test-hook read, directly or through a string constant
from the same crate.

Secrets are passed as files: set the `*_PATH` variable to a mounted file. The
inline forms (`AVENRA_ECW_CREDENTIAL_KEY`, `AVENRA_ECW_CREDENTIAL_PREVIOUS_KEY`,
`AVENRA_API_ROUTE_ED25519_SIGNING_KEY`, `AVENRA_API_ROUTE_ED25519_VERIFICATION_BUNDLE`)
and `AVENRA_CLUSTER_NODE_API_ENDPOINT` are deprecated and still read for one
release so directly provisioned hosts can upgrade in place.

## Reconcilers and kill switches

Each mutating loop has one `*_ENABLED` switch. Loops that are on by default
treat any value other than a truthy one as off, so a typo stops the loop
instead of leaving it running. The one exception is
`AVENRA_PUBLIC_OPERATION_DISPATCHER_ENABLED`, which keeps the lenient parser
because disabling it by mistake would leave every create queued forever.

| Variable | Default | Notes |
| --- | --- | --- |
| `AVENRA_FAILED_RESTORABLE_REPAIR_ENABLED` | on | Reverts failed restorable sandboxes. |
| `AVENRA_STARTED_SANDBOX_REPAIR_ENABLED` | on | |
| `AVENRA_FAILED_SANDBOX_CLEANUP_ENABLED` | on | Part of the transient lifecycle sweeper. |
| `AVENRA_INTERNAL_API_DELETED_PARTITION_ENABLED` | on | |
| `AVENRA_INTERNAL_API_DELETED_SUPPRESS_ABSOLUTE` | 500 | |
| `AVENRA_KUBE_NODE_SYNC_ENABLED` | on | |
| `AVENRA_OBSERVED_PROJECTION_GC_ENABLED` | on | |
| `AVENRA_PUBLIC_OPERATION_DISPATCHER_ENABLED` | on | Without it, create requests are accepted and never complete. |
| `AVENRA_SNAPSHOT_STORAGE_SWEEPER_ENABLED` | on | Does nothing without `AVENRA_SNAPSHOT_BUCKET`. |
| `AVENRA_ORPHANED_SANDBOX_RECONCILER_ENABLED` | on | |
| `AVENRA_API_RECONCILER_DRY_RUN` | true | The orphaned-sandbox reconciler classifies and logs only until set to false. |
| `AVENRA_API_RECONCILER_MAX_CLASSIFICATIONS_PER_MINUTE` | 60 | A larger value clamps to 300; `0` or a non-number keeps the default. |
| `AVENRA_API_RECONCILER_MAX_ACTIVE_QUARANTINES`, `AVENRA_API_RECONCILER_MAX_NEW_QUARANTINES_PER_SWEEP`, `AVENRA_API_RECONCILER_MAX_QUARANTINE_PERCENTAGE` | 50, 20, 25 | Safety ceilings; `0` stops new quarantines. A larger value clamps to 500, 200 and 75 respectively. |
| `AVENRA_GONE_NODE_QUEUE_GC_ENABLED` | on | |
| `AVENRA_GONE_NODE_QUEUE_GC_DRY_RUN` | true | |
| `AVENRA_TRANSIENT_LIFECYCLE_SWEEPER_ENABLED` | off | |
| `AVENRA_TRANSIENT_LIFECYCLE_SWEEPER_TICK_SECS` | 60 | |
| `AVENRA_TRANSIENT_LIFECYCLE_SWEEPER_DEFAULT_AGE_SECS` | 600 | Age before a row in a transient lifecycle state is retired, unless a per-state age below applies. |
| `AVENRA_TRANSIENT_LIFECYCLE_SWEEPER_RESTORING_AGE_SECS`, `AVENRA_TRANSIENT_LIFECYCLE_SWEEPER_SUSPENDING_AGE_SECS`, `AVENRA_TRANSIENT_LIFECYCLE_SWEEPER_CHECKPOINTING_AGE_SECS` | default age | Per-state override; `0` disables that state. |
| `AVENRA_STUCK_SNAPSHOT_REAPER_ENABLED` | off | |
| `AVENRA_SUSPENDED_EXECUTION_REAPER_ENABLED` | off | Runs dry by default. |
| `AVENRA_STUCK_CONTAINER_CREATING_GC_ENABLED` | off | |
| `AVENRA_STUCK_CONTAINER_CREATING_GC_MAX_DELETES_PER_MINUTE` | built-in | |
| `AVENRA_WARM_POOL_ORPHAN_GC_ENABLED` | off | Runs dry by default. |
| `AVENRA_ORPHANED_RESTORE_POD_GC_ENABLED` | off | |
| `AVENRA_ORPHANED_RESTORE_POD_GC_DRY_RUN` | true | Classifies and reports orphaned restore pods without deleting them. Only `0`, `false`, `False` or `FALSE` turns it off; any other value keeps it on. |
| `AVENRA_ORPHANED_RESTORE_POD_GC_INTERVAL_SECS`, `AVENRA_ORPHANED_RESTORE_POD_GC_TICK_BUDGET_SECS` | 60, 45 | Tick cadence and per-tick time budget. A budget at or above the interval lets ticks run back to back. Seconds; a value above one day clamps to it, and `0` or a non-number keeps the default. |
| `AVENRA_ORPHANED_RESTORE_POD_GC_THRESHOLD_SECS` | 300 | How long a pod must stay superseded before it's deleted. Seconds; a value above one day clamps to it, and `0` or a non-number keeps the default. |
| `AVENRA_ORPHANED_RESTORE_POD_GC_MAX_DELETES_PER_MINUTE` | 60 | Orphans acted on per rolling minute. A larger value clamps to 600; `0` or a non-number keeps the default. To stop deletions, use `DRY_RUN` or `ENABLED`. |
| `AVENRA_ORPHANED_RESTORE_POD_GC_BATCH_SIZE` | 500 | Sandbox rows looked up per query. A larger value clamps to 5000; `0` or a non-number keeps the default. |
| `AVENRA_ORPHANED_RESTORE_POD_GC_TEARDOWN_STUCK_AFTER_SECS` | 300 | How long a pod may stay terminating before it's reported as a stuck teardown. Seconds; a value above one day clamps to it, and `0` or a non-number keeps the default. |
| `AVENRA_RECONCILE_QUEUE_GC_ENABLED` | off | Needs AWS. |
| `AVENRA_ORPHANED_NODES_ENABLED` | off | Needs AWS. |
| `AVENRA_NODE_DRAIN_ENABLED` | off | |
| `AVENRA_NODE_DRAIN_NODE_CENSUS_RESIDENCY` | off | |
| `AVENRA_NODE_DRAIN_TERMINATION_BLOCKING_GATE` | off | Parks nodes that still hold stopped, created, deleting or quarantined residents in `draining` instead of advancing them to `drained`. |
| `AVENRA_NODE_DRAIN_TERMINATOR_KIND` | `aws` | `aws`, `kube-delete` or `noop`. |
| `AVENRA_DRAINED_NODE_TERMINATOR_ENABLED` | off | |
| `AVENRA_CLUSTER_CONTROLLER_DRAIN_EVACUATION_COHORT_VALUE` | unset | Unset disables the rollout-cohort feature. |
| `AVENRA_NODE_GROUP_LABELER_ENABLED`, `AVENRA_NODE_GROUP_MIRROR_ENABLED` | off | |
| `AVENRA_NODE_SERVICE_READINESS_ENABLED` | off | |
| `AVENRA_SNAPSHOT_RETENTION_SWEEPER_ENABLED` | off | |
| `AVENRA_SNAPSHOT_RETENTION_SWEEPER_KEEP_N`, `AVENRA_SNAPSHOT_RETENTION_SWEEPER_MAX_PER_TICK` | built-in | |
| `AVENRA_ADMIN_AUDIT_RETENTION_GRACE_SECS` | 30 days | The retention loops themselves always run. |
| `AVENRA_ADMIN_AUDIT_RETENTION_MAX_RECORDS` | 100,000 | Row cap applied regardless of age; raise it to keep more history (ceiling 10,000,000). |
| `AVENRA_TEMPLATE_PUBLISH_RESERVED_NAMES` | empty | Extra template names reserved for operator publication, in addition to `seed` and `code-runtime`. |

## Sandbox placement and nodes

| Variable | Default | Notes |
| --- | --- | --- |
| `AVENRA_NODE_ROLE` | `worker` | |
| `AVENRA_NODE_ID` | file, then generated | |
| `AVENRA_NODE_MANAGED_BY` | unset | |
| `AVENRA_K8S_NODE_NAME`, `AVENRA_K8S_CONTROL_PLANE` | unset / off | |
| `AVENRA_ALLOW_SANDBOX_PLACEMENT` | off | |
| `AVENRA_SANDBOX_NODE_GROUP_PLACEMENT` | off | |
| `AVENRA_SANDBOX_RESTORE_SOURCE_NODE_HARD_PIN` | off | Pin a restore to the snapshot's source node with a required node-affinity term. |
| `AVENRA_SANDBOX_NODE_SERVICES_READY_AFFINITY` | off | Prefer nodes whose node-local services are ready; useful only where `AVENRA_NODE_SERVICE_READINESS_ENABLED` is on. |
| `AVENRA_SANDBOX_NODE_SELECTOR_KEY`, `AVENRA_SANDBOX_NODE_SELECTOR_VALUE` | `node.kubernetes.io/avenra-enabled`, `true` | Base node selector for sandbox pods and for the node-sync, node-group mirror and warm-pool loops; set both together. |
| `AVENRA_SANDBOX_NODE_SELECTOR_LABELS` | empty | |
| `AVENRA_SANDBOX_NAMESPACE` | `POD_NAMESPACE`, then `default` | Namespace of the sandbox pods. When it is not the node services' namespace, also set `AVENRA_NODE_API_NAMESPACE`: without it and `POD_NAMESPACE`, the API looks for node-api pods here. |
| `AVENRA_SANDBOX_POD_IMAGE` | `registry.k8s.io/pause:3.10` | |
| `AVENRA_SANDBOX_FORK_ENABLED` | on | |
| `AVENRA_SANDBOX_FORK_HARD_DEADLINE_SECS` | 240 | Positive integer seconds. |
| `AVENRA_NODEMGR_NETWORK_POOL_MAX_SLOTS` | 300 | |
| `AVENRA_NODEMGR_NETWORK_POOL_TARGET`, `AVENRA_NODEMGR_NETWORK_POOL_RETAIN_READY`, `AVENRA_NODEMGR_NETWORK_POOL_MIN_READY` | `min(max_slots, 100)`, `max_slots`, `min(max_slots, 32)` | Warm network slot pool sizes; set target and retained to `0` to keep the pool off. The pool only serves allocations that use the pool's own network namespace. |
| `AVENRA_NODEMGR_SERVICE_PORT_INGRESS_ISOLATION` | auto | `shared_host` on Kubernetes, else `exclusive_host`. |
| `AVENRA_NODEMGR_ACTIVATE_RUNTIME_SUPERVISOR_ON_START` | off | |
| `AVENRA_RTMGR_INSTALL_KERNELS_DIR` | unset | Enables runtime-manager kernel installs in this versioned kernel root, which must exist on the state directory's filesystem. Unset, installs and uninstalls are refused. |
| `AVENRA_NODEMGR_BTRFS_BALANCE_ENABLED` | on | |
| `AVENRA_NODEMGR_BTRFS_SEND_UNSTAMPED_PARENT` | off | Seal retained btrfs send-source parents without the template's received UUID; see [snapshot upload streaming](snapshot-upload-streaming.md). |
| `AVENRA_NODEMGR_DISK_IO_RESERVATION_ENABLED` | off | |
| `AVENRA_NODEMGR_DISK_IO_MAX_OPERATIONS_PER_SEC` | unset | Operator ceiling for each Firecracker root drive. Set a positive integer (for example `1000`) on node-manager and restart it. Applies to new boots, restores and running VMs, independently of saved assignments. See [guest disk I/O](avenra/design/guest-io.md#operator-ceiling). |
| `AVENRA_DISK_IO_SAFETY_MAX_OPERATIONS_PER_SEC`, `AVENRA_DISK_IO_SAFETY_MAX_BANDWIDTH_MIB_PER_SEC` | unset | Set both or neither. Caps explicit warm-pool policy limits; unset, the cap is the platform default of 10,000 operations/s and 100 MiB/s. A value below the default leaves that dimension at the default. |
| `AVENRA_NODE_DISK_ADMISSION_MIN_AVAILABLE_BYTES` | 50 GiB | Hard free-space floor. Lower it on small disks. |
| `AVENRA_BUILDER_BLOB_MIN_AVAILABLE_BYTES` | 50 GiB | |
| `AVENRA_NODE_API_UDS_PER_SANDBOX` | 64 | Per-sandbox control-socket permits, `1..=node permits`. |
| `AVENRA_INTERNAL_API_VISIBILITY_CACHE_TTL_SECS`, `AVENRA_INTERNAL_API_VISIBILITY_CACHE_STALE_CEILING_SECS` | built-in | Template visibility cache freshness and stale ceiling. |
| `AVENRA_SBXMGR_ACTIVATION_ADMISSION_MAX_CONCURRENT` | 5 | |
| `AVENRA_SBX_EVICT_ENABLED`, `AVENRA_SBX_EVICT_ENGAGE_FRACTION`, `AVENRA_SBX_EVICT_TARGET_FRACTION`, `AVENRA_SBX_EVICT_MIN_IDLE_SECS` | off, 0.75, 0.70, 25 h | |
| `AVENRA_SBX_EVICT_INTERVAL_SECS`, `AVENRA_SBX_EVICT_MAX_PER_TICK`, `AVENRA_SBX_EVICT_REUPLOAD_MAX_PER_TICK` | 600, 100, 5 | |
| `AVENRA_SBXMGR_NETWORK_CREDENTIALS_ENABLED` | off | |
| `AVENRA_SNAPSHOT_KEY_PREFIX` | empty | Object key prefix for suspend snapshots; keeps clusters apart in a shared bucket. |
| `AVENRA_CREDMGR_TOKEN_PATH` | `/var/run/avenra-auth/credmgr.token` | When set, the file is authoritative and an unreadable file is an error. |
| `AVENRA_SBX_GC_ENABLED`, `AVENRA_SBX_GC_ENGAGE_FRACTION` | off, 0.50 | |
| `AVENRA_UDS_PEER_PROCESS_CHECK` | off | The peer uid check always applies. |
| `AVENRA_ENABLE_GUEST_CONSOLE_LOGS` | off | Debug: attach the guest serial console and write `guest-console.log` per sandbox (node manager and sandbox manager). |
| `AVENRA_FIRECRACKER_VENDOR_DIR`, `AVENRA_FIRECRACKER_ASSET_DIR`, `AVENRA_FIRECRACKER_KERNEL_ASSET_DIR` | built-in paths | Node-manager runtime asset locations. |
| `AVENRA_SUSPEND_WAKE_ARBITRATION` | on | Kill switch: off lets a suspend that queued behind a concurrent wake through instead of rejecting it as superseded. |
| `AVENRA_SUSPEND_DEFAULT_FORMAT` | `btrfs-send` | Set `full` to roll the suspend codec default back. |
| `AVENRA_CLUSTER_CONTROLLER_LEADER_ELECTION_ENABLED` | on | Kill switch; off runs the reconcilers without a lease, which is only safe with one replica. |

## API, admission and limits

| Variable | Default | Notes |
| --- | --- | --- |
| `AVENRA_API_CLIENT_CONCURRENCY_MODE` | `observe` | `off`, `observe` or `enforce`. |
| `AVENRA_API_CLIENT_CONCURRENCY_MAX_PER_CLIENT` | 32 | |
| `AVENRA_API_CLIENT_CONCURRENCY_KNOWN_CLIENTS` | empty | |
| `AVENRA_API_CLIENT_CLASSES` | empty | JSON map; invalid JSON fails startup. |
| `AVENRA_CLUSTER_ACTIVATION_ADMISSION_ENABLED` | on | Fails open (with a warning) when `AVENRA_REDIS_URL` is unset. |
| `AVENRA_CLUSTER_ACTIVATION_ADMISSION_REFILL_RPS` | 60 | |
| `AVENRA_PUBLIC_OPERATION_DISPATCHER_EXECUTION_CONCURRENCY` | 16 | Operations the dispatcher runs at once. A larger value clamps to 128; `0` or a non-number keeps the default without a warning. Each execution holds its slot for the whole operation, and freed slots refill on the next 25 ms claim tick, at most 64 per tick. So throughput is roughly this divided by mean execution time, up to 2,560 operations a second. Executions draw on the cluster controller's `avenra_user` Postgres pool, which most other reconcilers share (`POSTGRES_POOL_MAX_CONNECTIONS`, default 25). Raising this adds contention for that pool, and an execution whose claim renewal or final status write can't get a connection within the pool's acquire timeout is abandoned and runs again once its claim goes stale, repeating any work it had already done. |
| `AVENRA_NATIVE_CREATE_POOL_CLAIM_ENABLED` | off | Env master for claiming a ready warm-pool slot when `POST /v1/sandboxes` admits a create, under the `avenra-native-create-pool-claim` flag (falls back off). Every `avenra-cluster-controller` replica must understand claim-carrying create payloads before this is turned on. |
| `AVENRA_NATIVE_CREATE_INLINE_ENABLED` | on | Kill switch for running a native create that won an admission-time warm-pool claim inside the `POST /v1/sandboxes` request instead of leaving it queued for the dispatcher. Read only while admission-time claiming is on; off keeps those creates queued. The API then runs the create executor itself, so it needs the same create settings as `avenra-cluster-controller` (for example `AVENRA_BYOK_VOLUME_ENCRYPTION`). |
| `AVENRA_NATIVE_CREATE_INLINE_WAIT_MS` | `2000` | How long the create request waits for that inline execution before answering. Past the wait the request answers and the execution continues in the background; a malformed value uses the default, and values above 30000 are capped. |
| `AVENRA_REDIS_URL` | unset | |
| `AVENRA_SHARED_VALKEY_URL` | unset | Unset disables the consumer. |
| `AVENRA_GRPC_CLIENT_MAX_CONNECTION_AGE_SECS` | off | |
| `AVENRA_CLUSTER_API_ENDPOINT_DISCOVERY` | `off` | `off` or `kubernetes` (EndpointSlice per-RPC balancing for cluster-api clients; rollout gate). |
| `AVENRA_PUBLIC_CURSOR_SECRET` | **required** | |
| `AVENRA_DATA_PLANE_PUBLIC_URL` | empty | Empty yields relative URLs. |
| `AVENRA_FEDERATED_GRANT_AUDIENCE`, `AVENRA_FEDERATED_IDENTITY_ISSUER`, `AVENRA_FEDERATED_REJECTED_SUBJECTS` | `credential-provider`, `space`, `[]` | |
| `AVENRA_E2B_HOME_WORKDIR_TEMPLATES`, `AVENRA_ECW_HOSTS` | `code-runtime`, none | E2B routes return 404 without hosts. |
| `AVENRA_CLUSTER_ID` | unset | Required for route signing. |
| `AVENRA_CREATE_COMMAND_READY_TIMEOUT_SECS`, `AVENRA_CREATE_DATA_PLANE_READY_TIMEOUT_SECS` | built-in | Create-path waits for command and data-plane readiness. |
| `AVENRA_DATA_PLANE_UPLOAD_ADMISSION` | off | Data plane: admit ECW `POST /files` uploads under the per-replica limits below; rejected uploads get 429 with a jittered `Retry-After`. Accepts `true`/`1`/`yes`/`on` and their negations. |
| `AVENRA_DATA_PLANE_MAX_CONCURRENT_UPLOADS`, `AVENRA_DATA_PLANE_MAX_IN_FLIGHT_UPLOAD_BYTES` | 64, 1 GiB | Per-replica upload slots and byte budget. Each upload reserves its declared `Content-Length` rounded up to a KiB, or its path's body limit (256 MiB, or 1 MiB for JSON) when none is declared, so the 1 GiB default fits four undeclared-length uploads at a time. A reservation is capped at the whole budget: a budget smaller than one upload admits uploads one at a time and is exceeded by that upload. Both must be non-zero. |
| `AVENRA_DATA_PLANE_UPLOAD_ADMISSION_WAIT_MS` | 2000 | How long an upload waits for a slot and its bytes before the 429. |

## Templates and builds

Prefetch requires the catalog to confirm generic-only release resolution from the same database snapshot as its storage locations. Cached visibility alone does not authorize a download. Upgrade the catalog before template-manager; an older catalog cannot acknowledge this restriction, so prefetch pauses until it is upgraded. On-demand pulls retain their existing authorization.

| Variable | Default | Notes |
| --- | --- | --- |
| `AVENRA_TEMPLATE_PREFETCH_ENABLED` | on | |
| `AVENRA_TEMPLATE_PREFETCH_TAGS` | `seed:latest`, `code-runtime:latest` | |
| `AVENRA_TEMPLATE_PREFETCH_VISIBILITY_ENFORCEMENT` | retired | Ignored; prefetch requires confirmed generic visibility. |
| `AVENRA_TEMPLATE_CACHE_BUDGET_BYTES` | 100 GiB | 0 or an invalid value disables cache GC. |
| `AVENRA_TEMPLATE_BUILD_DISK_MIB` | build-manager default | JSON. |
| `AVENRA_TRUSTED_TEMPLATE_STORAGE_BUCKETS` | empty = any bucket | |
| `AVENRA_TEMPLATE_PUBLISH_SYSTEM_SERVICE_ACCOUNTS`, `AVENRA_TEMPLATE_PUBLISH_TENANT_SERVICE_ACCOUNTS` | empty | Startup error when the allowed set is empty. An account in both lists (build-manager) must present the grant issued for that system build by the API or a system-only operator; each grant authorizes one release. |
| `AVENRA_BUILD_AUTH_SYSTEM_SERVICE_ACCOUNTS`, `AVENRA_BUILD_AUTH_TENANT_SERVICE_ACCOUNTS` | empty | |
| `AVENRA_TEMPLATE_PUBLICATION_TOKEN_PATH` | unset | Direct deployments only: dedicated publisher credential file shared by ClusterApi and build-manager. Never reuse the shared node token; incompatible with Kubernetes authentication. See below. |
| `AVENRA_BUILD_MANAGER_ENDPOINT` | `127.0.0.1:9900` | |
| `AVENRA_BUILD_MANAGER_TLS_SERVER_NAME` | from endpoint | |
| `AVENRA_BUILD_MANAGER_AUTH_ALLOWED_SERVICE_ACCOUNTS` | empty | |
| `AVENRA_BUILD_EXPORT_<NAME>` | unset = not exported | Opt a value into recipe `env_refs` as guest variable `NAME`; see [build environment exports](../templates/firecracker/seed/README.md#build-environment-exports). |
| `AVENRA_BUILDER_ECR_REPOSITORIES` | unset | JSON. |
| `AVENRA_UBUNTU_APT_MIRROR` | `https://archive.ubuntu.com/ubuntu` | |

Direct/systemd deployments can publish API-submitted template builds without
Kubernetes projected credentials. Generate a separate random publisher secret
(for example, 32 random bytes encoded as hex), install it in a protected file
readable only by the appropriate service identity, and set
`AVENRA_TEMPLATE_PUBLICATION_TOKEN_PATH` on **ClusterApi and build-manager only**.
The file must contain 32–4096 visible ASCII bytes; surrounding whitespace is
trimmed. Both processes still require their normal shared node bearer and
internal RPC TLS configuration. Other node services must not receive the
publisher secret.

Build-manager attaches the dedicated proof only to template and release
registration. The shared node bearer alone cannot publish. This grants tenant
publication authority and system publication authority **only with the existing
per-build grant** issued by the API. It cannot issue system grants itself, and
does not enable ungranted system builds from the operator CLI. Kubernetes
publication continues using projected tokens; combining Kubernetes auth with
the direct receiver credential fails startup.

Upgrade and configure ClusterApi first, then build-manager, before submitting
builds. Unconfigured receivers retain their existing rejection of static
publication; a sender upgrade alone cannot enable publication. Missing or
malformed configured files fail closed. The receiver loads its credential at
startup; the sender reads it on each registration attempt. To rotate it, pause
build submissions and drain publishing jobs, replace both files atomically,
restart ClusterApi, and resume. Removing the setting from both services disables
direct publication without changing previously published templates. Failed
builds must be resubmitted after configuration is corrected.

For direct `avenra-buildmgr build` calls without `--org-id`, configure
`AVENRA_INTERNAL_API_ENDPOINT` and `AVENRA_CLUSTER_API_TOKEN_PATH` in the CLI's
process. The projected token must identify an account in the system publication
list only. The CLI obtains a grant before submitting the build and sends it with
the job; build-manager's dual-authority credential cannot issue grants. Configure
`AVENRA_BUILD_MANAGER_TOKEN_PATH` separately for permission to submit builds.
Direct operator builds may still use custom system template names. API system
builds remain limited to `seed` and `code-runtime` at the node gateway.

The grant follows the persisted build job through template and release
registration, expires after seven days, and is consumed only when the release
transaction commits. A failed API dispatch revokes only its own grant. Grants
left by failed direct submissions expire and are swept when a new grant is issued.
With no ClusterApi endpoint configured, the CLI retains local-only build behavior;
cluster publication requires the operator configuration above.

Pause system build submissions and drain older system jobs before rolling out
ClusterApi's grant enforcement. Apply the grant-table migration and role
bootstrap first, upgrade build-manager and the node-api gateway, then upgrade
the API and ClusterApi before resuming submissions. The gateway must understand
the new field to forward each build's grant. Resubmit any older queued system
jobs that could not be drained: jobs without a grant cannot publish through a
dual-authority account after the rollout. System-only deployment publishers
retain their existing publication authority.

Tenant publication grants are in a preparation phase. Before dispatching an
organization's build, the API records a grant naming the target template and
organization, sends it with `StartBuild`, and pins it to the accepting builder.
Failed acceptance revokes that submission's grant. Build-manager persists and
forwards the optional grant to template and release registration.

ClusterApi still uses the existing tenant publisher and ownership checks. It
accepts publications both with and without the new field and does not consume
tenant grants yet. Older gateways/builders may drop the field during a rolling
upgrade; older queued jobs and direct tenant CLI submissions still work.
System publication grants remain enforced as before.

Apply the grant-table migration and role bootstrap before deploying the new
API. After that prerequisite, the API, ClusterApi, node-api, and build-manager
can roll in any order. Existing grants expire and are swept on new submissions.
This preparation release does not complete tenant publication hardening.

Before deploying a later enforcement release, finish upgrading every API,
gateway, and builder, then drain or resubmit jobs accepted before that upgrade.
Verify tenant builds carry grants end to end. Rollback after enforcement must
preserve grant-producing and forwarding components until enforcement is removed;
keep the additive schema in place.

### Template balloon device

A template can opt its sandboxes into a Firecracker
[balloon device](https://github.com/firecracker-microvm/firecracker/blob/main/docs/ballooning.md)
through `balloon` in its recipe's `[metadata]`, which the build records in the
template's `metadata.json`:

```toml
[metadata]
memory_mib = 8192
# One of:
balloon = { mode = "inflate", amount_mib = 1024, deflate_on_oom = true }
balloon = { mode = "report" }  # free-page reporting
balloon = { mode = "hint" }    # free-page hinting
```

`stats_polling_interval_s` (default 0, off) enables balloon statistics in any
mode. `amount_mib` and `deflate_on_oom` are required for `inflate` and rejected
otherwise; `amount_mib` must be below the sandbox's memory. Unknown modes and
fields are rejected. Without `balloon`, the VM configuration is unchanged.

The device is rendered only when a sandbox cold-boots from the template build,
which includes creating the template's base snapshot. Sandboxes restored from a
snapshot keep the device that snapshot captured, so changing the setting takes
effect only after rebuilding the template and its base snapshot. The REST build
API does not accept the setting yet.

Hint mode also needs `balloon_hint_timeout_ms` (at most 30000) in the node's
`/avenra/etc/sandbox.toml`. Before each suspend snapshot, a VM whose balloon has
free-page hinting then runs one hinting pass bounded by this time; timeouts and
errors are logged and the snapshot proceeds. Unset, suspend makes no balloon
calls.


## Object storage and snapshots

| Variable | Default | Notes |
| --- | --- | --- |
| `AVENRA_OBJECT_STORE_BUCKET`, `AVENRA_OBJECT_STORE_REGION` | unset | Set together. |
| `AVENRA_OBJECT_STORE_ENDPOINT` | AWS | A custom endpoint also turns on path-style addressing. |
| `AVENRA_OBJECT_STORE_STATIC_ACCESS_KEY_ID`, `AVENRA_OBJECT_STORE_STATIC_SECRET_ACCESS_KEY` | unset | Set both or neither. |
| `AVENRA_OBJECT_STORE_MULTIPART_PART_SIZE_BYTES` | built-in | 8 MiB to 5 GiB. |
| `AVENRA_OBJECT_STORE_MULTIPART_CONCURRENCY` | built-in | 1 to 64 in-flight parts. |
| `AVENRA_OBJECT_STORE_DOWNLOAD_READER_CONCURRENCY`, `AVENRA_OBJECT_STORE_DOWNLOAD_CHUNK_SIZE_BYTES`, `AVENRA_OBJECT_STORE_DOWNLOAD_BUFFER_BUDGET_BYTES` | 8, 32 MiB, 2 GiB | Peak buffered memory is bounded by the budget; size it below the service's memory limit. |
| `AVENRA_OBJECT_STORE_REQUEST_ATTEMPT_TIMEOUT_SECS`, `AVENRA_OBJECT_STORE_BODY_UPLOAD_ATTEMPT_TIMEOUT_SECS`, `AVENRA_OBJECT_STORE_SERVER_SIDE_ATTEMPT_TIMEOUT_SECS` | 10, 60, 30 | 5 to 21600. Bound one attempt of a get, head, or create/abort multipart call; of a put or part upload; and of a complete or copy. Raise the body-upload value with the part size. |
| `AVENRA_OBJECT_STORE_REQUEST_OPERATION_TIMEOUT_SECS`, `AVENRA_OBJECT_STORE_BODY_UPLOAD_OPERATION_TIMEOUT_SECS`, `AVENRA_OBJECT_STORE_SERVER_SIDE_OPERATION_TIMEOUT_SECS` | 3 × attempt + 5 s | 5 to 86400. Bound the same calls across all three SDK attempts. |
| `AVENRA_OBJECT_STORE_POOL_IDLE_TIMEOUT_SECS` | 3 | 1 to 90. How long an idle pooled connection stays eligible for reuse. Keep it below the endpoint's idle close; AWS S3 closes idle HTTPS connections after about 6 s. Applies to object-store transfers; the snapshot-storage sweepers and the kernel fetcher use the 3 s default. |
| `AVENRA_OBJECT_STORE_STATIC_SESSION_TOKEN` | unset | Session token for temporary static credentials. |
| `AVENRA_SNAPSHOT_BUCKET`, `AVENRA_SNAPSHOT_REGION`, `AVENRA_SNAPSHOT_ENDPOINT` | unset | Unset disables snapshot upload. Give the bucket an abort-incomplete-multipart lifecycle rule; uploads interrupted by process death otherwise keep their parts. |
| `AVENRA_VOLUME_STREAM_PUSH_BUFFER_BUDGET_BYTES` | 1 GiB | Volume-manager streams eligible snapshot uploads with this shared part budget; `0` uploads through temporary files. Whole MiB, at least 64 MiB. See [snapshot upload streaming](snapshot-upload-streaming.md). |

## Authentication, TLS and credentials

| Variable | Default | Notes |
| --- | --- | --- |
| `AVENRA_INTERNAL_TOKEN`, `AVENRA_INTERNAL_CONTROL_TOKEN` | **required** where used | Node-to-node bearer tokens. |
| `AVENRA_INTERNAL_SECRET_NAME` | `avenra-internal` | |
| `AVENRA_INTERNAL_API_ENDPOINT` | unset | |
| `AVENRA_INTERNAL_RPC_INSECURE_DEV` | off | TLS is required unless set. |
| `AVENRA_INTERNAL_RPC_TLS_CA`, `AVENRA_INTERNAL_RPC_TLS_CERT`, `AVENRA_INTERNAL_RPC_TLS_KEY` | unset | Cert and key are set together. |
| `AVENRA_K8S_TOKEN_AUTH_ENABLED` | off | |
| `AVENRA_K8S_TOKEN_AUTH_STATIC_FALLBACK_ENABLED` | on | Keeps the shared static token accepted next to ServiceAccount auth. |
| `AVENRA_K8S_TOKEN_AUTH_REVIEW_RATE_PER_SEC` | 50 | |
| `AVENRA_CLUSTER_API_AUTH_ALLOWED_SERVICE_ACCOUNTS`, `AVENRA_NODE_API_AUTH_ALLOWED_SERVICE_ACCOUNTS`, `AVENRA_NODE_API_INTERNAL_AUTH_ALLOWED_SERVICE_ACCOUNTS` | empty | With Kubernetes auth on, an empty list rejects every call. |
| `AVENRA_NODE_API_AUTH_GUEST_BRIDGE_SERVICE_ACCOUNTS`, `AVENRA_NODE_API_AUTH_TEMPLATE_BUILD_SERVICE_ACCOUNTS`, `AVENRA_NODE_API_AUTH_TRUSTED_POLICY_SERVICE_ACCOUNTS`, `AVENRA_NODE_API_AUTH_TRUSTED_WAIT_READY_SERVICE_ACCOUNTS` | empty | |
| `AVENRA_NODE_API_AUTH_TEMPLATE_TRANSFER_SERVICE_ACCOUNTS` | empty | Explicit artifact-transfer accounts for `NodeExportTemplate` and `PullTemplate`; also require normal surface admission. Guest-bridge accounts are always excluded. |
| `AVENRA_NODE_API_NAMESPACE`, `AVENRA_NODE_API_TLS_SERVER_NAME`, `AVENRA_CLUSTER_API_TLS_SERVER_NAME` | namespace chain, from endpoint | |
| `AVENRA_API_ROUTE_ED25519_ISSUER`, `AVENRA_API_ROUTE_ED25519_SIGNING_KEY_ID`, `AVENRA_API_ROUTE_ED25519_SIGNING_KEY_PATH` | unset = no signing | Set all three. |
| `AVENRA_API_ROUTE_ED25519_VERIFICATION_BUNDLE_PATH` | required on sandbox managers | |
| `AVENRA_API_ROUTE_GENERATION_FENCE_ENABLED` | on | |
| `AVENRA_ECW_CREDENTIAL_KEY_PATH` | **required** on api and internal-api | |
| `AVENRA_ECW_CREDENTIAL_PREVIOUS_KEY_PATH` | unset | Set only while rotating. |
| `AVENRA_CREDMGR_ENDPOINT`, `AVENRA_CREDMGR_TLS_CA` | local Unix socket | `https` needs the CA. |
| `AVENRA_CREDMGR_K8S_AUTH_ENABLED` | off | |
| `AVENRA_CREDMGR_K8S_AUTH_NAMESPACE` | pod namespace | Namespace of the ServiceAccounts credmgr accepts. |
| `AVENRA_CREDMGR_AUTH_AUDIENCE` | `avenra-credential-manager` | Token audience credmgr validates. |
| `AVENRA_CREDMGR_AUTH_ALLOWED_SERVICE_ACCOUNTS` | every credmgr client service | Comma-separated allowlist; narrows which ServiceAccounts are admitted. |
| `AVENRA_CREDMGR_FAIL_ON_KEYLESS_AUTHORITY` | off | Crash instead of serving Ready-but-broken when a node-local authority has no key. |
| `AVENRA_CREDMGR_S3_VEND_ROLE_ARN` | unset = no storage exchange | Requires `DATABASE_URL`; a malformed ARN fails startup. See `docs/avenra/design/credential-token-exchange.md`. |
| `AVENRA_CREDMGR_SECRETS_MANAGER_ROLE_ARN` | unset = credential references are not resolvable | IAM role assumed per organization for every Secrets Manager read, with session tag `avenra-org-id=<organization>` and a session policy that allows `GetSecretValue` only on secrets tagged with that organization and `kms:Decrypt` only through Secrets Manager; a malformed ARN fails startup. The role's trust policy admits only the authority's identity, with `sts:AssumeRole` and `sts:TagSession`, and requires the `avenra-org-id` request tag. Its policy allows `GetSecretValue` only when `secretsmanager:ResourceTag/avenra-org-id` equals `${aws:PrincipalTag/avenra-org-id}`, allows `kms:Decrypt` only with `kms:ViaService` `secretsmanager.*.amazonaws.com`, and explicitly denies secrets whose `avenra-org-id` tag is missing or differs from the session's, sessions without the tag, and any KMS use outside Secrets Manager; the explicit denials also hold against same-account resource and key policies. Tag each referenced secret `avenra-org-id=<owning organization ID>`. A secret in another account also needs a resource policy allowing the role, and a secret under a customer-managed KMS key (required across accounts) needs the key policy to allow the role `kms:Decrypt` with `kms:ViaService` `secretsmanager.<region>.amazonaws.com`. |
| `AVENRA_CREDMGR_INTERMEDIATE_CA_ARN` | unset | |
| `AVENRA_CREDMGR_INTERMEDIATE_KEY_BROKER_URL`, `AVENRA_CREDMGR_INTERMEDIATE_KEY_BROKER_TOKEN_FILE`, `AVENRA_CREDMGR_INTERMEDIATE_KEY_BROKER_TLS_CA`, `AVENRA_CREDMGR_INTERMEDIATE_KEY_BROKER_GRANT_SCHEME`, `AVENRA_CREDMGR_INTERMEDIATE_KEY_BROKER_TENANT_NAMESPACE` | unset | Configure as a group. |
| `AVENRA_PLATFORM_KEK_PROVIDER` | none | |
| `AVENRA_BYOK_VOLUME_ENCRYPTION` | off | |
| `AVENRA_EGRESS_PROFILES`, `AVENRA_NET_GATEWAY_KNOWN_EGRESS_DOMAINS` | unset / empty | |

### Template mutation authority

On worker and builder node-api, `DeleteTemplate`, `DeleteTemplateBuild`,
`CreatePool`, and `DeletePool` require the trusted policy service-account
capability. Template-manager's local roles and deletion dependency guards still
apply. Pool target counts have no capacity-based upper bound yet; operators
must budget pool sizes even after caller authorization succeeds.

With Kubernetes authentication disabled, the shared internal token remains the
operator trust boundary and retains mutation authority. When Kubernetes
authentication is enabled, static fallback has no mutation authority. Missing
authentication context is denied.

### Template transfer authority

`AVENRA_NODE_API_AUTH_TEMPLATE_TRANSFER_SERVICE_ACCOUNTS` grants only artifact
transfer, independently of mutation. Configure the controller and node accounts
that perform transfers before upgrading. This is a cluster infrastructure role:
it permits exporting cached private releases from any organization as well as
system templates. Do not grant it to tenant-facing or general admitted services.
Node-api replaces forwarded authority metadata, and template-manager requires
that grant from its authenticated node-api Unix-socket peer. Upgrade node-api
before template-manager; an old forwarder is denied by the new manager.

With Kubernetes authentication disabled, the shared internal token remains the
operator trust boundary and retains transfer authority. When Kubernetes
authentication is enabled, static fallback has no transfer authority.
Missing authentication context is denied. Local root/operator exports remain
available through template-manager's Unix socket.

A peer transfer has two callers, and each needs the grant and admission by
`AVENRA_NODE_API_AUTH_ALLOWED_SERVICE_ACCOUNTS` on the node it calls. The caller
of `PullTemplate` on the target node starts it: the admin API's node
template-pull route, which only the direct substrate serves, or an operator
calling node-api directly. The target's node-api then calls
`NodeExportTemplate` on the source node with its own `AVENRA_NODE_API_TOKEN_PATH`
token. List the account that starts pulls and the node-api account of every
role that can be a target. An empty list disables peer
transfers and node-api logs a startup warning. A deployment without peer
transfers may leave it empty.

Exact-digest export can use the local cache offline. An omitted digest resolves
the requested catalog tag (default `latest`) and exports only that digest; a
missing tag, unavailable catalog, or uncached resolved digest fails without
falling back to a newer local build.

### Policy authorization

Before enabling Kubernetes authentication or upgrading an authenticated worker
or builder, set `AVENRA_NODE_API_AUTH_TRUSTED_POLICY_SERVICE_ACCOUNTS` to the
service accounts that own policy intent. It must be nonempty on both roles or
node-api refuses to start. These accounts must also be admitted by the normal
and internal surface allowlists for the RPCs they call. Policy trust is
independent of the `SpacedWaitReady` trust set. Policy-bearing pool adoption
uses the internal surface, so its caller needs an internal-audience token.

Shared static-token fallback cannot mutate policy when Kubernetes
authentication is enabled; static-only deployments retain that authority.
Policy-bearing cold creation, internal pool adoption, and worker and builder
template updates require a trusted caller. Cold creation and pool adoption also
require policy authority for a nonzero sandbox-policy generation, even without
an override. Requests with no overrides and generation zero remain available to
otherwise admitted callers and inherit the template's policy. Guest-bridge
accounts already have separate method restrictions.

`Local` policy is node-administrator-owned, including normal standalone use and
operator overrides on cluster-managed nodes. It requires a root or Operator Unix
socket peer. Forwarders such as node-api and node-ctl must run under their
dedicated non-root identities: UID 0 is classified as Root before the
service-account lookup.

Forwarded imports carrying `Local` network or auto-lifecycle policy are
rejected, and retrying does not help. To retain `Local` policy, an operator must
review its ownership and perform the import through an authorized local peer;
forwarding never relabels it.

## Database

| Variable | Notes |
| --- | --- |
| `AVENRA_DB_USER_PASSWORD`, `AVENRA_DB_CREDS_PASSWORD` | **required** by services. |
| `AVENRA_DB_BOOTSTRAP_USER_PASSWORD`, `AVENRA_DB_BOOTSTRAP_CREDS_PASSWORD`, `AVENRA_DB_BOOTSTRAP_MIGRATOR_PASSWORD`, `AVENRA_DB_BOOTSTRAP_RO_PASSWORD`, `AVENRA_DB_BOOTSTRAP_SUPERADMIN_PASSWORD` | Required by `bootstrap-roles` unless `--allow-missing-passwords`. |
| `AVENRA_USER_PASSWORD` | Migrator command. |
| `AVENRA_SQLX_QUERY_LOG_LEVEL` | Off by default. |
| `AVENRA_DB_MIGRATE_JOB_DEADLINE_SECONDS` | The migrate Job's `activeDeadlineSeconds`, passed in so retries stop before Kubernetes kills the Job. |
| `AVENRA_DB_APP_LOCK_TIMEOUT` | `bootstrap-roles` application-role `lock_timeout`; same as `--app-lock-timeout`. `0` resets it, unset leaves it unchanged. |
| `AVENRA_API_URL`, `AVENRA_API_TOKEN` | Admin CLI. |

## Observability and feature flags

| Variable | Default | Notes |
| --- | --- | --- |
| `AVENRA_ENV` | unset | Deployment identity for telemetry and node-manager execution admission. Recognized development identities, including `local-*`, permit approved non-default environments; missing or unrecognized identities restrict explicit selection to E0. |
| `AVENRA_SERVICE_NAME`, `AVENRA_VERSION` | unset | Avenra identity tier: outrank `OTEL_SERVICE_NAME`/`OTEL_RESOURCE_ATTRIBUTES` and `DD_*`. |
| `AVENRA_K8S_POD_NAME` | unset | Reported as `k8s.pod.name` on the metrics resource. |
| `AVENRA_LOG_FORMAT` | auto | `json`, `compact`, `pretty` or `text`. |
| `AVENRA_TRACE_FILTER` | follows `RUST_LOG` | Span filter, independent of the log filter. |
| `AVENRA_GIT_SHA` | build identity file | Overrides the build git SHA reported in telemetry. |
| `AVENRA_METRICS_SINK`, `AVENRA_TRACE_EXPORTER` | auto | `datadog` when compiled in, else `otlp` when an endpoint is set, else off. |
| `AVENRA_FLAGD_ENABLED` | off | |

Identity precedence and the full telemetry contract are in
[observability](observability.md).
