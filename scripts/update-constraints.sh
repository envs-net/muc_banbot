#!/bin/sh
set -eu

usage() {
    echo "usage: $0 <3.12|3.13|3.14> [--refresh]" >&2
    exit 2
}

version=${1:-}
mode=${2:-}
case "$version" in
    3.12) interpreter=${PYTHON312:-python3.12}; output=constraints/python312.txt ;;
    3.13) interpreter=${PYTHON313:-python3.13}; output=constraints/python313.txt ;;
    3.14) interpreter=${PYTHON314:-python3.14}; output=constraints/python314.txt ;;
    *) usage ;;
esac
case "$mode" in
    ""|--refresh) ;;
    *) usage ;;
esac

command -v "$interpreter" >/dev/null 2>&1 || {
    echo "interpreter not found: $interpreter" >&2
    exit 1
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT INT TERM
"$interpreter" -m venv "$tmp/venv"

if [ "$mode" = "--refresh" ]; then
    # Resolve a fresh set within requirements.txt / requirements-dev.txt ranges.
    "$tmp/venv/bin/python" -m pip install -r requirements.txt -r requirements-dev.txt
else
    # Reproduce the checked-in snapshot exactly before writing it back out.
    "$tmp/venv/bin/python" -m pip install -c "$output" -r requirements.txt -r requirements-dev.txt
fi

{
    echo "# Fully resolved dependency snapshot for Python $version."
    if [ "$mode" = "--refresh" ]; then
        echo "# Refreshed by scripts/update-constraints.sh $version --refresh."
    else
        echo "# Reproduced by scripts/update-constraints.sh $version."
    fi
    "$tmp/venv/bin/python" -m pip freeze --all \
        | grep -v '^pip==' \
        | grep -v '^setuptools==' \
        | grep -v '^wheel==' \
        | sort -f
} > "$output"

"$tmp/venv/bin/python" scripts/check_constraints.py "$output"
echo "updated $output"
