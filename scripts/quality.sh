#!/bin/sh
set -eu

ruff_fix_args=""
if [ "${1:-}" = "--fix" ]; then
  ruff_fix_args="--fix"
  shift
fi
if [ "$#" -ne 0 ]; then
  echo "usage: $0 [--fix]" >&2
  exit 2
fi

printf '%s\n' '[1/7] Python compilation'
python -m compileall -q muc_banbot.py banbot scripts

printf '%s\n' '[2/7] Configuration sample syntax'
python -m py_compile config_sample.py

printf '%s\n' '[3/7] Test suite (non-integration, warnings strict)'
./scripts/test.sh

printf '%s\n' '[4/7] Ruff: repository correctness'
ruff check $ruff_fix_args .

printf '%s\n' '[5/7] Ruff: unused imports (F401)'
ruff check $ruff_fix_args --select F401 --extend-exclude '**/__init__.py' .

printf '%s\n' '[6/7] mypy: typed operations/deployment modules'
mypy --follow-imports=skip banbot/task_supervisor.py banbot/runtime_watchdog.py scripts/deploy.py

printf '%s\n' '[7/7] Dependency audit (pip-audit)'
pip-audit -r requirements.txt

printf '%s\n' 'Quality checks passed (7/7).'
