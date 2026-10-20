#!/usr/bin/env bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# Install auditd and configure audit rules for Avenra node security monitoring.
# Runs as root during Packer build.
#
# Logs: /var/log/audit/audit.log (managed by auditd, rotated automatically)
# View: ausearch -k <key>      (e.g. ausearch -k avenra-binary-tamper)
# Report: aureport --summary
set -euxo pipefail

export DEBIAN_FRONTEND=noninteractive
AL2023_RELEASEVER="${AL2023_RELEASEVER:-latest}"

if command -v apt-get >/dev/null 2>&1; then
  apt-get install -y auditd audispd-plugins 2>/dev/null || apt-get install -y auditd
elif command -v dnf >/dev/null 2>&1; then
  dnf --releasever="$AL2023_RELEASEVER" install -y audit
else
  echo "No supported package manager found" >&2
  exit 1
fi

# ── Audit daemon config ─────────────────────────────────────────────────────
# Keep defaults but ensure enough buffer for busy sandbox nodes.
sed -i 's/^num_logs =.*/num_logs = 8/' /etc/audit/auditd.conf
sed -i 's/^max_log_file =.*/max_log_file = 32/' /etc/audit/auditd.conf
sed -i 's/^max_log_file_action =.*/max_log_file_action = ROTATE/' /etc/audit/auditd.conf

# ── Avenra audit rules ───────────────────────────────────────────────────────
tee /etc/audit/rules.d/90-space.rules > /dev/null <<'EOF'
## ==========================================================================
## Avenra platform audit rules
## Keys use "avenra-" prefix for easy filtering: ausearch -k avenra-*
## ==========================================================================

## ── 1. Privilege escalation ─────────────────────────────────────────────────
## Log all sudo executions
-a always,exit -F path=/usr/bin/sudo -F perm=x -k avenra-sudo
## Log setuid/setgid syscalls (privilege changes in sandbox launch path)
-a always,exit -F arch=b64 -S setuid -S setgid -S setreuid -S setregid -k avenra-privesc
## Log capability changes
-a always,exit -F arch=b64 -S capset -k avenra-capset

## ── 2. Binary integrity ────────────────────────────────────────────────────
## Detect writes to Avenra binaries (firecracker, jailer, services)
-w /avenra/bin/ -p wa -k avenra-binary-tamper
## Detect writes to Firecracker/jailer vendor binaries and the node's
## artifact store, which only node-manager writes
-w /avenra/vendor/ -p wa -k avenra-vendor-tamper

## ── 3. Config integrity ────────────────────────────────────────────────────
## Avenra platform config
-w /avenra/etc/ -p wa -k avenra-config-tamper
## Sudoers modifications
-w /etc/sudoers -p wa -k avenra-sudoers
-w /etc/sudoers.d/ -p wa -k avenra-sudoers
## Sysctl modifications (affects hugepages, ip_forward)
-w /etc/sysctl.d/ -p wa -k avenra-sysctl

## ── 4. User/group account changes ──────────────────────────────────────────
-w /etc/passwd -p wa -k avenra-identity
-w /etc/group -p wa -k avenra-identity
-w /etc/shadow -p wa -k avenra-identity
-w /etc/subuid -p wa -k avenra-identity
-w /etc/subgid -p wa -k avenra-identity

## ── 5. SSH key access ──────────────────────────────────────────────────────
## Sandbox private key — only root/builder should read this
-w /root/.ssh/sandbox_ed25519 -p r -k avenra-ssh-key
-w /avenra/etc/ssh/ -p wa -k avenra-ssh-config

## ── 6. Namespace & isolation syscalls ──────────────────────────────────────
## Log setns (entering namespaces) and unshare (creating namespaces)
## Exclude node-manager (UID 0, expected) — alert on unexpected callers
-a always,exit -F arch=b64 -S setns -S unshare -F auid>=1000 -F auid!=4294967295 -k avenra-namespace
## Log clone with CLONE_NEWNET from non-root users. This filters on clone(2),
## whose first argument is the flags word.
-a always,exit -F arch=b64 -S clone -F a0&0x40000000 -F auid>=1000 -F auid!=4294967295 -k avenra-namespace
## clone3 can create a namespace directly, but its flags sit in a struct its
## first argument points to, which audit filters can't read. So log every
## clone3 from a logged-in user (services carry no auid and are excluded),
## under its own key, and filter for namespace flags when reading the log.
-a always,exit -F arch=b64 -S clone3 -F auid>=1000 -F auid!=4294967295 -k avenra-namespace-clone3

## ── 7. KVM device access ───────────────────────────────────────────────────
## Track who opens /dev/kvm (VM creation)
-w /dev/kvm -p r -k avenra-kvm-access

## ── 8. Service socket directories ──────────────────────────────────────────
## Host-local control sockets live in service-owned directories. Track metadata
## changes because unexpected socket replacement indicates compromise.
-w /avenra/run/node-manager -p wa -k avenra-nodemgr-socket
-w /avenra/run/sandbox-manager -p wa -k avenra-sbxmgr-socket
-w /avenra/run/sandbox-host-control -p wa -k avenra-sbxmgr-host-control
-w /avenra/run/credential-manager -p wa -k avenra-credmgr-socket
-w /avenra/run/template-manager -p wa -k avenra-tmplmgr-socket
## SQLite shares this directory; audit only socket objects, including a
## socket created after rules load, rather than ordinary DB/WAL writes.
-a always,exit -F dir=/avenra/run/runtime-manager -F filetype=socket -F perm=wa -k avenra-rtmgr-socket

## ── 9. Sandbox escape indicators ───────────────────────────────────────────
## Sandbox UIDs (10000+) accessing sensitive host paths outside their sandbox dir.
## These should never happen under normal operation.
-a always,exit -F arch=b64 -S openat -F dir=/etc -F auid>=10000 -F auid!=4294967295 -k avenra-sbx-escape
-a always,exit -F arch=b64 -S openat -F dir=/root -F auid>=10000 -F auid!=4294967295 -k avenra-sbx-escape

## ── 10. Firecracker runtime ────────────────────────────────────────────────
## Track jailer invocations
-w /avenra/bin/jailer -p x -k avenra-jailer-exec
## Track direct firecracker invocations (should go through jailer in prod)
-w /avenra/bin/firecracker -p x -k avenra-fc-exec

## ── 11. Module loading ─────────────────────────────────────────────────────
## Kernel module loads could weaken isolation
-a always,exit -F arch=b64 -S init_module -S finit_module -S delete_module -k avenra-kmod
EOF

# ── Enable and start ────────────────────────────────────────────────────────
# AL2023's audit package ships rules.d/audit.rules ending in `-a task,never`,
# which turns syscall auditing off for every task started after the rules load,
# so the Avenra syscall rules above would load and then never fire. Keep the
# rest of that file (-D, buffer and failure settings) and drop that rule.
if [[ -f /etc/audit/rules.d/audit.rules ]]; then
  sed -i '/^-a[[:space:]]*task,never/d' /etc/audit/rules.d/audit.rules
fi
systemctl enable auditd
# Load the merged rules now, so a rule the kernel rejects fails the bake
# instead of shipping silently.
augenrules --load
if grep -q '^-a[[:space:]]*task,never' /etc/audit/audit.rules; then
  echo "ERROR: /etc/audit/audit.rules still disables syscall auditing (-a task,never)" >&2
  exit 1
fi

echo "=== Auditd configured ==="
echo "  Rules:  /etc/audit/rules.d/90-space.rules"
echo "  Log:    /var/log/audit/audit.log"
echo "  Search: ausearch -k avenra-sudo"
echo "  Report: aureport --summary"
