#!/usr/bin/env bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
AVENRA_API_URL="${AVENRA_API_URL:-http://127.0.0.1:8080}"

if [[ -z "${AVENRA_API_TOKEN:-}" ]]; then
  echo "ERROR: AVENRA_API_TOKEN is required" >&2
  exit 1
fi

work_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT

spec="$work_dir/openapi-m1.json"
curl -fsS "${AVENRA_API_URL%/}/openapi-m1.json" -o "$spec"

export AVENRA_M1_GENERATED_SDK_DIR="$work_dir/generated"
mkdir -p "$AVENRA_M1_GENERATED_SDK_DIR"
bash "$REPO_ROOT/scripts/check-m1-sdk-generation.sh" "$spec"

uv_bin="$(bash "$REPO_ROOT/ci/scripts/ensure-uv.sh")"
PYTHONPATH="$AVENRA_M1_GENERATED_SDK_DIR" \
  "$uv_bin" run --frozen --python 3.11 \
  --script "$SCRIPT_DIR/smoke-avenra-m1-conformance.py" "$spec"
