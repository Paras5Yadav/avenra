#!/usr/bin/env bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# node-services.sh — the node-service releases the Kind harness deploys, one
# DaemonSet per service per role, as production runs them.
#
# Source it for the arrays and helpers, or run it to print one
# `<release> <role> <service>` line per release (the Tiltfile reads that).
# Each service's chart is infra/avenra/kind/charts/avenra-<service>-local, its
# DaemonSet `<release>-<service>`, and its builder overrides, if any, the
# chart's values-builder.yaml.

# Sandbox workers (release `space`) run these; builders (release
# `avenra-builder`) run them plus build-manager.
AVENRA_KIND_NODE_SERVICES=(
  node-manager
  runtime-manager
  volume-manager
  credential-manager
  template-manager
  sandbox-manager
  node-ctl
  node-api
)
AVENRA_KIND_BUILDER_ONLY_SERVICES=(build-manager)

# Prints `<release> <role> <service>` for every node-service release.
avenra_kind_node_releases() {
  local service
  for service in "${AVENRA_KIND_NODE_SERVICES[@]}"; do
    printf 'space worker %s\n' "$service"
  done
  for service in "${AVENRA_KIND_NODE_SERVICES[@]}" "${AVENRA_KIND_BUILDER_ONLY_SERVICES[@]}"; do
    printf 'avenra-builder builder %s\n' "$service"
  done
}

# Prints every node-service DaemonSet name, plus the Kind-only operator shell.
avenra_kind_node_daemonsets() {
  local release _role service
  while read -r release _role service; do
    printf '%s-%s\n' "$release" "$service"
  done < <(avenra_kind_node_releases)
  printf 'avenra-node-debug\n'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  case "${1:-releases}" in
    releases) avenra_kind_node_releases ;;
    daemonsets) avenra_kind_node_daemonsets ;;
    *) echo "usage: $0 [releases|daemonsets]" >&2; exit 2 ;;
  esac
fi
