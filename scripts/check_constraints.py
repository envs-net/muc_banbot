#!/usr/bin/env python3
"""Verify that a constraint snapshot pins the complete installed dependency closure."""

from __future__ import annotations

import argparse
import importlib.metadata
from collections import deque
from collections.abc import Mapping, Set
from pathlib import Path
from typing import cast

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

_REQUIREMENT_FILES = (Path("requirements.txt"), Path("requirements-dev.txt"))
_BOOTSTRAP_DISTRIBUTIONS = {"pip", "setuptools", "wheel"}


def _iter_requirement_lines(path: Path):
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            yield line


def _root_requirements() -> list[Requirement]:
    roots: list[Requirement] = []
    env = cast(Mapping[str, str | Set[str]], default_environment())
    for path in _REQUIREMENT_FILES:
        for line in _iter_requirement_lines(path):
            requirement = Requirement(line)
            if requirement.marker is None or requirement.marker.evaluate(env):
                roots.append(requirement)
    return roots


def _constraint_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line or line.count("==") != 1:
            raise ValueError(f"constraint is not an exact pin: {line}")
        name, version = line.split("==", 1)
        if not name.strip() or not version.strip():
            raise ValueError(f"invalid constraint pin: {line}")
        pins[canonicalize_name(name.strip())] = version.strip()
    return pins


def _marker_matches(requirement: Requirement, parent_extras: frozenset[str]) -> bool:
    if requirement.marker is None:
        return True
    env = cast(Mapping[str, str | Set[str]], default_environment())
    extras = parent_extras or frozenset({""})
    for extra in extras:
        candidate: dict[str, str | Set[str]] = dict(env)
        candidate["extra"] = extra
        if requirement.marker.evaluate(candidate):
            return True
    return False


def dependency_closure() -> dict[str, str]:
    """Return installed versions reachable from the declared requirements."""
    requested_extras: dict[str, set[str]] = {}
    queue: deque[tuple[str, frozenset[str]]] = deque()
    for requirement in _root_requirements():
        queue.append((canonicalize_name(requirement.name), frozenset(requirement.extras)))

    versions: dict[str, str] = {}
    processed: set[tuple[str, frozenset[str]]] = set()
    while queue:
        name, extras = queue.popleft()
        if name in _BOOTSTRAP_DISTRIBUTIONS:
            continue
        known_extras = requested_extras.setdefault(name, set())
        if extras.issubset(known_extras) and name in versions:
            continue
        known_extras.update(extras)
        effective_extras = frozenset(known_extras)
        marker_key = (name, effective_extras)
        if marker_key in processed:
            continue
        processed.add(marker_key)

        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(f"required distribution is not installed: {name}") from exc

        versions[name] = distribution.version
        for raw_dependency in distribution.requires or ():
            dependency = Requirement(raw_dependency)
            if not _marker_matches(dependency, effective_extras):
                continue
            queue.append(
                (
                    canonicalize_name(dependency.name),
                    frozenset(dependency.extras),
                )
            )
    return versions


def check_constraints(path: Path) -> list[str]:
    pins = _constraint_pins(path)
    installed = dependency_closure()
    errors: list[str] = []
    for name, version in sorted(installed.items()):
        pinned = pins.get(name)
        if pinned is None:
            errors.append(f"missing transitive pin: {name}=={version}")
        elif pinned != version:
            errors.append(f"version mismatch: {name} installed={version} pinned={pinned}")
    for name, version in sorted(pins.items()):
        if name not in installed:
            errors.append(f"stale or unreachable pin: {name}=={version}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("constraints", type=Path)
    args = parser.parse_args()
    errors = check_constraints(args.constraints)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Constraint snapshot is complete for the installed dependency closure: {args.constraints}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
