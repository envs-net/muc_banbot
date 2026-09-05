#!/bin/sh
set -eu

exec python -m envs_xmpp_ops.testing "$@"
