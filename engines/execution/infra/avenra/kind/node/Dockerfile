# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# Kind node image for Avenra sandbox workers and builders: the stand-in for the
# Avenra node image a production worker or builder boots (infra/avenra/packer).
#
# Packer bakes the node layer into the image: packages, scripts and enabled
# systemd units, so a node boots with its btrfs /avenra/cache, cgroup subtree,
# loop-device headroom and /space skeleton before kubelet starts and nothing is
# installed when the node joins. This image does the same for a kind node. The
# units run on every node start, including after `docker restart`, which gives
# the container a fresh /dev, mount table and cgroups; see avenra-kind-node.sh.
#
# Built and distributed by infra/avenra/kind/scripts/kind-node-image.sh, which
# tags it by the SHA-256 of exactly the files copied below plus this one. The
# build context is those files alone, so nothing else can reach the image.
#
# The base is the node image kind v0.30.0 (install-prereqs.sh) defaults to,
# pinned by its multi-platform index. The control-plane and infra nodes run the
# same base unchanged (kind-cluster.yaml.tmpl), so every node runs one
# Kubernetes version.
FROM kindest/node:v1.34.0@sha256:7416a61b42b1662ca6ca89f02028ac133a309a2a30ba309614e8ec94d976dc5a

# mkfs.btrfs for avenra-cache.service's first format (01-partitions.sh installs
# the same package into the production image). Everything else the units use
# (losetup, blkid, findmnt, mount, install, stat) is in the base image.
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends btrfs-progs \
 && rm -rf /var/lib/apt/lists/* \
 && command -v mkfs.btrfs losetup blkid findmnt >/dev/null

COPY --chmod=0755 infra/avenra/kind/node/avenra-kind-node.sh /usr/local/lib/avenra/kind-node/avenra-kind-node.sh
COPY --chmod=0644 scripts/lib/avenra-loop-devices.sh /usr/local/lib/avenra/kind-node/avenra-loop-devices.sh
COPY --chmod=0644 infra/avenra/identity/node-identities.sh /usr/local/lib/avenra/kind-node/node-identities.sh
COPY --chmod=0644 infra/avenra/kind/node/systemd/ /etc/systemd/system/
# The kubelet and containerd settings a deployed node's bootstrap applies before
# either starts (infra/avenra/aws/eks/modules/avenra/templates/
# worker-bootstrap.sh.tftpl). Baked in rather than patched into the cluster
# config, so a cluster created without them runs another image tag and
# check-kind-node-image.sh refuses it. kind's kubelet unit takes extra flags from
# /etc/default/kubelet, and its containerd config (version 2) gains an import of
# the version 2 drop-in. The build fails unless the configuration containerd
# loads has the `space` runtime with its option and still has kind's own
# settings: runc's systemd cgroup driver, which kubelet adopts from containerd,
# and discard_unpacked_layers (see the drop-in for how a version mismatch loses
# them). The awk prints each `table|key = value` of the dump.
COPY --chmod=0644 infra/avenra/kind/node/kubelet/50-space.conf /etc/kubernetes/kubelet.conf.d/50-space.conf
COPY --chmod=0644 infra/avenra/kind/node/containerd/avenra-runtime.toml /etc/containerd/conf.d/avenra-runtime.toml
RUN sed -i 's|^KUBELET_EXTRA_ARGS=|KUBELET_EXTRA_ARGS=--config-dir=/etc/kubernetes/kubelet.conf.d |' /etc/default/kubelet \
 && grep -q '^KUBELET_EXTRA_ARGS=--config-dir=/etc/kubernetes/kubelet.conf.d ' /etc/default/kubelet \
 && sed -i '0,/^version = 2$/s||version = 2\nimports = ["/etc/containerd/conf.d/*.toml"]|' /etc/containerd/config.toml \
 && containerd config dump 2>/dev/null \
    | awk '/^ *\[/ { table = $0; gsub(/^ *\[|\] *$/, "", table); next } NF { sub(/^ */, ""); print table "|" $0 }' \
    > /tmp/containerd-config \
 && for want in \
      "plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.avenra|runtime_type = 'io.containerd.avenra.v2'" \
      "plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.avenra|pod_annotations = ['space.pplx.net/*']" \
      "plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.avenra.options|CniConfDir = '/etc/cni/net.d'" \
      "plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runc.options|SystemdCgroup = true" \
      "plugins.'io.containerd.cri.v1.images'|discard_unpacked_layers = true"; do \
      grep -qxF "$want" /tmp/containerd-config || { echo "containerd config lacks: $want" >&2; exit 1; }; \
    done \
 && rm /tmp/containerd-config
RUN systemctl enable \
      avenra-loop-nodes.service \
      avenra-kind-devices.service \
      avenra-cgroup.service \
      avenra-layout.service \
      avenra-cache.service \
      avenra-kind-vsock-jails.service

# The inputs tag, so a node can be matched against the tree that should have
# built it (scripts/check-kind-node-image.sh). Containers inherit image labels.
ARG AVENRA_KIND_NODE_IMAGE_TAG=unset
LABEL org.opencontainers.image.version="${AVENRA_KIND_NODE_IMAGE_TAG}" \
      org.opencontainers.image.title="avenra-kind-node"
