#!/bin/sh
set -eu

coverage=0
last_failed=0
durations=""

usage() {
  cat <<'USAGE'
Usage: ./scripts/test.sh [options] [pytest target ...]

Run muc_banbot's warning-strict non-integration pytest suite.

Options:
  --coverage       Enable repository coverage reporting.
  --last-failed    Re-run only tests that failed in the previous pytest run.
  --durations N    Show the N slowest tests.
  -h, --help       Show this help.

Live Prosody/XMPP integration tests remain opt-in.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --coverage)
      coverage=1
      shift
      ;;
    --last-failed)
      last_failed=1
      shift
      ;;
    --durations)
      if [ "$#" -lt 2 ] || ! case "$2" in *[!0-9]*|'') false;; *) true;; esac; then
        echo "test.sh: --durations requires a positive integer" >&2
        exit 2
      fi
      if [ "$2" -le 0 ]; then
        echo "test.sh: --durations requires a positive integer" >&2
        exit 2
      fi
      durations="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "test.sh: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      break
      ;;
  esac
done

if [ "$last_failed" -eq 1 ]; then
  set -- --lf "$@"
fi
if [ -n "$durations" ]; then
  set -- "--durations=$durations" --durations-min=0.1 "$@"
fi

coverage_args=""
if [ "$coverage" -eq 1 ]; then
  coverage_args="--cov=banbot --cov-report=term-missing"
fi

# Mirror envsbot's warning policy: coroutine leaks and deprecations fail the
# suite, while unrelated ResourceWarnings from optional test dependencies do
# not turn an otherwise valid release check red.
exec pytest \
  -o addopts= \
  -q \
  -W error::RuntimeWarning \
  -W error::DeprecationWarning \
  -m 'not integration' \
  $coverage_args \
  "$@"
