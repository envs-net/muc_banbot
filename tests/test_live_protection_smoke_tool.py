from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "live_protection_smoke.py"


def _load_tool_module():
    spec = importlib.util.spec_from_file_location("muc_banbot_live_protection_smoke", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_main_preserves_explicit_empty_argv(monkeypatch) -> None:
    tool = _load_tool_module()
    seen: list[list[str]] = []

    def fake_parse_args(argv: list[str]):
        seen.append(argv)
        raise RuntimeError("stop before destructive smoke execution")

    monkeypatch.setattr(tool, "parse_args", fake_parse_args)
    monkeypatch.setattr(sys, "argv", ["live_protection_smoke.py", "--destructive"])

    with pytest.raises(RuntimeError, match="stop before destructive smoke execution"):
        tool.main([])

    assert seen == [[]]


def test_env_default_uses_fallback_for_missing_or_empty_values(monkeypatch) -> None:
    tool = _load_tool_module()

    monkeypatch.delenv("BANBOT_SMOKE_TEST_VALUE", raising=False)
    assert tool.env_default("BANBOT_SMOKE_TEST_VALUE", "5") == "5"

    monkeypatch.setenv("BANBOT_SMOKE_TEST_VALUE", "")
    assert tool.env_default("BANBOT_SMOKE_TEST_VALUE", "5") == "5"

    monkeypatch.setenv("BANBOT_SMOKE_TEST_VALUE", "7")
    assert tool.env_default("BANBOT_SMOKE_TEST_VALUE", "5") == "7"
