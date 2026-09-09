#!/usr/bin/env python3
"""Require a release tag to match the BanBot package version exactly."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

VERSION_FILE = Path("banbot/_version.py")


def _project_version() -> str:
    tree = ast.parse(VERSION_FILE.read_text(encoding="utf-8"), filename=str(VERSION_FILE))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            continue
        expression = node.value
        if expression is None:
            continue
        value = ast.literal_eval(expression)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise RuntimeError(f"could not read __version__ from {VERSION_FILE}")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} vX.Y.Z", file=sys.stderr)
        return 2

    tag = sys.argv[1].strip()
    expected = f"v{_project_version()}"
    if tag != expected:
        print(
            f"release tag/version mismatch: tag={tag!r}, expected={expected!r}",
            file=sys.stderr,
        )
        return 1

    print(f"release tag matches project version: {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
