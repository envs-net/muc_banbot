#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

# mutmut runs pytest inside ./mutants. An absolute PYTHONPATH pointing at the
# original checkout would shadow mutated modules and invalidate the result.
unset PYTHONPATH

command=${1:-run}
if [ "$#" -gt 0 ]; then
    shift
fi

case "$command" in
    fresh)
        python -m envs_xmpp_ops.regression mutation-tool-check
        rm -rf mutants
        exec mutmut run "$@"
        ;;
    run|results|browse)
        python -m envs_xmpp_ops.regression mutation-tool-check
        exec mutmut "$command" "$@"
        ;;
    check)
        exec python -m envs_xmpp_ops.regression mutation-check
        ;;
    accept)
        exec python -m envs_xmpp_ops.regression mutation-accept
        ;;
    *)
        printf 'Usage: %s [fresh|run|results|browse|check|accept] [mutmut arguments...]\n' "$0" >&2
        exit 2
        ;;
esac
