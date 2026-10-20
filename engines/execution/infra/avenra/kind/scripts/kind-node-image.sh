#!/usr/bin/env bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# Provide the Kind node image for Avenra sandbox workers and builders
# (infra/avenra/kind/node/Dockerfile) without rebuilding it on every bring-up.
#
# The image is content-addressed, like the CI image (ci/scripts/ci-image.sh).
# Its tag is the first 16 characters of the SHA-256 over the files it is built
# from (INPUTS below, each hashed with its path). The build context holds those
# files and nothing else, so the tag names everything that can reach the image.
#
#   ref      print the image reference for the current tree
#   base     print the pinned kindest/node base (the Dockerfile's FROM)
#   inputs   print the files the tag covers, one per line
#   ensure   make the reference available locally: use a local image with that
#            tag, else pull it from GHCR, else build it. Main CI publishes each
#            new tag, so a registry miss (a tag main has not published yet, or
#            no access) costs a local build, never a failed bring-up.
#   build    build the reference locally, whatever is present
#   publish  push the reference to GHCR unless it is already there (main CI)
#
# GHCR_TOKEN, when set, logs in to the registry first.
# AVENRA_KIND_NODE_IMAGE_REPOSITORY overrides the repository. Builds take the
# base from the repository's third-party image mirror when AVENRA_CI_IMAGE_MIRROR
# is set and the mirror has it (ci/mirrored-images.txt), as the CI image does;
# otherwise from the Dockerfile's own FROM.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
REPOSITORY="${AVENRA_KIND_NODE_IMAGE_REPOSITORY:-ghcr.io/avenra/execution-kind-node}"
SOURCE_URL="https://github.com/avenra/Avenra"
DOCKERFILE=infra/avenra/kind/node/Dockerfile
# Everything the Dockerfile copies, and the Dockerfile itself. A new COPY has to
# be listed here, or the tag would not change when that file does; the build
# context is staged from this list, so an unlisted file fails the build instead
# of silently reaching the image.
INPUTS=(
  "${DOCKERFILE}"
  infra/avenra/kind/node/avenra-kind-node.sh
  infra/avenra/kind/node/containerd/avenra-runtime.toml
  infra/avenra/kind/node/kubelet/50-space.conf
  infra/avenra/kind/node/systemd/avenra-cache.service
  infra/avenra/kind/node/systemd/avenra-cgroup.service
  infra/avenra/kind/node/systemd/avenra-kind-devices.service
  infra/avenra/kind/node/systemd/avenra-kind-vsock-jails.service
  infra/avenra/kind/node/systemd/avenra-layout.service
  infra/avenra/kind/node/systemd/avenra-loop-nodes.service
  infra/avenra/identity/node-identities.sh
  scripts/lib/avenra-loop-devices.sh
)
# Part of the tag, so that publishing the same inputs in a new form produces a
# new tag instead of reusing the old one.
PUBLISH_FORMAT="zstd"
BUILDER="avenra-kind-node-image"

log() { printf '[kind-node-image] %s\n' "$*" >&2; }

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$@"
  else
    shasum -a 256 "$@"
  fi
}

image_tag() {
  (cd "${REPO_ROOT}" && { sha256 "${INPUTS[@]}"; echo "format: ${PUBLISH_FORMAT}"; } | sha256 | cut -c1-16)
}

image_ref() {
  printf '%s:%s\n' "${REPOSITORY}" "$(image_tag)"
}

base_ref() {
  awk 'toupper($1) == "FROM" { print $2; exit }' "${REPO_ROOT}/${DOCKERFILE}"
}

registry_login() {
  [ -n "${GHCR_TOKEN:-}" ] || return 0
  printf '%s' "${GHCR_TOKEN}" |
    docker login "${REPOSITORY%%/*}" --username "${GITHUB_ACTOR:-avenra-ci}" \
      --password-stdin >/dev/null
}

# A build context of exactly the inputs, at their repository paths.
stage_context() {
  local dir="$1" input
  for input in "${INPUTS[@]}"; do
    install -D -m 0644 "${REPO_ROOT}/${input}" "${dir}/${input}"
  done
}

# The --build-context options that take the Dockerfile's base from the mirror.
mirrored_base() {
  bash "${REPO_ROOT}/ci/scripts/mirror-images.sh" build-contexts "${REPO_ROOT}/${DOCKERFILE}"
}

build_local() {
  local ref context status=0
  ref="$(image_ref)"
  context="$(mktemp -d)"
  stage_context "${context}"
  log "building ${ref}"
  # shellcheck disable=SC2046 # one option per line, none containing spaces
  docker build --provenance=false --sbom=false --file "${context}/${DOCKERFILE}" --tag "${ref}" \
    --build-arg "AVENRA_KIND_NODE_IMAGE_TAG=${ref##*:}" \
    --label "org.opencontainers.image.source=${SOURCE_URL}" \
    $(mirrored_base) "${context}" >&2 || status=$?
  rm -rf "${context}"
  return "${status}"
}

ensure() {
  local ref error
  ref="$(image_ref)"
  if docker image inspect "${ref}" >/dev/null 2>&1; then
    log "using the local ${ref}"
  else
    if ! registry_login; then
      log "WARNING: could not log in to ${REPOSITORY%%/*}; the pull below may fail"
    fi
    if error="$(docker pull --quiet "${ref}" 2>&1 >/dev/null)"; then
      log "pulled the published ${ref}"
    else
      log "building ${ref} locally; it is not published or not reachable: ${error%%$'\n'*}"
      build_local
    fi
  fi
  printf '%s\n' "${ref}"
}

publish() {
  local ref context status=0
  ref="$(image_ref)"
  registry_login
  if docker manifest inspect "${ref}" >/dev/null 2>&1; then
    echo "${ref} is already published"
    return 0
  fi
  if ! docker buildx inspect "${BUILDER}" >/dev/null 2>&1; then
    docker buildx create --name "${BUILDER}" --driver docker-container >/dev/null
  fi
  context="$(mktemp -d)"
  stage_context "${context}"
  # zstd layers unpack faster than gzip; the docker driver cannot choose layer
  # compression, so build on a container builder and push from it.
  # shellcheck disable=SC2046 # one option per line, none containing spaces
  docker buildx build --builder "${BUILDER}" \
    --file "${context}/${DOCKERFILE}" $(mirrored_base) \
    --build-arg "AVENRA_KIND_NODE_IMAGE_TAG=${ref##*:}" \
    --label "org.opencontainers.image.source=${SOURCE_URL}" \
    --output "type=registry,name=${ref},oci-mediatypes=true,compression=${PUBLISH_FORMAT},force-compression=true" \
    "${context}" || status=$?
  rm -rf "${context}"
  return "${status}"
}

case "${1:-}" in
  ref) image_ref ;;
  base) base_ref ;;
  inputs) printf '%s\n' "${INPUTS[@]}" ;;
  ensure) ensure ;;
  build) build_local && image_ref ;;
  publish) publish ;;
  *)
    echo "usage: $0 ref|base|inputs|ensure|build|publish" >&2
    exit 2
    ;;
esac
