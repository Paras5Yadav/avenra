#!/usr/bin/env bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# build-images.sh — build every Avenra image the local kind stack needs and
# `kind load` it into the cluster.
#
# Mirrors the docker-build chain in the Tiltfile so a scripted bring-up and a
# `tilt up` run produce byte-identical images. Every Rust binary comes from
# one artifact image (avenra-rust-build-kind:local — the union of the
# production artifact sets, built once by build-rust-artifacts.sh on the host
# or inside Docker); re-runs are fast because that build is incremental or
# layer-cached and every runtime stage sits in the docker layer cache.
#
# Usage:
#   bash infra/avenra/kind/scripts/build-images.sh                # build all
#   bash infra/avenra/kind/scripts/build-images.sh avenra-api      # build one
#   bash infra/avenra/kind/scripts/build-images.sh --no-load all  # skip kind load
#
# Env knobs:
#   AVENRA_KIND_CLUSTER       kind cluster name to load images into (default: avenra-local)
#   AVENRA_KIND_PARALLEL=1    build all images in parallel via background jobs
set -euo pipefail

KIND_CLUSTER="${AVENRA_KIND_CLUSTER:-avenra-local}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
SEED_OUTPUT_DIR="${AVENRA_SEED_OUTPUT_DIR:-$HOME/.avenra-devbox/kind/seed/build}"
cd "$REPO_ROOT"

LOAD_INTO_KIND=1
if [[ "${1:-}" == "--no-load" ]]; then
  LOAD_INTO_KIND=0
  shift
fi

log() { printf '[build-images] %s\n' "$*"; }

kind_load() {
  local ref=$1
  if (( LOAD_INTO_KIND )); then
    log "kind load: $ref → $KIND_CLUSTER"
    kind load docker-image "$ref" --name "$KIND_CLUSTER"
  fi
}

RUST_ARTIFACT_IMAGE="avenra-rust-build-kind:local"
# The build SHA every Avenra image records, matching the version the workloads
# report; see telemetry_config.py.
KIND_GIT_SHA="$(python3 "$REPO_ROOT/infra/avenra/kind/scripts/telemetry_config.py" commit)"

# The one Rust build every runtime image below copies from. Owned by
# build-rust-artifacts.sh: an incremental cargo build (inside the CI builder
# image, host target dir) staged into the /artifacts layout on Linux/x86_64,
# the in-Docker union build otherwise (AVENRA_KIND_RUST_ARTIFACTS=incremental|docker forces either). Idempotent
# and lock-serialized; an up-to-date build returns in seconds.
build_rust_artifacts() {
  log "building rust artifacts: $RUST_ARTIFACT_IMAGE (every production set, one build)"
  bash "$REPO_ROOT/infra/avenra/kind/scripts/build-rust-artifacts.sh"
}

# `docker build` for one runtime image, as the Tiltfile runs it: its Dockerfile,
# its bases from the GHCR mirror when AVENRA_CI_IMAGE_MIRROR is set, and its
# dependency stages from the registry layer cache when
# AVENRA_KIND_BUILD_CACHE_REPOSITORY is set (see runtime-image-cache.sh).
image_build() {
  bash "$REPO_ROOT/infra/avenra/kind/scripts/runtime-image-cache.sh" build "$@"
}

# Build the runtime image that copies its binaries out of the shared artifact
# image into a debian-slim base.
build_rust_runtime() {
  local name=$1 dockerfile=$2
  local runtime_ref="${name}:local"

  build_rust_artifacts

  log "building runtime image: $runtime_ref ($dockerfile)"
  image_build "$name" --provenance=false --sbom=false -t "$runtime_ref" \
    --build-arg "AVENRA_RUST_BUILD_IMAGE=${RUST_ARTIFACT_IMAGE}" \
    --build-arg "AVENRA_GIT_SHA=${KIND_GIT_SHA:-unknown}" \
    .

  kind_load "$runtime_ref"
}

build_avenra_api() {
  build_rust_runtime avenra-api docker/Dockerfile.api
}

build_avenra_data_plane() {
  build_rust_runtime avenra-data-plane docker/Dockerfile.data-plane
}

# Kind-only: the intermediate-key broker fixture the credential-manager
# authority uses as its external key provider (charts/avenra-kind-key-broker).
build_avenra_kind_key_broker() {
  build_rust_runtime avenra-kind-key-broker docker/Dockerfile.kind-key-broker
}

build_avenra_cluster_controller() {
  build_rust_runtime avenra-cluster-controller docker/Dockerfile.cluster-controller
}

# The production worker image (docker/Dockerfile.worker — the binaries, cargo
# features, identities, init scripts and nftables policies of the production
# node DaemonSets), which every node-service chart, the operator shell and
# avenra-internal-api run unchanged. LTO stays off locally.
build_avenra_worker() {
  build_rust_runtime avenra-worker docker/Dockerfile.worker
}

# seed-publish carries the locally built production seed (see build-seed.sh)
# plus the publisher that uploads it to the object store and registers the release
# through the real ClusterApi RPCs. build-seed.sh is idempotent and skips the
# privileged rootfs build when its inputs are unchanged.
build_avenra_seed_publish() {
  build_rust_artifacts
  AVENRA_SEED_OUTPUT_DIR="$SEED_OUTPUT_DIR" \
  AVENRA_RUST_BUILD_IMAGE="$RUST_ARTIFACT_IMAGE" \
    bash "$REPO_ROOT/infra/avenra/kind/scripts/build-seed.sh"

  log "building runtime image: avenra-seed-publish:local (docker/Dockerfile.seed-publish)"
  image_build avenra-seed-publish --provenance=false --sbom=false -t avenra-seed-publish:local \
    --build-context "seed=$SEED_OUTPUT_DIR" \
    .

  kind_load avenra-seed-publish:local
}

# UI is a vanilla node:22-based build. NEXT_PUBLIC_API_URL is baked in at
# build time because Next.js freezes server-resolved env in
# .next/required-server-files.json; the local stack rendezvouses on the
# in-cluster Service DNS (Tilt uses the same value).
build_avenra_ui() {
  log "building runtime image: avenra-ui:local (docker/Dockerfile.ui)"
  image_build avenra-ui --provenance=false --sbom=false -t avenra-ui:local \
    --build-arg NEXT_PUBLIC_API_URL=http://avenra-api:8080 \
    --build-arg NEXT_PUBLIC_DATA_PLANE_URL=http://avenra-data-plane:8090 \
    .
  kind_load avenra-ui:local
}

# One list with the superseded-image prune (lib/avenra-images.sh), so every
# image built here also has its old builds removed from the nodes.
# shellcheck source=../lib/avenra-images.sh
. "$REPO_ROOT/infra/avenra/kind/lib/avenra-images.sh"
ALL_IMAGES=("${AVENRA_KIND_LOCAL_IMAGES[@]}")

dispatch() {
  case $1 in
    avenra-api)                    build_avenra_api ;;
    avenra-data-plane)             build_avenra_data_plane ;;
    avenra-cluster-controller)     build_avenra_cluster_controller ;;
    avenra-worker)                 build_avenra_worker ;;
    avenra-seed-publish)           build_avenra_seed_publish ;;
    avenra-kind-key-broker)        build_avenra_kind_key_broker ;;
    avenra-ui)                     build_avenra_ui ;;
    *)
      printf 'unknown image: %s\nknown: %s\n' "$1" "${ALL_IMAGES[*]}" >&2
      exit 2
      ;;
  esac
}

if (( LOAD_INTO_KIND )); then
  if ! kind get clusters 2>/dev/null | grep -qx "$KIND_CLUSTER"; then
    printf 'kind cluster %s not found; create it first or pass --no-load\n' "$KIND_CLUSTER" >&2
    exit 1
  fi
fi

TARGETS=()
if (( $# == 0 )) || [[ "$1" == "all" ]]; then
  TARGETS=("${ALL_IMAGES[@]}")
else
  TARGETS=("$@")
fi

if [[ "${AVENRA_KIND_PARALLEL:-0}" == "1" ]]; then
  log "parallel build of ${#TARGETS[@]} images (logs interleaved)"
  pids=()
  for img in "${TARGETS[@]}"; do
    (dispatch "$img") &
    pids+=($!)
  done
  fail=0
  for pid in "${pids[@]}"; do
    wait "$pid" || fail=1
  done
  exit "$fail"
fi

for img in "${TARGETS[@]}"; do
  dispatch "$img"
done

log "all done."
