#!/usr/bin/env bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# Run pending control-plane Postgres migrations using SQLx.
#
# Reads POSTGRES_CONNECTION_STRING from env or /avenra/config/space.env.
# Platform-agnostic — usable by deployment tooling, k8s init containers,
# or manual SSH invocation.
#
# Usage:
#   avenra-db-migrate.sh              # apply pending migrations
#   avenra-db-migrate.sh status       # show migration status
#   avenra-db-migrate.sh down         # rollback last migration
#
# Environment:
#   POSTGRES_CONNECTION_STRING   Control-plane Postgres connection URI (required)
#   AVENRA_DB_MIGRATOR_BIN        Override migrator binary path
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATOR="${AVENRA_DB_MIGRATOR_BIN:-$SCRIPT_DIR/avenra-db-migrator}"
COMMAND="${1:-up}"

# Load connection string from space.env if not already in env
if [[ -z "${POSTGRES_CONNECTION_STRING:-}" ]]; then
  if [[ -f /avenra/config/space.env ]]; then
    POSTGRES_CONNECTION_STRING=$(grep '^POSTGRES_CONNECTION_STRING=' /avenra/config/space.env | cut -d= -f2-)
    export POSTGRES_CONNECTION_STRING
  fi
fi

if [[ -z "${POSTGRES_CONNECTION_STRING:-}" ]]; then
  echo "ERROR: POSTGRES_CONNECTION_STRING not set and not found in /avenra/config/space.env" >&2
  exit 1
fi

if [[ ! -x "$MIGRATOR" ]]; then
  echo "ERROR: avenra-db-migrator not found at $MIGRATOR" >&2
  echo "Build avenra-db-migrator (cargo build --locked --bin avenra-db-migrator) and set AVENRA_DB_MIGRATOR_BIN to its executable path." >&2
  exit 1
fi

exec "$MIGRATOR" control-plane \
  --database-url "$POSTGRES_CONNECTION_STRING" \
  "$COMMAND"
