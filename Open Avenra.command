#!/bin/zsh
set -e
avenra_root="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$avenra_root"
python3 scripts/local.py start
open http://127.0.0.1:4827
