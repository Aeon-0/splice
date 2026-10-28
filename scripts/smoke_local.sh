#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
cd "$ROOT"
# In-process HTTP requests: no listening socket, Instagram or model download.
export SPLICE_OFFLINE=1
exec /Users/aeon/aeon-env/bin/python "$ROOT/scripts/smoke_local.py"
