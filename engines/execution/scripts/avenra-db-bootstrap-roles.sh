#!/usr/bin/env bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# Converge control-plane Postgres roles via `avenra-db-migrator bootstrap-roles`.
#
# Reads POSTGRES_CONNECTION_STRING and the AVENRA_DB_BOOTSTRAP_*_PASSWORD
# variables from env or /avenra/config/space.env. Convergent and safe to
# re-run; extra arguments pass through to bootstrap-roles (e.g.
# --allow-missing-passwords for grants-only runs). See db/README.md for the
# role model and provisioning order.
#
# Usage:
#   avenra-db-bootstrap-roles.sh [extra bootstrap-roles args]
#
# Environment:
#   POSTGRES_CONNECTION_STRING     Admin connection URI (required; must
#                                  authenticate as a role that can create
#                                  roles — the RDS master user)
#   AVENRA_DB_BOOTSTRAP_*_PASSWORD  Managed role passwords
#   AVENRA_DB_MIGRATOR_BIN          Override migrator binary path
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATOR="${AVENRA_DB_MIGRATOR_BIN:-$SCRIPT_DIR/avenra-db-migrator}"

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
  exit 1
fi

exec "$MIGRATOR" bootstrap-roles \
  --database-url "$POSTGRES_CONNECTION_STRING" \
  "$@"
