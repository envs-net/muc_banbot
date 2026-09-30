#!/bin/sh
set -eu
# Shared implementation; run with the project's active development Python.
exec "${PYTHON:-python3}" -m envs_xmpp_ops.constraints_update "$@"
