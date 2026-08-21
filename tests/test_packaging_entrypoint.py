from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_console_entrypoint_targets_bot_main() -> None:
    config = _pyproject()

    assert config["project"]["scripts"] == {
        "muc_banbot": "banbot.bot:main",
    }
    assert config["project"]["dynamic"] == ["version"]
    assert config["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "banbot._version.__version__",
    }


def test_legacy_launcher_still_delegates_to_bot_main() -> None:
    launcher = (ROOT / "muc_banbot.py").read_text(encoding="utf-8")

    assert "from banbot.bot import main" in launcher
    assert 'if __name__ == "__main__":' in launcher
    assert "    main()" in launcher


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
