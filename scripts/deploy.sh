#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON=${MUC_BANBOT_DEPLOY_PYTHON:-python3}

exec "$PYTHON" "$SCRIPT_DIR/deploy.py" "$@"
