#!/usr/bin/env sh
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# Run one Cargo invocation with the release-build S3 cache. The BuildKit secret
# contains the pod's short-lived IRSA token and is never copied into an image
# layer. Cache availability is an optimization, so initialization fails open.
set -eu

if [ "${AVENRA_SCCACHE_ENABLED:-false}" != "true" ] || [ -z "${AVENRA_SCCACHE_BUCKET:-}" ]; then
    exec "$@"
fi

token_file="${AVENRA_SCCACHE_WEB_IDENTITY_TOKEN_FILE:-/run/secrets/aws_web_identity_token}"
if [ -z "${AVENRA_SCCACHE_ROLE_ARN:-}" ] || [ ! -r "${token_file}" ]; then
    echo "WARNING: sccache credentials are unavailable; continuing without compiler caching." >&2
    exec "$@"
fi

export AWS_ROLE_ARN="${AVENRA_SCCACHE_ROLE_ARN}"
export AWS_WEB_IDENTITY_TOKEN_FILE="${token_file}"
export AWS_REGION="${AVENRA_SCCACHE_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="${AWS_REGION}"
export SCCACHE_BUCKET="${AVENRA_SCCACHE_BUCKET}"
export SCCACHE_REGION="${AVENRA_SCCACHE_REGION:-us-east-1}"
export SCCACHE_S3_KEY_PREFIX="${AVENRA_SCCACHE_S3_KEY_PREFIX:-sccache/avenra/v1/release}"
export SCCACHE_S3_RW_MODE="${AVENRA_SCCACHE_S3_RW_MODE:-READ_WRITE}"
export SCCACHE_S3_SERVER_SIDE_ENCRYPTION="${AVENRA_SCCACHE_S3_SERVER_SIDE_ENCRYPTION:-true}"
export SCCACHE_BASEDIRS="${SCCACHE_BASEDIRS:-/workspace}"
export CARGO_INCREMENTAL=0
export RUSTC_WRAPPER=sccache

cleanup() {
    status=$?
    trap - 0
    echo "--- sccache release-build statistics"
    sccache --show-stats || true
    sccache --stop-server >/dev/null 2>&1 || true
    exit "${status}"
}
trap cleanup 0

if ! sccache --zero-stats; then
    echo "WARNING: sccache initialization failed; continuing without compiler caching." >&2
    unset RUSTC_WRAPPER
    trap - 0
    exec "$@"
fi

"$@"
