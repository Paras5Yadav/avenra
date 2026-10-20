#!/usr/bin/env bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# setup.sh — single-command bring-up of the local Avenra-on-kind stack.
#
# Order of operations (each step is its own idempotent script — re-running
# `setup.sh` skips work that's already done):
#   1. install-prereqs.sh    — kind / kubectl / helm / grpcurl
#   2. start-dind.sh         — side-by-side docker daemon on the 500 GiB rootfs,
#                              required when the host docker's data partition
#                              is full or shared with an unrelated stack
#                              (`AVENRA_USE_DIND=0` skips this step and points
#                              kind at whatever docker the caller has)
#   3. bootstrap.sh          — kind cluster, image builds, helm releases,
#                              port-forwards
#
# The dind daemon needs root once. The rest runs as the calling user.
#
# Usage:
#   sudo bash infra/avenra/kind/scripts/setup.sh
#   sudo AVENRA_USE_DIND=0 bash infra/avenra/kind/scripts/setup.sh  # use host docker
#
# After setup.sh returns, the cluster is reachable as:
#   API:        http://localhost:8080
#   Data-plane: http://localhost:8090
#   UI:         http://localhost:3000
# Login: root@local / AVENRA_CONSOLE_PASSWORD (default: password)
#
# Subsequent shells:
#   source /home/dev/.avenra-kind-env   # (or equivalent created below)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

AVENRA_USE_DIND="${AVENRA_USE_DIND:-1}"
AVENRA_KIND_PARALLEL="${AVENRA_KIND_PARALLEL:-1}"
INSTALL_DIR="${INSTALL_DIR:-/home/dev/.local/bin}"
ENV_FILE="${AVENRA_KIND_ENV_FILE:-/home/dev/.avenra-kind-env}"

log() { printf '[setup] %s\n' "$*"; }

# Setup may need root (for dind) but we want the build-step to run as the
# non-root user (so the source tree's ownership stays consistent and so the
# host docker socket stays untouched). Detect whether we're root or the
# normal `dev` user.
if [[ $EUID -eq 0 ]]; then
  AS_DEV=(sudo -u dev -E env "PATH=$INSTALL_DIR:$PATH")
else
  AS_DEV=(env "PATH=$INSTALL_DIR:$PATH")
fi

# ── 1. install-prereqs.sh ────────────────────────────────────────────────────
log "step 1/3: install prereqs (kind/kubectl/helm/grpcurl)"
"${AS_DEV[@]}" "INSTALL_DIR=$INSTALL_DIR" \
  bash "$SCRIPT_DIR/install-prereqs.sh"

# ── 2. start-dind.sh ─────────────────────────────────────────────────────────
if [[ "$AVENRA_USE_DIND" == "1" ]]; then
  log "step 2/3: start side-by-side dind daemon (AVENRA_USE_DIND=1)"
  if [[ $EUID -ne 0 ]]; then
    sudo bash "$SCRIPT_DIR/start-dind.sh" start
  else
    bash "$SCRIPT_DIR/start-dind.sh" start
  fi
  DOCKER_HOST_FOR_KIND="unix:///run/docker-kind.sock"
else
  log "step 2/3: skipping dind (AVENRA_USE_DIND=0); using host docker"
  DOCKER_HOST_FOR_KIND="${DOCKER_HOST:-}"
fi

# Persist the env so the user can `source` it later.
cat >"$ENV_FILE" <<EOF
# Sourced by any shell that needs to talk to the local Avenra-on-kind cluster
# and the dind daemon that backs it.
export PATH="$INSTALL_DIR:\$PATH"
EOF
if [[ -n "$DOCKER_HOST_FOR_KIND" ]]; then
  echo "export DOCKER_HOST=$DOCKER_HOST_FOR_KIND" >>"$ENV_FILE"
fi
chmod a+r "$ENV_FILE"
log "wrote $ENV_FILE (source it in any new shell)"

# ── 3. bootstrap.sh ──────────────────────────────────────────────────────────
log "step 3/3: bootstrap kind cluster + build images + helm releases"
"${AS_DEV[@]}" "DOCKER_HOST=$DOCKER_HOST_FOR_KIND" \
  "AVENRA_KIND_PARALLEL=$AVENRA_KIND_PARALLEL" \
  bash "$SCRIPT_DIR/bootstrap.sh"

log "all steps complete"
