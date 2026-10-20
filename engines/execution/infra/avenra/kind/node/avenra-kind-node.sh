#!/usr/bin/env bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# shellcheck source-path=SCRIPTDIR
# avenra-kind-node.sh — the Kind node's stand-in for the Avenra node image.
#
# A production worker or builder gets its btrfs /avenra/cache, cgroup subtree,
# loop-device headroom, device permissions, Avenra accounts and /space layout
# from the node image (infra/avenra/packer) before any Avenra pod starts. The Kind
# node image for sandbox workers and builders (Dockerfile beside this script)
# bakes in this script and enables the units that run it, ordered before
# kubelet:
#
#   avenra-loop-nodes.service       loop-device node headroom in the node's /dev
#   avenra-kind-devices.service     Kind-only /dev/kvm and vhost permission widening
#   avenra-cgroup.service           /sys/fs/cgroup/space   (packer 07-sysctl.sh)
#   avenra-layout.service           Avenra accounts, /space skeleton, Firecracker
#                                  copies/links, sandbox.toml    (packer 04-firecracker.sh,
#                                                          07-sysctl.sh)
#   avenra-cache.service            btrfs /avenra/cache     (packer 01-partitions.sh,
#                                                          11-nvme-cache.sh)
#   avenra-kind-vsock-jails.service Kind-only vhost-vsock nodes in Firecracker jails
#
# The node also runs avenra-runtime-supervisor.service, which node-manager's
# init container installs and upgrades (scripts/k8s/node-manager-init.sh), as
# on a production node.
#
# A container's /dev, mounts and cgroups do not survive `docker restart`, which
# is why these are boot units and not a one-time setup. Each step is
# idempotent within a boot, so re-running a unit by hand on a live node is
# safe. The cache is the one step that does not carry state across boots: as on
# a deployed node, every boot starts it as a fresh filesystem.
#
# Defaults are baked like the rest of the layer; /etc/avenra/kind-node.env, when
# present, overrides AVENRA_CACHE_SIZE_MB and AVENRA_LOOP_DEVICE_TARGET.
#
# Usage: avenra-kind-node.sh loop-nodes|devices|cgroup|layout|cache|vsock-jails
set -euo pipefail

# Baked together into the Kind node image; the source= paths below are their
# repository copies.
LIB_DIR=/usr/local/lib/avenra/kind-node
# shellcheck source=../../identity/node-identities.sh
. "$LIB_DIR/node-identities.sh"
# shellcheck source=../../../../scripts/lib/avenra-loop-devices.sh
. "$LIB_DIR/avenra-loop-devices.sh"

STEP="${1:-}"
CACHE_DIR=/avenra/cache
# On the node's /var, which kind keeps on a volume. The image file outlives a
# node restart; its filesystem does not (see cache below).
CACHE_IMAGE=/var/lib/avenra-cache.img
# Marks the cache formatted and mounted for this boot. kind mounts the node's
# /run as a tmpfs, so a node start finds it absent.
CACHE_STATE_DIR=/run/avenra-kind-node
CACHE_BOOT_MARKER="$CACHE_STATE_DIR/cache.done"
CACHE_SIZE_MB="${AVENRA_CACHE_SIZE_MB:-40960}"

log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# ── Loop devices ─────────────────────────────────────────────────────────────
# Docker gives the node a /dev tmpfs holding the eight loop nodes the host had
# at creation, and every pod on the node copies the node's /dev when it starts.
# The node's cache attach below and each pod's first attaches need more; the
# library explains why, and what production gets from max_loop=10000 instead.
loop_nodes() {
  avenra_loop_stage_pool log
}

# ── Device permissions (Kind only) ───────────────────────────────────────────
# Production grants the jailer's per-sandbox uids /dev/kvm through the `kvm`
# group on the node image. Kind has no such group membership: the sandbox uids
# are created on the fly and own no numeric group that holds /dev/kvm, so
# Firecracker fails with "KVM object: Permission denied". Widen the modes on
# the node, where pods copy them from. Do NOT replicate this on a real worker.
devices() {
  local dev
  for dev in /dev/kvm /dev/vhost-vsock /dev/vhost-net; do
    [[ -e $dev ]] || continue
    chmod 0666 "$dev"
    log "chmod 0666 $dev (Kind-only kvm-group workaround)"
  done
}

# ── cgroup v2 parent ─────────────────────────────────────────────────────────
# node-manager writes /sys/fs/cgroup/avenra/{cpu,memory}.max at startup and
# creates per-sandbox children with these controllers delegated. Same commands
# as packer's avenra-cgroup.service.
cgroup() {
  echo "+io" >/sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true
  mkdir -p /sys/fs/cgroup/space
  echo "+cpu +memory +io +pids" >/sys/fs/cgroup/avenra/cgroup.subtree_control
  log "/sys/fs/cgroup/space delegates: $(cat /sys/fs/cgroup/avenra/cgroup.subtree_control)"
}

# ── Avenra accounts ───────────────────────────────────────────────────────────
# The groups and service users packer 07-sysctl.sh creates on the node image,
# with the same numbers the worker image uses: the host runtime-supervisor and
# the sidecars it launches run here, and the node's files carry these owners.
ensure_identities() {
  local spec group gid actual _alias user uid primary
  for spec in $AVENRA_GROUP_SPECS; do
    group="${spec%%:*}" gid="${spec##*:}"
    if actual="$(getent group "$group" | cut -d: -f3)" && [[ -n $actual ]]; then
      [[ $actual == "$gid" ]] || groupmod -g "$gid" "$group"
    else
      groupadd -g "$gid" "$group"
    fi
  done
  while IFS=: read -r _alias user uid primary; do
    [[ -n $user ]] || continue
    if id "$user" >/dev/null 2>&1; then
      [[ "$(id -u "$user")" == "$uid" ]] || usermod -u "$uid" "$user"
      usermod -g "$primary" "$user"
    else
      useradd --system --no-create-home --uid "$uid" --gid "$primary" \
        --shell /usr/sbin/nologin "$user"
    fi
  done <<<"$AVENRA_USER_SPECS"
}

# ── /space skeleton ──────────────────────────────────────────────────────────
# The developer's read-only vendor mount is a download cache, not executable
# root-owned state. Copy the bootstrap E0 runtime to the node's /avenra/bin
# hostPath; node-manager verifies its compiled pins before admitting execution.
# Never rewrite an installed runtime: a live supervisor may still be using it.
stage_runtime() (
  local version=v1.15.1 root=/avenra/bin/runtime/firecracker
  local source="/avenra/vendor/firecracker/$version" path binary staged=""
  for path in /avenra/bin/runtime "$root"; do
    if [[ ! -e $path && ! -L $path ]]; then
      mkdir -m 0755 "$path"
    fi
    [[ -d $path && ! -L $path && $(stat -c %u "$path") == 0 ]] ||
      die "runtime directory is not root-owned: $path"
    (( (8#$(stat -c %a "$path") & 0022) == 0 )) ||
      die "runtime directory is writable by another account: $path"
  done
  exec 9>"$root/.stage.lock"
  flock -x 9
  for path in "$source" "$source/bin"; do
    [[ -d $path && ! -L $path ]] || die "missing or linked runtime source: $path"
  done
  for binary in firecracker jailer; do
    path="$source/bin/$binary"
    [[ -f $path && ! -L $path ]] || die "missing or linked runtime binary: $path"
  done
  if [[ -e $root/$version || -L $root/$version ]]; then
    for path in "$root/$version" "$root/$version/bin" \
        "$root/$version/bin/firecracker" "$root/$version/bin/jailer"; do
      [[ ! -L $path && $(stat -c %u "$path") == 0 ]] ||
        die "unprotected installed runtime: $path"
      (( (8#$(stat -c %a "$path") & 0022) == 0 )) ||
        die "installed runtime is writable by another account: $path"
    done
    for binary in firecracker jailer; do
      if [[ ! -f $root/$version/bin/$binary || ! -x $root/$version/bin/$binary ]] ||
          ! cmp -s "$source/bin/$binary" "$root/$version/bin/$binary"; then
        die "installed $version differs from the download cache; recreate the Kind node"
      fi
    done
  else
    staged="$(mktemp -d "$root/.incoming.XXXXXXXX")"
    trap '[[ -z $staged ]] || rm -rf -- "$staged"' EXIT
    mkdir -m 0755 "$staged/bin"
    for binary in firecracker jailer; do
      cp -P -- "$source/bin/$binary" "$staged/bin/$binary"
      [[ -f $staged/bin/$binary && ! -L $staged/bin/$binary ]] ||
        die "runtime source changed during staging: $binary"
      chmod 0755 "$staged/bin/$binary"
    done
    chmod 0755 "$staged"
    sync -f "$staged"
    mv -T -- "$staged" "$root/$version"
    sync -f "$root"
    staged=""
  fi
  if [[ ! -e $root/default && ! -L $root/default ]]; then
    ln -s "$version" "$root/default"
  fi
  [[ -L $root/default && $(readlink "$root/default") == "$version" ]] ||
    die "unexpected Kind runtime default: $root/default"
  log "protected $version runtime staged on the node; admission verifies catalog pins"
)

# The host state directories, Firecracker links and per-VM defaults packer
# bakes into the node image, on the hostPaths the node-service pods mount. The
# directories only a service's own init container creates in production
# (volume-manager's, node-ctl's, runtime-manager's, build-manager's) are left
# to those charts.
layout() {
  local sbxclients_gid gate gate_path
  ensure_identities
  sbxclients_gid="$(avenra_expected_gid_for_group avenra-sbxclients)"
  install -d -m 0755 -o 0 -g 0 /space /avenra/etc /avenra/bin /avenra/run /avenra/run/node-manager \
    /avenra/run/firecracker /avenra/run/firecracker/sandboxes /var/run/netns
  install -d -m 0750 -o 0 -g "$sbxclients_gid" \
    /avenra/run/dependency-drain /avenra/run/dependency-drain/sandbox-manager
  # Lock files a running sandbox-manager may hold: create or re-own them, never
  # truncate or replace them.
  for gate in capability-v1.lock admission-v1.lock work-v1.lock; do
    gate_path="/avenra/run/dependency-drain/sandbox-manager/$gate"
    if [[ -L "$gate_path" || ( -e "$gate_path" && ! -f "$gate_path" ) ]]; then
      die "dependency-drain gate is not a regular file: $gate_path"
    fi
    touch "$gate_path"
    chown "0:$sbxclients_gid" "$gate_path"
    chmod 0660 "$gate_path"
  done
  install -d -m 0750 -o "$AVENRA_SBXMGR_UID" -g "$sbxclients_gid" \
    /avenra/run/sandbox-manager /avenra/run/credential-manager
  install -d -m 0755 -o "$AVENRA_SBXMGR_UID" -g "$AVENRA_SBXMGR_GID" /avenra/run/sandbox-host-control
  install -d -m 0750 -o "$AVENRA_TMPLMGR_UID" -g "$(avenra_expected_gid_for_group avenra-tmplclients)" \
    /avenra/run/template-manager
  stage_runtime
  # The host supervisor and node-manager see the same protected runtime path.
  # The production init container stages Avenra's service binaries beside it.
  ln -sfn runtime/firecracker/default/bin/firecracker /avenra/bin/firecracker
  ln -sfn runtime/firecracker/default/bin/jailer /avenra/bin/jailer
  # Per-VM defaults sandbox-manager reads; packer writes its sizing knobs here,
  # and Kind sets none of them.
  printf '%s\n' "# Generated by the Kind node layer (infra/avenra/kind/node/avenra-kind-node.sh)" \
    >/avenra/etc/sandbox.toml
  chmod 0644 /avenra/etc/sandbox.toml
  log "Avenra accounts and /space skeleton in place"
}

# ── btrfs /avenra/cache ───────────────────────────────────────────────────────
# sandbox-manager clones template rootfs into instance dirs with btrfs subvolume
# snapshots, so the cache must be btrfs. A deployed node formats its
# instance-store NVMe on every boot (packer 11-nvme-cache.sh), so templates,
# sandboxes and snapshots on the cache never survive a node restart, and node
# services re-create what they need on it. Here the cache is a sparse loopback
# image on the node, reformatted the same way on every start of the node
# container.
cache_is_mounted() {
  findmnt -n -t btrfs --mountpoint "$CACHE_DIR" >/dev/null 2>&1
}

cache_image_is_btrfs() {
  [[ -s $CACHE_IMAGE ]] && [[ "$(blkid -p -s TYPE -o value "$CACHE_IMAGE" 2>/dev/null)" == btrfs ]]
}

# A loop device still bound to the image (a crash before autoclear was armed)
# would keep serving the old filesystem's blocks over the new one. `losetup -j`
# matches an existing file by device and inode, but a missing one by path, and
# the host kernel's loop devices are shared by every kind node, each with an
# image at this path: without the image there is nothing of this node's to
# release, and matching by path would find the other nodes' caches.
release_cache_image() {
  local loopdev
  [[ -e $CACHE_IMAGE ]] || return 0
  while read -r loopdev; do
    [[ -n $loopdev ]] || continue
    findmnt -n --source "$loopdev" >/dev/null 2>&1 \
      && die "$loopdev (backing $CACHE_IMAGE) is still mounted; restart the node to start its cache fresh"
    avenra_loop_detach "$loopdev" || true
  done < <(losetup -l -n -O NAME -j "$CACHE_IMAGE" 2>/dev/null | tr -d '[:blank:]')
  [[ -z "$(losetup -l -n -O NAME -j "$CACHE_IMAGE" 2>/dev/null)" ]] \
    || die "a loop device still holds $CACHE_IMAGE; restart the node to start its cache fresh"
}

format_cache_image() {
  log "formatting btrfs backing image $CACHE_IMAGE (${CACHE_SIZE_MB}M, sparse)"
  release_cache_image
  # Sparse (truncate, not fallocate): the virtual size gives disk admission
  # production-like headroom while real blocks are only consumed as sandboxes
  # write. Truncating to zero first also returns the previous boot's blocks to
  # the host.
  truncate -s 0 "$CACHE_IMAGE"
  truncate -s "${CACHE_SIZE_MB}M" "$CACHE_IMAGE"
  mkfs.btrfs -f -L avenra-cache "$CACHE_IMAGE" >/dev/null
}

mount_cache_image() {
  local loopdev reused=0
  # A device already bound to this image is reused rather than a second one
  # attached: one an earlier attach still has mounted (btrfs refuses a second
  # device with the same fsid while that mount lives), or one a crash left
  # attached and unmounted, which would otherwise never be reclaimed.
  loopdev="$(losetup -l -n -O NAME -j "$CACHE_IMAGE" 2>/dev/null | head -n 1 | tr -d '[:space:]')"
  if [[ -n $loopdev && -b $loopdev ]]; then
    reused=1
    log "reusing $loopdev, already attached to $CACHE_IMAGE"
  else
    # The library's retrying attach: allocation is two ioctls, and attaches
    # racing on other nodes can be handed the same free index.
    loopdev="$(avenra_loop_attach "$CACHE_IMAGE")" || loopdev=""
    if [[ -z $loopdev || ! -b $loopdev ]]; then
      avenra_loop_exhaustion_report "$CACHE_IMAGE" | while IFS= read -r line; do log "$line" >&2; done
      die "no loop device available for $CACHE_IMAGE"
    fi
  fi
  # noatime as deployed nodes mount the cache (packer 11-nvme-cache.sh).
  if ! mount -o nosuid,noatime "$loopdev" "$CACHE_DIR"; then
    (( reused )) || avenra_loop_detach "$loopdev" || true
    die "mount $loopdev $CACHE_DIR failed (image $CACHE_IMAGE; if this repeats, delete the image on the node)"
  fi
  # `losetup -d` on a mounted device does not detach it: it arms
  # LO_FLAGS_AUTOCLEAR, so the kernel releases the device once the filesystem
  # is unmounted everywhere (node stop, `docker restart`). Without it every node
  # restart would leak one device from the kernel-global pool.
  avenra_loop_detach "$loopdev" \
    || log "WARNING: could not set autoclear on $loopdev; it will leak when the node stops"
  log "mounted btrfs loop $loopdev on $CACHE_DIR (image $CACHE_IMAGE, autoclear)"
}

# The cache directories packer bakes and 11-nvme-cache.sh recreates after it
# mounts the instance store: setgid dirs the avenra-cache-rw services (sbxmgr,
# tmplmgr, volmgr) write, root-owned jails. The two shared parents are also
# sticky, so those services cannot rename away the root-owned kernel and asset
# roots staged beneath them.
cache_skeleton() {
  local cache_rw_gid
  cache_rw_gid="$(avenra_expected_gid_for_group avenra-cache-rw)"
  install -d -m 3775 -o 0 -g "$cache_rw_gid" "$CACHE_DIR" "$CACHE_DIR/firecracker"
  install -d -m 2775 -o 0 -g "$cache_rw_gid" \
    "$CACHE_DIR/firecracker/templates" \
    "$CACHE_DIR/firecracker/sandboxes" \
    "$CACHE_DIR/firecracker/builds" \
    "$CACHE_DIR/firecracker/.volmgr-staging"
  install -d -m 0755 -o 0 -g 0 "$CACHE_DIR/firecracker/jails"
}

# The first run in a boot formats the image; a later run in the same boot (the
# unit re-run by hand) keeps the mounted cache, or remounts the image if it was
# unmounted, reformatting it only if it is no longer btrfs. The marker is
# written last, so a run that failed partway formats again.
cache() {
  install -d -m 0755 -o 0 -g 0 /space "$CACHE_DIR" "$CACHE_STATE_DIR"
  exec 9>"$CACHE_STATE_DIR/cache.lock"
  flock 9
  if cache_is_mounted; then
    log "$CACHE_DIR is already btrfs"
  else
    if findmnt -n --mountpoint "$CACHE_DIR" >/dev/null 2>&1; then
      die "$CACHE_DIR is mounted but not btrfs: $(findmnt -n -o SOURCE,FSTYPE --mountpoint "$CACHE_DIR")"
    fi
    command -v mkfs.btrfs >/dev/null \
      || die "mkfs.btrfs missing; this node is not running the Avenra Kind node image (infra/avenra/kind/node/Dockerfile)"
    avenra_loop__losetup_bin >/dev/null || die "losetup missing (util-linux)"
    if [[ ! -e $CACHE_BOOT_MARKER ]] || ! cache_image_is_btrfs; then
      format_cache_image
    fi
    mount_cache_image
  fi
  # Shared, so a pod that receives the cache HostToContainer also receives a
  # later remount. kind's entrypoint makes `/` rshared, which new mounts
  # inherit; this holds for a mount whatever made it.
  mount --make-shared "$CACHE_DIR"
  local img_mb
  img_mb=$(( $(stat -c %s "$CACHE_IMAGE") / 1048576 ))
  if (( img_mb != CACHE_SIZE_MB )); then
    log "WARNING: the cache size is ${CACHE_SIZE_MB}M (AVENRA_CACHE_SIZE_MB) but the image formatted this boot is ${img_mb}M; restart the node to reformat it at the new size"
  fi
  cache_skeleton
  touch "$CACHE_BOOT_MARKER"
}

# ── vhost-vsock nodes in Firecracker jails (Kind only) ───────────────────────
# The jailer mknods /dev/kvm, /dev/net/tun, /dev/urandom and /dev/userfaultfd
# into each jail, but not /dev/vhost-vsock (a known Firecracker jailer
# limitation). Without it Firecracker silently disables the vsock device, the
# host bridge cannot reach the guest, and readiness expires at 30 s. A
# production node image provides the device to its jails; here this
# long-running unit races the jailer instead: as soon as a jail's dev/ appears
# it mknods the node into it, polling at 10 ms because Firecracker opens the
# device very shortly after exec. The jails live in the node's mount
# namespace, under the host runtime-supervisor, so they appear here directly.
# Do NOT replicate this on a real worker.
vsock_jails() {
  local src=/dev/vhost-vsock major minor jail_dev
  if [[ ! -c $src ]]; then
    log "no /dev/vhost-vsock on this node; nothing to provide"
    exec sleep infinity
  fi
  major=$((16#$(stat -c '%t' "$src")))
  minor=$((16#$(stat -c '%T' "$src")))
  log "providing vhost-vsock (c $major $minor) to Firecracker jails"
  while true; do
    for jail_dev in "$CACHE_DIR"/firecracker/jails/firecracker/*/root/dev; do
      [[ -d $jail_dev && ! -e $jail_dev/vhost-vsock ]] || continue
      # 0666 so the sandbox uid Firecracker switches to can open it.
      if mknod "$jail_dev/vhost-vsock" c "$major" "$minor" 2>/dev/null; then
        chmod 0666 "$jail_dev/vhost-vsock" 2>/dev/null || true
        log "mknod $jail_dev/vhost-vsock"
      fi
    done
    sleep 0.01
  done
}

case "$STEP" in
  loop-nodes) loop_nodes ;;
  devices) devices ;;
  cgroup) cgroup ;;
  layout) layout ;;
  cache) cache ;;
  vsock-jails) vsock_jails ;;
  *) die "usage: $0 loop-nodes|devices|cgroup|layout|cache|vsock-jails" ;;
esac
