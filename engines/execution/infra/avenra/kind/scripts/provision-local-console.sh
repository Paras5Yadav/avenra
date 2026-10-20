#!/usr/bin/env bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# Activate the Kind-only root@local login after API migrations complete.
# Called by both scripted bootstrap and Tilt; the password is never rendered
# into a Kubernetes resource, and --if-locked preserves an existing login.
set -euo pipefail
set +x

KIND_CLUSTER="${AVENRA_KIND_CLUSTER:-avenra-local}"
KCTX="kind-$KIND_CLUSTER"
NAMESPACE=space
CONSOLE_PASSWORD="${AVENRA_CONSOLE_PASSWORD-password}"
export -n CONSOLE_PASSWORD
unset AVENRA_CONSOLE_PASSWORD

[[ -n "$CONSOLE_PASSWORD" ]] || { printf 'AVENRA_CONSOLE_PASSWORD must not be empty\n' >&2; exit 1; }
[[ "$CONSOLE_PASSWORD" != *$'\n'* ]] || { printf 'AVENRA_CONSOLE_PASSWORD must not contain a newline\n' >&2; exit 1; }
password_bytes=$(printf '%s' "$CONSOLE_PASSWORD" | wc -c)
(( password_bytes <= 72 )) || { printf 'AVENRA_CONSOLE_PASSWORD must be at most 72 bytes\n' >&2; exit 1; }

kubectl --context "$KCTX" -n "$NAMESPACE" rollout status deploy/avenra-api \
  --timeout="${AVENRA_BOOTSTRAP_TIMEOUT:-600}s"
printf '[provision-local-console] activating root@local if locked\n'
printf '%s\n' "$CONSOLE_PASSWORD" |
  kubectl --context "$KCTX" -n "$NAMESPACE" exec -i deploy/avenra-api -c api -- sh -c '
    IFS= read -r AVENRA_USER_PASSWORD || exit 1
    export AVENRA_USER_PASSWORD
    exec /avenra/bin/avenra-db-migrator set-user-password \
      --database-url "$POSTGRES_CONNECTION_STRING" \
      --email root@local --if-locked
  '
unset CONSOLE_PASSWORD

# Previous Kind revisions persisted this password. No current pod consumes
# the Secret, so remove only that obsolete resource after provisioning works.
kubectl --context "$KCTX" -n "$NAMESPACE" delete secret avenra-console-bootstrap --ignore-not-found
