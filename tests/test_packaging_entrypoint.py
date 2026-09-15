from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from banbot import cli as cli_module

ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_console_entrypoint_targets_lightweight_cli() -> None:
    config = _pyproject()

    assert config["project"]["scripts"] == {
        "muc_banbot": "banbot.cli:main",
    }
    assert config["project"]["dynamic"] == ["version"]
    assert config["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "banbot._version.__version__",
    }


def test_legacy_launcher_still_delegates_to_lightweight_cli() -> None:
    launcher = (ROOT / "muc_banbot.py").read_text(encoding="utf-8")

    assert "from banbot.cli import main" in launcher
    assert 'if __name__ == "__main__":' in launcher
    assert "    main()" in launcher


def test_cli_version_does_not_start_runtime(monkeypatch, capsys) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli_module, "_run_bot", lambda: calls.append("run"))

    assert cli_module.main(["--version"]) == 0
    output = capsys.readouterr().out.strip()
    assert output.startswith("muc_banbot ")
    assert "(envs-xmpp " in output
    assert calls == []


def test_cli_short_version_is_supported(monkeypatch, capsys) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli_module, "_run_bot", lambda: calls.append("run"))

    assert cli_module.main(["-V"]) == 0
    assert capsys.readouterr().out.startswith("muc_banbot ")
    assert calls == []


def test_cli_help_does_not_start_runtime(monkeypatch, capsys) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli_module, "_run_bot", lambda: calls.append("run"))

    with pytest.raises(SystemExit) as exc_info:
        cli_module.main(["--help"])

    assert exc_info.value.code == 0
    assert "usage: muc_banbot" in capsys.readouterr().out
    assert calls == []


def test_cli_rejects_unknown_or_malformed_metadata_args(monkeypatch, capsys) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli_module, "_run_bot", lambda: calls.append("run"))

    with pytest.raises(SystemExit) as unknown:
        cli_module.main(["--hepl"])
    assert unknown.value.code == 2

    with pytest.raises(SystemExit) as malformed_version:
        cli_module.main(["--version", "unexpected"])
    assert malformed_version.value.code == 2

    assert "unrecognized arguments" in capsys.readouterr().err
    assert calls == []


def test_cli_delegates_normal_startup(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli_module, "_run_bot", lambda: calls.append("run"))

    assert cli_module.main([]) == 0
    assert calls == ["run"]


def test_systemd_service_uses_hardened_runtime_layout() -> None:
    service = (ROOT / "contrib" / "muc_banbot.service").read_text(encoding="utf-8")

    assert "Type=notify" in service
    assert "NotifyAccess=main" in service
    assert "WorkingDirectory=/srv/adminbot/muc_banbot" in service
    assert "Environment=PYTHONDONTWRITEBYTECODE=1" in service
    assert "Environment=MUC_BANBOT_CONFIG=/etc/muc_banbot/config.py" in service
    assert "ExecStart=/srv/adminbot/muc_banbot/venv/bin/muc_banbot" in service
    assert "Restart=on-failure" in service
    assert "WatchdogSec=60" in service
    assert "ProtectSystem=strict" in service
    assert "ReadWritePaths=/etc/muc_banbot /var/lib/muc_banbot" in service
    assert "ReadWritePaths=/srv/adminbot/muc_banbot" not in service
    assert "muc_banbot.py" not in service


def test_legacy_systemd_service_remains_available() -> None:
    service = (ROOT / "contrib" / "muc_banbot-legacy.service").read_text(
        encoding="utf-8"
    )

    assert "Type=simple" in service
    assert "WorkingDirectory=/srv/adminbot/muc_banbot" in service
    assert "Environment=MUC_BANBOT_CONFIG=" not in service
    assert "ReadWritePaths=/srv/adminbot/muc_banbot" in service
    assert "ExecStart=/srv/adminbot/muc_banbot/venv/bin/muc_banbot" in service


def test_package_exports_banbot_lazily() -> None:
    import banbot

    assert "BanBot" in banbot.__all__
    assert banbot.BanBot.__name__ == "BanBot"
