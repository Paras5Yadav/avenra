{{/*
Shared pod shape for the Kind node-service charts. Each chart renders one
DaemonSet for one node service and calls these with
`(dict "ctx" . "component" "<component>")`, where the component is the
`app.kubernetes.io/component` value production's node DaemonSets carry
(crates/libs/cluster/controller/server/src/node_service_readiness/mod.rs
names node-ctl, volume-manager, sandbox-manager, node-manager, node-api and
template-manager).
*/}}

{{- define "avenra-node-service.role" -}}
{{- $role := required "node.role is required" .Values.node.role -}}
{{- if not (has $role (list "worker" "builder")) -}}
{{- fail (printf "node.role must be worker or builder, got %q" $role) -}}
{{- end -}}
{{- $role -}}
{{- end -}}

{{/* DaemonSet and ServiceAccount name: avenra-node-manager, avenra-builder-node-manager, ... */}}
{{- define "avenra-node-service.name" -}}
{{- printf "%s-%s" .ctx.Release.Name .component -}}
{{- end -}}

{{- define "avenra-node-service.selectorLabels" -}}
app.kubernetes.io/name: avenra-{{ include "avenra-node-service.role" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
`app.kubernetes.io/name` is the node role (avenra-worker / avenra-builder) on
every node-service pod. The API's pod reflector maps that label to a Avenra
node role but watches only `app.kubernetes.io/component=node-api`
(crates/libs/api/src/controllers/substrate/kubernetes/caches.rs), so only the
node-api pods become node endpoints. `space.pplx.net/node-service` lets the
harness and diagnostics select every node-service pod at once.
*/}}
{{- define "avenra-node-service.labels" -}}
{{ include "avenra-node-service.selectorLabels" . }}
app.kubernetes.io/part-of: avenra-node
space.pplx.net/node-service: {{ .component }}
team: {{ .ctx.Values.node.datadogTeam | quote }}
{{- end -}}

{{- define "avenra-node-service.nodeSelector" -}}
{{- $role := include "avenra-node-service.role" .ctx -}}
nodeSelector:
  space.pplx.net/node-pool: {{ index .ctx.Values.node.pools $role | quote }}
{{- end -}}

{{/*
Node services carry the built-in system-node-critical class, as deployments
give their node-service DaemonSets (infra/avenra/kind/manifests/priorityclasses.yaml
has the cluster's other classes): a node-service pod that cannot fit preempts
lower-priority pods rather than leaving the node without it, and the kubelet's
node-pressure eviction ranks it behind lower-priority pods that exceed their
requests.
*/}}
{{- define "avenra-node-service.priorityClass" -}}
priorityClassName: system-node-critical
{{- end -}}

{{- define "avenra-node-service.image" -}}
image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
imagePullPolicy: {{ .Values.image.pullPolicy }}
{{- end -}}

{{/*
Identity every node service reports. AVENRA_NODE_ID is the raw kube node name
(fieldRef spec.nodeName); services and the cluster API derive the node UUID
from (name, role) via avenra_k8s_node_id, so nothing hand-rolls an id. The
node and pod names are the observability identity
(crates/libs/observability/src/identity.rs).
*/}}
{{- define "avenra-node-service.env.identity" -}}
- name: AVENRA_NODE_ID
  valueFrom:
    fieldRef:
      fieldPath: spec.nodeName
- name: AVENRA_NODE_ROLE
  value: {{ include "avenra-node-service.role" . | quote }}
- name: AVENRA_CLUSTER_ID
  value: {{ .Values.clusterId | quote }}
- name: KUBE_NODE_NAME
  valueFrom:
    fieldRef:
      fieldPath: spec.nodeName
- name: AVENRA_K8S_NODE_NAME
  valueFrom:
    fieldRef:
      fieldPath: spec.nodeName
- name: AVENRA_K8S_POD_NAME
  valueFrom:
    fieldRef:
      fieldPath: metadata.name
{{ include "avenra-kind-telemetry.env" . }}
{{- end -}}

{{/*
ClusterApi client wiring: the HTTPS endpoint plus the pod's own projected
ServiceAccount token (audience avenra-cluster-api), which internal-api
authenticates by TokenReview against its allowlist
(AVENRA_CLUSTER_API_AUTH_ALLOWED_SERVICE_ACCOUNTS in avenra-internal-api-local),
and the internal RPC trust (env.rpcTrust). Pair with the cluster-api-token and
rpc-ca volumes below.
*/}}
{{- define "avenra-node-service.env.clusterApi" -}}
- name: AVENRA_INTERNAL_API_ENDPOINT
  value: {{ .Values.node.internalApiEndpoint | quote }}
- name: AVENRA_CLUSTER_API_TOKEN_PATH
  value: /var/run/avenra-publication/cluster-api.token
{{ include "avenra-node-service.env.rpcTrust" . }}
{{- end -}}

{{/*
Internal RPC TLS trust for a ClusterApi, NodeApi or BuildManager TCP caller
(docs/internal-rpc-tls.md): the cluster's CA bundle
(internal-rpc-tls.sh's avenra-internal-rpc-ca, mounted where the EKS module
mounts it), and the shared NodeApi identity every node-api presents, since
callers dial node-api by node IP and its leaf carries no IP SANs. ClusterApi
needs no override: the endpoint host is the leaf's own name.
*/}}
{{- define "avenra-node-service.env.rpcTrust" -}}
- name: AVENRA_INTERNAL_RPC_TLS_CA
  value: /etc/avenra-rpc-ca/ca.crt
- name: AVENRA_NODE_API_TLS_SERVER_NAME
  value: avenra-node-api
{{- end -}}

{{/*
A TLS listener's own identity (internal-rpc-tls.sh's avenra-<listener>-tls
Secret, mounted where the EKS module mounts it). Pair with volume.rpcIdentity.
*/}}
{{- define "avenra-node-service.env.rpcIdentity" -}}
- name: AVENRA_INTERNAL_RPC_TLS_CERT
  value: /etc/avenra-rpc-tls/tls.crt
- name: AVENRA_INTERNAL_RPC_TLS_KEY
  value: /etc/avenra-rpc-tls/tls.key
{{- end -}}

{{/*
The control-plane credential-manager authority, for the node credential
agent and node-manager's direct sandbox volume DEK calls: its HTTPS endpoint
(AVENRA_CREDMGR_ENDPOINT, crates/libs/credential/mgr/client/src/endpoint.rs),
whose host must be one of the certificate's names (the client has no
server-name override); the authority's self-signed certificate as the trust
anchor (AVENRA_CREDMGR_TLS_CA, as on EKS: credmgr_authority_tls.tf); and the
pod's projected ServiceAccount token at the client's default path
(/var/run/avenra-auth/credmgr.token, client/src/token.rs) with the
authority's audience. The authority maps the ServiceAccount to its client
service by TokenReview. Pair with the credmgr-token volume below.
*/}}
{{- define "avenra-node-service.env.credmgrAuthority" -}}
- name: AVENRA_CREDMGR_ENDPOINT
  value: {{ .Values.node.credmgrAuthorityEndpoint | quote }}
- name: AVENRA_CREDMGR_TLS_CA
  value: /var/run/avenra-auth/credmgr-ca.crt
{{- end -}}

{{- define "avenra-node-service.env.internalToken" -}}
- name: AVENRA_INTERNAL_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ .Values.node.internalSecretName }}
      key: AVENRA_INTERNAL_TOKEN
{{- end -}}

{{- define "avenra-node-service.env.diskAdmission" -}}
- name: AVENRA_NODE_DISK_ADMISSION_MIN_AVAILABLE_BYTES
  value: {{ .Values.node.diskAdmission.minAvailableBytes | quote }}
- name: AVENRA_NODE_DISK_ADMISSION_ALLOCATION_RESERVATION_BYTES
  value: {{ .Values.node.diskAdmission.allocationReservationBytes | quote }}
{{- end -}}

{{- define "avenra-node-service.env.objectStoreCredentials" -}}
- name: AVENRA_OBJECT_STORE_STATIC_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ .Values.node.objectStore.credentialsSecretName }}
      key: AVENRA_OBJECT_STORE_STATIC_ACCESS_KEY_ID
- name: AVENRA_OBJECT_STORE_STATIC_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.node.objectStore.credentialsSecretName }}
      key: AVENRA_OBJECT_STORE_STATIC_SECRET_ACCESS_KEY
{{- end -}}

{{- define "avenra-node-service.env.routeVerification" -}}
- name: AVENRA_API_ROUTE_ED25519_VERIFICATION_BUNDLE_PATH
  value: /var/run/avenra-route-verification/verification-bundle.json
- name: AVENRA_API_ROUTE_GENERATION_FENCE_ENABLED
  value: {{ .Values.node.routeVerification.generationFenceEnabled | quote }}
{{- end -}}

{{/*
Instance-local health socket. Each binary binds it before waiting for its
service mutex and answers `readiness` (SERVING while passive, NOT_SERVING
while taking over, then the production socket's verdict) and `liveness`
(crates/libs/common/svc/src/local_health.rs). It must be on a pod-private
mount, so a surging successor cannot replace its predecessor's socket.
*/}}
{{- define "avenra-node-service.healthSocket" -}}
/run/health/{{ .component }}.sock
{{- end -}}

{{- define "avenra-node-service.probes" -}}
{{- $socket := include "avenra-node-service.healthSocket" . -}}
{{- $startupSeconds := .startupSeconds | default 300 | int -}}
# Liveness answers as soon as the health socket is bound, before the service
# mutex and startup recovery, so the startup budget only covers reaching the
# bind; readiness gates traffic and the rollout.
startupProbe:
  exec:
    command: ["/opt/avenra/bin/grpc_health_probe", "-addr=unix://{{ $socket }}", "-service=liveness", "-connect-timeout=2s", "-rpc-timeout=2s"]
  periodSeconds: 5
  failureThreshold: {{ div $startupSeconds 5 }}
readinessProbe:
  exec:
    command: ["/opt/avenra/bin/grpc_health_probe", "-addr=unix://{{ $socket }}", "-service=readiness", "-connect-timeout=2s", "-rpc-timeout=2s"]
  periodSeconds: 5
  failureThreshold: 3
livenessProbe:
  exec:
    command: ["/opt/avenra/bin/grpc_health_probe", "-addr=unix://{{ $socket }}", "-service=liveness", "-connect-timeout=3s", "-rpc-timeout=3s"]
  periodSeconds: 20
  failureThreshold: 3
{{- end -}}

{{/*
Volume mounts. /avenra/run is the node's hostPath: the services' Unix
sockets, service mutexes and dependency-drain gates live there, and the
containerd shim and the host runtime-supervisor reach the same paths from the
node. /avenra/cache is the node's btrfs cache; HostToContainer receives the
node's mount (and a remount by avenra-cache.service) without sending this
pod's own mounts back to the node.
*/}}
{{- define "avenra-node-service.mount.run" -}}
- name: run
  mountPath: /avenra/run
{{- end -}}

{{- define "avenra-node-service.mount.cache" -}}
- name: cache
  mountPath: /avenra/cache
  mountPropagation: {{ .propagation | default "HostToContainer" }}
  {{- if .readOnly }}
  readOnly: true
  {{- end }}
{{- end -}}

{{- define "avenra-node-service.mount.private" -}}
- name: health
  mountPath: /run/health
- name: tmp
  mountPath: /tmp
{{- end -}}

{{- define "avenra-node-service.mount.clusterApiToken" -}}
- name: cluster-api-token
  mountPath: /var/run/avenra-publication
  readOnly: true
{{- end -}}

{{- define "avenra-node-service.mount.credmgrToken" -}}
- name: credmgr-token
  mountPath: /var/run/avenra-auth
  readOnly: true
{{- end -}}

{{- define "avenra-node-service.mount.rpcCa" -}}
- name: rpc-ca
  mountPath: /etc/avenra-rpc-ca
  readOnly: true
{{- end -}}

{{- define "avenra-node-service.mount.rpcIdentity" -}}
- name: rpc-identity
  mountPath: /etc/avenra-rpc-tls
  readOnly: true
{{- end -}}

{{- define "avenra-node-service.mount.routeVerification" -}}
- name: route-verification-bundle
  mountPath: /var/run/avenra-route-verification
  readOnly: true
{{- end -}}

{{- define "avenra-node-service.volume.run" -}}
# DirectoryOrCreate: the node's avenra-layout.service creates the skeleton
# before kubelet starts; a pod scheduled first still starts and its service
# fails, and restarts, until the directory it needs exists.
- name: run
  hostPath:
    path: /avenra/run
    type: DirectoryOrCreate
{{- end -}}

{{- define "avenra-node-service.volume.cache" -}}
# Mounted on the node by avenra-cache.service before kubelet starts.
- name: cache
  hostPath:
    path: /avenra/cache
    type: Directory
{{- end -}}

{{- define "avenra-node-service.volume.private" -}}
- name: health
  emptyDir:
    medium: Memory
    sizeLimit: 1Mi
- name: tmp
  emptyDir: {}
{{- end -}}

{{- define "avenra-node-service.volume.credmgrToken" -}}
- name: credmgr-token
  projected:
    defaultMode: 0444
    sources:
      - serviceAccountToken:
          path: credmgr.token
          audience: avenra-credential-manager
          # The node services' deployed lifetime, as for the ClusterApi
          # token below.
          expirationSeconds: 600
      - secret:
          name: avenra-credmgr-authority-trust
          items:
            - key: ca.crt
              path: credmgr-ca.crt
{{- end -}}

{{- define "avenra-node-service.volume.rpcCa" -}}
- name: rpc-ca
  secret:
    secretName: avenra-internal-rpc-ca
    defaultMode: 0444
{{- end -}}

{{/*
`secret` is the listener's Secret. The key is readable only by the pod's
group: a non-root listener sets fsGroup to its own GID (pod securityContext),
a root listener reads it as owner.
*/}}
{{- define "avenra-node-service.volume.rpcIdentity" -}}
- name: rpc-identity
  secret:
    secretName: {{ .secret }}
    defaultMode: 0440
{{- end -}}

{{/*
The 600 s lifetime is the node services' deployed one; the kubelet refreshes
the file at 80% of it and the client rereads it (TOKEN_FILE_CACHE_TTL,
bearer_token.rs).
*/}}
{{- define "avenra-node-service.volume.clusterApiToken" -}}
- name: cluster-api-token
  projected:
    defaultMode: 0444
    sources:
      - serviceAccountToken:
          path: cluster-api.token
          audience: avenra-cluster-api
          expirationSeconds: 600
{{- end -}}

{{- define "avenra-node-service.volume.routeVerification" -}}
- name: route-verification-bundle
  secret:
    secretName: {{ .Values.node.routeVerification.secretName }}
    defaultMode: 0444
    items:
      - key: {{ .Values.node.routeVerification.bundleKey }}
        path: verification-bundle.json
{{- end -}}

{{/*
An unprivileged, read-only container: no capabilities, no escalation. Every
service that only needs file access and Unix sockets runs this way, as the
service crates describe their production shape (e.g. "runAsNonRoot with no
added capabilities" in crates/libs/template/mgr/src/btrfs_via_nodemgr.rs and
crates/libs/sbx/mgr/server/src/btrfs_via_nodemgr.rs).
*/}}
{{- define "avenra-node-service.restrictedContainer" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: ["ALL"]
{{- end -}}

{{/*
Init container that creates the service's own /avenra/run directory with the
ownership its socket peers expect, as production's per-service chart inits
do for the directories the node image does not bake (packer 07-sysctl.sh
creates node-manager's, sandbox-manager's, credential-manager's and
template-manager's). Root with only CHOWN and FOWNER: `install -d -o -g -m`
on a root-owned parent.
*/}}
{{- define "avenra-node-service.prepareRunDir" -}}
- name: prepare-run-dir
  {{- include "avenra-node-service.image" .ctx | nindent 2 }}
  command:
    - install
    - -d
    - -m
    - {{ .mode | quote }}
    - -o
    - {{ .uid | quote }}
    - -g
    - {{ .gid | quote }}
    - {{ .path }}
  securityContext:
    runAsNonRoot: false
    runAsUser: 0
    runAsGroup: 0
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    capabilities:
      drop: ["ALL"]
      add: ["CHOWN", "FOWNER"]
  volumeMounts:
    {{- include "avenra-node-service.mount.run" .ctx | nindent 4 }}
{{- end -}}

{{/*
Rolling-update strategy. `surge` starts the replacement beside the old pod:
for services that hold a node-wide service mutex (/avenra/run/<svc>/service.mutex)
the successor waits passively, reports ready while passive, and takes over
when the predecessor drains and releases the lock, with its sockets replaced
by rename (crates/libs/common/svc/src/service_mutex.rs, bootstrap.rs).
`deleteFirst` stops the old pod before the new one starts, for services that
cannot run twice on a node.
*/}}
{{- define "avenra-node-service.updateStrategy" -}}
updateStrategy:
  type: RollingUpdate
  rollingUpdate:
    {{- if eq .strategy "surge" }}
    maxSurge: 1
    maxUnavailable: 0
    {{- else if eq .strategy "deleteFirst" }}
    maxSurge: 0
    maxUnavailable: 1
    {{- else }}
    {{- fail (printf "unknown update strategy %q" .strategy) }}
    {{- end }}
{{- end -}}

{{/*
Host network for a service that makes node-bound ClusterApi calls.
internal-api binds every node-bound RPC (PublishNodeState and the other
node_id-carrying calls, quarantine recovery plans, artifact backups) to the
node's registered address — the Kubernetes Node's InternalIP, which
cluster-controller's node sync writes — and refuses a call from any other
source address whatever the credential or transport
(validate_origin_remote_addr in crates/libs/internal/api/server/src/lib.rs;
internal RPC TLS is server-auth only). So each such caller must reach it from
the node's own IP. ClusterFirstWithHostNet keeps in-cluster DNS.
*/}}
{{- define "avenra-node-service.nodeBoundNetwork" -}}
hostNetwork: true
dnsPolicy: ClusterFirstWithHostNet
{{- end -}}

{{- define "avenra-node-service.podAnnotations" -}}
ad.datadoghq.com/tags: '{"team":"{{ .ctx.Values.node.datadogTeam }}"}'
{{ include "avenra-kind-telemetry.logAnnotations" .ctx }}
{{- end -}}

{{/*
Lets the service's ServiceAccount create TokenReviews, which a listener that
authenticates its callers' projected tokens issues for each one
(crates/libs/common/svc/src/auth/k8s.rs, auth/build/k8s.rs). Nothing else.
*/}}
{{- define "avenra-node-service.tokenReviewRbac" -}}
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {{ include "avenra-node-service.name" . }}-token-review
  labels:
    {{- include "avenra-node-service.labels" . | nindent 4 }}
rules:
  - apiGroups: ["authentication.k8s.io"]
    resources: ["tokenreviews"]
    verbs: ["create"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: {{ include "avenra-node-service.name" . }}-token-review
  labels:
    {{- include "avenra-node-service.labels" . | nindent 4 }}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: {{ include "avenra-node-service.name" . }}-token-review
subjects:
  - kind: ServiceAccount
    name: {{ include "avenra-node-service.name" . }}
    namespace: {{ .ctx.Release.Namespace }}
{{- end -}}

{{- define "avenra-node-service.serviceAccount" -}}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "avenra-node-service.name" . }}
  namespace: {{ .ctx.Release.Namespace }}
  labels:
    {{- include "avenra-node-service.labels" . | nindent 4 }}
{{- end -}}
