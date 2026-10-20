#!/usr/bin/env bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# Run pending node-local SQLite migrations for Avenra node services using SQLx.
#
# Usage:
#   avenra-sbx-db-migrate.sh              # apply pending migrations
#   avenra-sbx-db-migrate.sh status       # show migration status
#   avenra-sbx-db-migrate.sh down         # rollback last migration
#   CONFIRM_DATA_LOSS=1 avenra-sbx-db-migrate.sh reset
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATOR="${AVENRA_DB_MIGRATOR_BIN:-$SCRIPT_DIR/avenra-db-migrator}"
COMMAND="${1:-up}"
CONFIRM_ARGS=()

if [[ "$COMMAND" == "reset" ]]; then
  if [[ "${CONFIRM_DATA_LOSS:-}" != "1" ]]; then
    echo "ERROR: reset requires CONFIRM_DATA_LOSS=1" >&2
    exit 2
  fi
  CONFIRM_ARGS=(--confirm-data-loss)
fi

if [[ ! -x "$MIGRATOR" ]]; then
  echo "ERROR: avenra-db-migrator not found at $MIGRATOR" >&2
  exit 1
fi

run_store() {
  local store="$1"
  local db_path="$2"

  "$MIGRATOR" "$store" \
    --database-path "$db_path" \
    "${CONFIRM_ARGS[@]}" \
    "$COMMAND"
  chmod 0600 "$db_path" "$db_path-shm" "$db_path-wal" 2>/dev/null || true
}

run_store sandbox-manager \
  "${AVENRA_SBX_DB_PATH:-/avenra/run/sandbox-manager/sbx-manager.db}"
run_store template-manager \
  "${AVENRA_TEMPLATE_DB_PATH:-/avenra/run/template-manager/template-manager.db}"
run_store node-manager \
  "${AVENRA_NODE_MANAGER_DB_PATH:-/avenra/run/node-manager/node-manager.db}"
run_store node-ctl \
  "${AVENRA_NODE_CTL_DB_PATH:-/avenra/run/node-ctl/node-ctl.db}"
