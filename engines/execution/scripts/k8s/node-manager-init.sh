#!/bin/bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# node-manager-init.sh — Copies service binaries to the host and
# initializes the node-manager state file.
#
# Runs as a privileged init container with /space mounted from the host.
# The AMI provides users, groups, directory skeleton, and permissions
# (see packer 07-sysctl.sh). This script only handles what changes
# with each container image release.
set -euo pipefail

. /opt/avenra/etc/node-identities.sh

now_ms() {
  date +%s%3N
}

elapsed_ms() {
  local started_ms="$1"
  echo "$(($(now_ms) - started_ms))"
}

log_init() {
  echo "node-manager-init: $*"
}

. /opt/avenra/bin/node-manager-runtime-supervisor.sh

render_runtime_supervisor_unit() {
  cat <<'EOF'
[Unit]
Description=Avenra Runtime Supervisor
Wants=network-online.target
After=network-online.target avenra-cgroup.service
Requires=avenra-cgroup.service

[Service]
Type=simple
EnvironmentFile=-/avenra/config/space.env
ExecStart=/avenra/bin/avenra-runtime-supervisor-svc
Restart=always
RestartSec=1
KillMode=process
Delegate=yes
TasksMax=infinity
CPUWeight=1000
IOWeight=1000

[Install]
WantedBy=multi-user.target
EOF
}

AVENRA_SBXCLIENTS_GID="$(avenra_expected_gid_for_group avenra-sbxclients)"
AVENRA_TMPLCLIENTS_GID="$(avenra_expected_gid_for_group avenra-tmplclients)"

# Stage host binaries under a release directory keyed by the worker image tag,
# then atomically switch stable symlinks in /avenra/bin. This avoids overwriting
# in-use executables during rolling updates.
IMAGE_TAG="${AVENRA_IMAGE_TAG:?AVENRA_IMAGE_TAG must be set}"
RELEASE_DIR="/avenra/bin/releases/${IMAGE_TAG}"
RUNTIME_SUPERVISOR_RESTART_REQUIRED="/avenra/run/runtime-supervisor/restart-required"
RUNTIME_SUPERVISOR_HEALTH_SOCKET="/avenra/run/runtime-supervisor/runtime-supervisor.sock"
RUNTIME_SUPERVISOR_COMPATIBILITY="/opt/avenra/etc/avenra-runtime-supervisor-compatibility"
SBXMGR_DRAIN_ROOT="/avenra/run/dependency-drain"
SBXMGR_DRAIN_DIR="${SBXMGR_DRAIN_ROOT}/sandbox-manager"
SBXMGR_DRAIN_CAPABILITY="${SBXMGR_DRAIN_DIR}/capability-v1.lock"
SBXMGR_DRAIN_ADMISSION="${SBXMGR_DRAIN_DIR}/admission-v1.lock"
SBXMGR_DRAIN_WORK="${SBXMGR_DRAIN_DIR}/work-v1.lock"
init_started_ms="$(now_ms)"

mkdir -p /avenra/bin
mkdir -p "$RELEASE_DIR"
install -d -m 0755 -o 0 -g 0 /avenra/etc
if [[ -f /etc/avenra/build-git-sha ]]; then
  install -m 0644 /etc/avenra/build-git-sha /avenra/etc/build-git-sha
else
  printf '%s\n' unknown > /avenra/etc/build-git-sha
  chmod 0644 /avenra/etc/build-git-sha
fi
install -d -m 0755 -o 0 -g 0 /avenra/run/runtime-supervisor
install -d -m 0750 -o 0 -g "$AVENRA_SBXCLIENTS_GID" \
  "$SBXMGR_DRAIN_ROOT" "$SBXMGR_DRAIN_DIR"
for gate in "$SBXMGR_DRAIN_CAPABILITY" "$SBXMGR_DRAIN_ADMISSION" "$SBXMGR_DRAIN_WORK"; do
  if [[ -L "$gate" || ( -e "$gate" && ! -f "$gate" ) ]]; then
    echo "ERROR: dependency-drain gate is not a regular file: $gate" >&2
    exit 1
  fi
  touch "$gate"
  chown "0:$AVENRA_SBXCLIENTS_GID" "$gate"
  chmod 0660 "$gate"
done
log_init "start image_tag=${IMAGE_TAG} release_dir=${RELEASE_DIR}"

if ! read -r runtime_supervisor_compatibility <"$RUNTIME_SUPERVISOR_COMPATIBILITY" \
  || [[ ! "$runtime_supervisor_compatibility" =~ ^[0-9a-f]{64}$ ]]; then
  echo "ERROR: invalid runtime-supervisor compatibility identity" >&2
  exit 1
fi

runtime_supervisor_binary_changed=true
old_runtime_supervisor_target="$(readlink -f /avenra/bin/avenra-runtime-supervisor-svc 2>/dev/null || true)"
old_runtime_supervisor_compatibility="${old_runtime_supervisor_target%/*}/avenra-runtime-supervisor-compatibility"
if [[ -n "$old_runtime_supervisor_target" && -x "$old_runtime_supervisor_target" ]] \
  && cmp -s "$RUNTIME_SUPERVISOR_COMPATIBILITY" "$old_runtime_supervisor_compatibility"; then
  runtime_supervisor_binary_changed=false
fi
if [[ "$runtime_supervisor_binary_changed" == "true" ]]; then
  touch "$RUNTIME_SUPERVISOR_RESTART_REQUIRED"
fi

installed_count=0
reused_count=0
skipped_count=0

for bin in avenra-runtime-supervisor-svc avenra-nodemgr-svc avenra-sbxmgr-svc avenra-avenrad-host-svc avenra-netgtw-svc avenra-sbxmgr; do
  bin_started_ms="$(now_ms)"
  src="/opt/avenra/bin/$bin"
  dst="${RELEASE_DIR}/${bin}"

  if [[ ! -x "$src" ]]; then
    if [[ "$bin" == "avenra-sbxmgr" ]]; then
      echo "WARNING: optional binary $bin not found or not executable at $src -- skipping" >&2
      skipped_count=$((skipped_count + 1))
      log_init "binary bin=${bin} action=skipped elapsed_ms=$(elapsed_ms "$bin_started_ms")"
      continue
    fi
    echo "ERROR: required binary $bin not found or not executable at $src" >&2
    exit 1
  fi

  current_target="$(readlink -f "/avenra/bin/$bin" 2>/dev/null || true)"
  force_install=false
  if [[ "$bin" == "avenra-runtime-supervisor-svc" && "$runtime_supervisor_binary_changed" == "true" ]]; then
    force_install=true
  fi
  if [[ "$force_install" == "false" && -n "$current_target" && -x "$current_target" ]] \
    && cmp -s "$src" "$current_target"; then
    if [[ "$current_target" != "$dst" ]]; then
      ln -sfn "$current_target" "$dst"
    fi
    reused_count=$((reused_count + 1))
    action=reused
  else
    install -m 0755 "$src" "$dst"
    installed_count=$((installed_count + 1))
    action=installed
  fi

  if [[ "$bin" == "avenra-runtime-supervisor-svc" && "$action" == "installed" ]]; then
    install -m 0644 "$RUNTIME_SUPERVISOR_COMPATIBILITY" \
      "${RELEASE_DIR}/avenra-runtime-supervisor-compatibility"
  fi

  ln -sfn "$dst" "/avenra/bin/$bin"
  log_init "binary bin=${bin} action=${action} elapsed_ms=$(elapsed_ms "$bin_started_ms")"
done

dirs_started_ms="$(now_ms)"
install -d -m 0755 -o 0 -g 0 /avenra/run/node-manager
install -d -m 0755 -o 0 -g 0 /avenra/run/runtime-supervisor
install -d -m 0750 -o "$AVENRA_SBXMGR_UID" -g "$AVENRA_SBXCLIENTS_GID" /avenra/run/sandbox-manager /avenra/run/credential-manager
install -d -m 0755 -o "$AVENRA_SBXMGR_UID" -g "$AVENRA_SBXMGR_GID" /avenra/run/sandbox-host-control
install -d -m 0750 -o "$AVENRA_TMPLMGR_UID" -g "$AVENRA_TMPLCLIENTS_GID" /avenra/run/template-manager
install -d -m 0755 -o 0 -g 0 /avenra/run/firecracker/sandboxes
install -d -m 0755 -o 0 -g 0 /avenra/cache/firecracker/jails
# The shared cache parents stay writable by avenra-cache-rw, which the cache
# services need, but become sticky so those services cannot rename away the
# root-owned kernel and asset roots beneath them. The node image sets this
# after formatting the cache; asserting it on every start also covers nodes
# booted from older images.
chmod +t /avenra/cache /avenra/cache/firecracker
log_init "directories action=ensured elapsed_ms=$(elapsed_ms "$dirs_started_ms")"

# The guest kernel lives on the root volume under /avenra/vendor, but
# /avenra/cache is a fresh filesystem after every boot, so the copy the runtime
# inventory reads has to be re-made here. Staging before node-manager starts is
# what makes it an invariant rather than a race: the inventory is built at
# startup, and a node that cannot stage a kernel fails this init container
# instead of accepting sandboxes it would fail to boot.
kernel_started_ms="$(now_ms)"
/opt/avenra/bin/stage-guest-kernel.sh
log_init "guest-kernel action=staged elapsed_ms=$(elapsed_ms "$kernel_started_ms")"

status_started_ms="$(now_ms)"
runtime_supervisor_was_active=false
if nsenter --target 1 --mount --uts --ipc --net --pid -- systemctl is-active --quiet avenra-runtime-supervisor.service; then
  runtime_supervisor_was_active=true
fi
log_init "runtime-supervisor status active=${runtime_supervisor_was_active} elapsed_ms=$(elapsed_ms "$status_started_ms")"

unit_started_ms="$(now_ms)"
runtime_supervisor_unit_before="$(nsenter --target 1 --mount --uts --ipc --net --pid -- sh -c "sha256sum /etc/systemd/system/avenra-runtime-supervisor.service 2>/dev/null | awk '{print \$1}'" || true)"
runtime_supervisor_unit_expected="$(render_runtime_supervisor_unit | sha256sum | awk '{print $1}')"
runtime_supervisor_unit_changed=false
if [[ "$runtime_supervisor_unit_before" != "$runtime_supervisor_unit_expected" ]]; then
  runtime_supervisor_unit_changed=true
  touch "$RUNTIME_SUPERVISOR_RESTART_REQUIRED"
  render_runtime_supervisor_unit \
    | nsenter --target 1 --mount --uts --ipc --net --pid -- \
      tee /etc/systemd/system/avenra-runtime-supervisor.service >/dev/null
fi
log_init "runtime-supervisor unit changed=${runtime_supervisor_unit_changed} elapsed_ms=$(elapsed_ms "$unit_started_ms")"

if [[ -e "$RUNTIME_SUPERVISOR_RESTART_REQUIRED" ]]; then
  reload_started_ms="$(now_ms)"
  nsenter --target 1 --mount --uts --ipc --net --pid -- systemctl daemon-reload
  log_init "systemd daemon-reload action=ran elapsed_ms=$(elapsed_ms "$reload_started_ms")"
else
  log_init "systemd daemon-reload action=skipped reason=unit_unchanged"
fi

runtime_action=none
runtime_action_started_ms="$(now_ms)"
node_manager_ensure_runtime_supervisor \
  "$runtime_supervisor_was_active" \
  "$RUNTIME_SUPERVISOR_RESTART_REQUIRED" \
  "$SBXMGR_DRAIN_WORK" \
  "$RUNTIME_SUPERVISOR_HEALTH_SOCKET" \
  /opt/avenra/bin/grpc_health_probe
runtime_action="$RUNTIME_SUPERVISOR_ACTION"
log_init "runtime-supervisor action=${runtime_action} binary_changed=${runtime_supervisor_binary_changed} unit_changed=${runtime_supervisor_unit_changed} elapsed_ms=$(elapsed_ms "$runtime_action_started_ms")"
log_init "runtime-supervisor health action=ready"

log_init "complete elapsed_ms=$(elapsed_ms "$init_started_ms") installed=${installed_count} reused=${reused_count} skipped=${skipped_count}"
