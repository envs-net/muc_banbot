#!/usr/bin/env python3
"""Validate the reviewed constraint snapshot for the running Python minor."""

from __future__ import annotations

import sys
from pathlib import Path

from check_constraints import check_constraints


def main() -> int:
    snapshot = Path("constraints") / f"python{sys.version_info.major}{sys.version_info.minor}.txt"
    if not snapshot.is_file():
        print(f"ERROR: audited dependency snapshot missing: {snapshot}")
        return 1
    errors = check_constraints(snapshot)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Constraint snapshot is complete for the running environment: {snapshot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
