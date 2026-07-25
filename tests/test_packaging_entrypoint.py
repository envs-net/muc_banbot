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


def test_systemd_service_uses_installed_console_command() -> None:
    service = (ROOT / "contrib" / "muc_banbot.service").read_text(
        encoding="utf-8"
    )

    assert "WorkingDirectory=/srv/adminbot/muc_banbot" in service
    assert (
        "Environment=MUC_BANBOT_CONFIG=/srv/adminbot/muc_banbot/config.py"
        in service
    )
    assert "ExecStart=/srv/adminbot/muc_banbot/venv/bin/muc_banbot" in service
    assert "Restart=always" in service
    assert "ExecStart=" in service
    assert "muc_banbot.py" not in service
