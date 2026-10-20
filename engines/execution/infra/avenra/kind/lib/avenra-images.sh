#!/usr/bin/env bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# avenra-images.sh — the Avenra images the Kind harness builds locally and loads
# into every node as `<name>:local` (build-images.sh) or under Tilt's
# `<name>:tilt-<hash>` rewrite of that ref (the Tiltfile's custom_builds).
#
# Source it for the array, or run it to print one name per line (the
# superseded-image prune reads that). These are the only repositories
# prune-superseded-images.py may remove tags of: Kind cannot re-pull them, so
# every other image in a node's store is left alone. Each name's Dockerfile
# sets `org.opencontainers.image.title` to that name, which is how the prune
# recognizes one of these images after its tag has moved to a newer build.

AVENRA_KIND_LOCAL_IMAGES=(
  avenra-api
  avenra-data-plane
  avenra-cluster-controller
  avenra-worker
  avenra-seed-publish
  avenra-ui
  avenra-kind-key-broker
)

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  printf '%s\n' "${AVENRA_KIND_LOCAL_IMAGES[@]}"
fi
