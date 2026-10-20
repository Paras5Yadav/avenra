#!/bin/bash
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
# sandbox-manager-start.sh — Start sandbox-manager.
#
# Absolute path, not PATH lookup: credmgr's peer binary allowlist compares
# /proc/<pid>/exe against /opt/avenra/bin/avenra-sbxmgr-svc, and a PATH-mediated
# launch would couple that guarantee to the image's ENV PATH ordering.
set -euo pipefail

exec /opt/avenra/bin/avenra-sbxmgr-svc "$@"
