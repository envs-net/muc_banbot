"""Regression coverage for BanBot's XMPP session-start handler registration."""

from __future__ import annotations

import ast
from pathlib import Path


def _is_self_attribute(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == name
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def test_banbot_registers_own_start_handler_for_session_start() -> None:
    source_path = Path(__file__).resolve().parents[1] / "banbot" / "bot.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))

    handlers: list[ast.AST] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_self_attribute(node.func, "add_event_handler"):
            continue
        if len(node.args) < 2:
            continue
        event_name = node.args[0]
        if (
            isinstance(event_name, ast.Constant)
            and event_name.value == "session_start"
        ):
            handlers.append(node.args[1])

    assert len(handlers) == 1
    assert _is_self_attribute(handlers[0], "start")
