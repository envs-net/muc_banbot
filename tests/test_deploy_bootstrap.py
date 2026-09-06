from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = ROOT / "scripts" / "_envs_xmpp_bootstrap.py"

spec = importlib.util.spec_from_file_location("deploy_bootstrap_under_test", BOOTSTRAP_PATH)
assert spec is not None and spec.loader is not None
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def test_matching_version_needs_no_bootstrap(monkeypatch):
    monkeypatch.setattr(bootstrap, "_installed_version", lambda: "0.4.1")
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("subprocess should not run"),
    )
    monkeypatch.setattr(
        bootstrap.os,
        "execv",
        lambda *_args, **_kwargs: pytest.fail("exec should not run"),
    )

    bootstrap.ensure_envs_xmpp()


def test_different_patch_version_bootstraps(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    class Reexec(Exception):
        pass

    monkeypatch.setattr(bootstrap, "_installed_version", lambda: "0.3.0")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("ENVS_XMPP_DEPLOY_SOURCE", raising=False)
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda command, **_kwargs: calls.append([str(part) for part in command]),
    )

    def execv(_executable, _argv):
        raise Reexec

    monkeypatch.setattr(bootstrap.os, "execv", execv)

    with pytest.raises(Reexec):
        bootstrap.ensure_envs_xmpp()

    deploy_python = tmp_path / "envs-xmpp" / "deploy" / "0.4.1" / "bin" / "python"
    assert calls[0] == [
        str(bootstrap.sys.executable),
        "-m",
        "venv",
        str(deploy_python.parent.parent),
    ]
    assert calls[1][-1] == "envs-xmpp==0.4.1"


def test_missing_version_bootstraps_source_override_and_reexecs(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    class Reexec(Exception):
        pass

    monkeypatch.setattr(bootstrap, "_installed_version", lambda: None)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("ENVS_XMPP_DEPLOY_SOURCE", "/tmp/envs-xmpp.whl")
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda command, **_kwargs: calls.append([str(part) for part in command]),
    )

    def execv(_executable, _argv):
        raise Reexec

    monkeypatch.setattr(bootstrap.os, "execv", execv)

    with pytest.raises(Reexec):
        bootstrap.ensure_envs_xmpp()

    deploy_python = tmp_path / "envs-xmpp" / "deploy" / "0.4.1" / "bin" / "python"
    assert calls[0] == [
        str(bootstrap.sys.executable),
        "-m",
        "venv",
        str(deploy_python.parent.parent),
    ]
    assert calls[1][:5] == [
        str(deploy_python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
    ]
    assert calls[1][-1] == "/tmp/envs-xmpp.whl"
