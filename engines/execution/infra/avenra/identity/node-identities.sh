#!/usr/bin/env bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# Canonical Avenra node runtime identities.
#
# This file is sourced by direct-node deploy tooling, Packer provisioning, the
# worker image build, and the EKS Helm overlay renderer.

AVENRA_GROUP_SPECS="
avenra-nodeclients:950
avenra-sbxclients:951
avenra-cache-rw:952
avenra-run-rw:953
avenra-tmplclients:954
avenra-tmplmgr:955
avenra-sidecarclients:956
avenra-buildclients:957
avenra-nodectlclients:958
avenra-nodectl:959
avenra-sbxmgr:960
avenra-nodeapi:961
avenra-rtmgrclients:962
avenra-rtmgr:963
avenra-volmgr:964
avenra-volcredclients:965
"

# Groups managed as supplementary access groups for local operators/services.
# Service primary groups are intentionally excluded.
AVENRA_MANAGED_GROUPS="avenra-volcredclients avenra-nodeclients avenra-sbxclients avenra-cache-rw avenra-run-rw avenra-tmplclients avenra-sidecarclients avenra-buildclients avenra-nodectlclients avenra-rtmgrclients"

# Local deploy/debug accounts need only client socket groups. Direct cache/run
# writes stay scoped to services so RPC authorization remains the control point.
AVENRA_OPERATOR_GROUPS="avenra-nodeclients avenra-sbxclients avenra-tmplclients avenra-nodectlclients avenra-rtmgrclients"

AVENRA_VOLMGR_USER=avenra-volmgr
AVENRA_VOLMGR_UID=964
AVENRA_VOLMGR_GROUP=avenra-volmgr
AVENRA_VOLMGR_GID=964

AVENRA_SBXMGR_USER=avenra-sbxmgr
AVENRA_SBXMGR_UID=950
AVENRA_SBXMGR_GROUP=avenra-sbxmgr
AVENRA_SBXMGR_GID=960

AVENRA_TMPLMGR_USER=avenra-tmplmgr
AVENRA_TMPLMGR_UID=955
AVENRA_TMPLMGR_GROUP=avenra-tmplmgr
AVENRA_TMPLMGR_GID=955

AVENRA_NODEAPI_USER=avenra-nodeapi
AVENRA_NODEAPI_UID=954
AVENRA_NODEAPI_GROUP=avenra-nodeapi
AVENRA_NODEAPI_GID=961

AVENRA_NODECTL_USER=avenra-nodectl
AVENRA_NODECTL_UID=959
AVENRA_NODECTL_GROUP=avenra-nodectl
AVENRA_NODECTL_GID=959

AVENRA_KUBERNETES_NODE_CONTROLLER_USER=avenra-k8s-node-controller
AVENRA_KUBERNETES_NODE_CONTROLLER_UID=958
AVENRA_KUBERNETES_NODE_CONTROLLER_GROUP=avenra-nodectlclients
AVENRA_KUBERNETES_NODE_CONTROLLER_GID=958

AVENRA_RTMGR_USER=avenra-rtmgr
AVENRA_RTMGR_UID=963
AVENRA_RTMGR_GROUP=avenra-rtmgr
AVENRA_RTMGR_GID=963

# alias:name:uid:primary_group. Alias names are used by generators that need
# stable Rust/Python identifiers while shell consumers use the expanded values.
AVENRA_USER_SPECS="
AVENRA_VOLMGR:$AVENRA_VOLMGR_USER:$AVENRA_VOLMGR_UID:$AVENRA_VOLMGR_GROUP
AVENRA_SBXMGR:$AVENRA_SBXMGR_USER:$AVENRA_SBXMGR_UID:$AVENRA_SBXMGR_GROUP
AVENRA_TMPLMGR:$AVENRA_TMPLMGR_USER:$AVENRA_TMPLMGR_UID:$AVENRA_TMPLMGR_GROUP
AVENRA_NODEAPI:$AVENRA_NODEAPI_USER:$AVENRA_NODEAPI_UID:$AVENRA_NODEAPI_GROUP
AVENRA_NODECTL:$AVENRA_NODECTL_USER:$AVENRA_NODECTL_UID:$AVENRA_NODECTL_GROUP
AVENRA_K8S_NODE_CONTROLLER:$AVENRA_KUBERNETES_NODE_CONTROLLER_USER:$AVENRA_KUBERNETES_NODE_CONTROLLER_UID:$AVENRA_KUBERNETES_NODE_CONTROLLER_GROUP
AVENRA_RTMGR:$AVENRA_RTMGR_USER:$AVENRA_RTMGR_UID:$AVENRA_RTMGR_GROUP
"

AVENRA_SANDBOX_MANAGER_GROUPS="avenra-nodeclients avenra-sbxclients avenra-tmplclients avenra-cache-rw avenra-sidecarclients avenra-nodectlclients"
AVENRA_TEMPLATE_MANAGER_GROUPS="avenra-nodeclients avenra-sbxclients avenra-tmplclients avenra-cache-rw avenra-nodectlclients"
AVENRA_CREDENTIAL_MANAGER_GROUPS="avenra-sbxclients avenra-volcredclients"
# sbxclients is needed to publish volume-manager's own client socket; it does
# not authorize this UID on the standard credential-manager listener.
AVENRA_VOLUME_MANAGER_GROUPS="avenra-sbxclients avenra-cache-rw avenra-volcredclients avenra-nodeclients"
# The runtime manager reads the world-readable vendor and cache layouts. It
# needs its own socket group so it can hand that group to its socket, and
# node-manager's client group so it can ask node-manager to publish installs.
AVENRA_RUNTIME_MANAGER_GROUPS="avenra-rtmgrclients avenra-nodeclients"
AVENRA_NODECTL_GROUPS="avenra-nodeclients avenra-sbxclients avenra-tmplclients avenra-nodectlclients"
AVENRA_NODEAPI_WORKER_GROUPS="avenra-nodeclients avenra-sbxclients avenra-tmplclients avenra-nodectlclients"
AVENRA_NODEAPI_BUILDER_GROUPS="$AVENRA_NODEAPI_WORKER_GROUPS avenra-buildclients"
AVENRA_NODEAPI_CLUSTER_GROUPS="avenra-sbxclients avenra-tmplclients"
AVENRA_BUILD_MANAGER_GROUPS="avenra-cache-rw avenra-run-rw avenra-buildclients"
AVENRA_KUBERNETES_NODE_CONTROLLER_GROUPS="avenra-tmplclients avenra-tmplmgr"

avenra_primary_group_for_user() {
  local lookup="$1" alias user uid group
  while IFS=: read -r alias user uid group; do
    [[ -z "$user" ]] && continue
    if [[ "$user" == "$lookup" ]]; then
      echo "$group"
      return 0
    fi
  done <<< "$AVENRA_USER_SPECS"
  echo ""
}

avenra_uid_for_user() {
  local lookup="$1" alias user uid group
  while IFS=: read -r alias user uid group; do
    [[ -z "$user" ]] && continue
    if [[ "$user" == "$lookup" ]]; then
      echo "$uid"
      return 0
    fi
  done <<< "$AVENRA_USER_SPECS"
  echo ""
}

avenra_expected_gid_for_group() {
  local group="$1" spec
  for spec in $AVENRA_GROUP_SPECS; do
    if [[ "${spec%%:*}" == "$group" ]]; then
      echo "${spec##*:}"
      return 0
    fi
  done
  return 1
}

avenra_is_service_primary_group() {
  local lookup="$1" alias user uid group managed_group
  while IFS=: read -r alias user uid group; do
    [[ -z "$group" ]] && continue
    [[ "$group" == "$lookup" ]] || continue
    for managed_group in $AVENRA_MANAGED_GROUPS; do
      [[ "$managed_group" == "$group" ]] && return 1
    done
    return 0
  done <<< "$AVENRA_USER_SPECS"
  return 1
}
