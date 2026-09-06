"""Stdlib-only bootstrap for the shared envs-xmpp deployment tooling."""

from __future__ import annotations

import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_REQUIRED_VERSION = "0.4.0"
_REQUIRED_SPEC = f"envs-xmpp=={_REQUIRED_VERSION}"


def _installed_version() -> str | None:
    try:
        from envs_xmpp_ops import __version__ as package_version
    except ImportError:
        try:
            package_version = version("envs-xmpp")
        except PackageNotFoundError:
            return None
    return package_version


def ensure_envs_xmpp() -> None:
    """Re-exec this deploy frontend inside a cached envs-xmpp deploy venv if needed."""
    if _installed_version() == _REQUIRED_VERSION:
        return

    cache_base = Path(
        os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
    ).expanduser()
    deploy_venv = cache_base / "envs-xmpp" / "deploy" / _REQUIRED_VERSION
    deploy_python = deploy_venv / "bin" / "python"

    if not deploy_python.is_file():
        subprocess.run(
            [sys.executable, "-m", "venv", str(deploy_venv)],
            check=True,
        )

    source = os.environ.get("ENVS_XMPP_DEPLOY_SOURCE", _REQUIRED_SPEC)
    subprocess.run(
        [
            str(deploy_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--upgrade",
            source,
        ],
        check=True,
    )

    os.execv(
        str(deploy_python),
        [str(deploy_python), str(Path(sys.argv[0]).resolve()), *sys.argv[1:]],
    )
