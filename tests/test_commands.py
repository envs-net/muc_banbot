"""Shared command contracts used by both bot adapters."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from envs_xmpp_core.commands import (
    CommandExample,
    CommandSpec,
    SubcommandSpec,
    command_spec_from,
    command_tokens,
    format_command_examples,
    format_command_usage,
    is_command_family,
    normalize_command_example,
    normalize_command_examples,
    normalize_subcommand,
    parse_prefixed_command,
    render_subcommand_usage,
    resolve_help_topic,
    resolve_longest_command,
)


def test_example_normalizes_existing_documentation_forms() -> None:
    assert normalize_command_example("ping") == CommandExample("ping")
    assert normalize_command_example(("ping", "Try it")) == CommandExample("ping", "Try it")
    assert normalize_command_example({"example": "ping", "short": "Try it"}) == CommandExample("ping", "Try it")
    assert normalize_command_example({"command": "ping", "description": None}) == CommandExample("ping")
    assert normalize_command_examples("ping") == (CommandExample("ping"),)
    assert normalize_command_examples(None) == ()


def test_subcommand_normalization_keeps_aliases_and_delegates_role_policy() -> None:
    spec = normalize_subcommand(
        {
            "name": "reset",
            "usage": "{prefix}omemo reset confirm",
            "description": "Rotate identity",
            "aliases": "clear",
            "examples": [{"command": "!omemo reset confirm"}],
            "role": "ADMIN",
            "section": "Maintenance",
        },
        parse_role=lambda value: str(value).lower(),
    )
    assert spec == SubcommandSpec(
        "reset",
        "{prefix}omemo reset confirm",
        "Rotate identity",
        ("clear",),
        (CommandExample("!omemo reset confirm"),),
        "admin",
        "",
        "Maintenance",
    )
    assert normalize_subcommand(spec) is spec
    with pytest.raises(TypeError):
        normalize_subcommand(42)


@dataclass
class DecoratedCommand:
    name: str = "demo"
    role: str = "admin"
    usage: str = "{prefix}demo"
    aliases: tuple[str, ...] = ("d",)
    examples: list[str] | None = None
    subcommands: list[dict[str, object]] | None = None


def test_command_spec_snapshots_decorator_metadata() -> None:
    command = DecoratedCommand(examples=["{prefix}demo"], subcommands=[{"name": "run", "usage": "run"}])
    spec = command_spec_from(command)
    assert spec.name == "demo"
    assert spec.role == "admin"
    assert spec.aliases == ("d",)
    assert spec.examples == (CommandExample("{prefix}demo"),)
    assert spec.subcommands[0].name == "run"
    command.examples.append("changed")  # type: ignore[union-attr]
    assert spec.examples == (CommandExample("{prefix}demo"),)


def test_longest_command_matching_and_unmodified_args() -> None:
    commands = {("room",): "room", ("room", "invite"): "invite", ("whoami",): "whoami"}
    assert resolve_longest_command("ROOM invite Alice@Example.Org", commands) == ("invite", ["Alice@Example.Org"])
    assert resolve_longest_command("room list", commands) == ("room", ["list"])
    assert resolve_longest_command("missing x", commands) == (None, ["missing", "x"])
    assert resolve_longest_command("", commands) == (None, [])
    assert command_tokens("  HELLO  World ") == ("hello", "world")
    assert is_command_family("room", commands)
    assert not is_command_family("room invite", commands)


def test_help_topic_aliases_support_nested_topics_without_changing_dispatch() -> None:
    aliases = {"rooms": "room", "room invites": "room invite", "rtbl pub": "rtbl publish"}
    assert resolve_help_topic("ROOMS invite", aliases) == "room invite"
    assert resolve_help_topic("room invites", aliases) == "room invite"
    assert resolve_help_topic("rooms invites", aliases) == "room invite"
    assert resolve_help_topic("a", {"a": "b", "b": "a"}) in {"a", "b"}
    assert resolve_help_topic(["RTBL", "PUB"], aliases) == "rtbl publish"
    assert resolve_help_topic("nonexistent", aliases) == "nonexistent"
    assert resolve_help_topic("", aliases) == ""


def test_usage_examples_and_subcommands_render_consistently() -> None:
    assert format_command_usage("{prefix}status", "!") == ["!status"]
    assert format_command_usage("", "!") == []
    assert format_command_examples([CommandExample("{prefix}demo", "Run {prefix}demo")], "!") == [
        CommandExample("!demo", "Run !demo")
    ]
    spec: CommandSpec[object] = CommandSpec(
        name="omemo",
        subcommands=(
            SubcommandSpec("status", "{prefix}omemo status", "Show state"),
            SubcommandSpec("reset", "{prefix}omemo reset [confirm]", "Rotate"),
        ),
    )
    assert render_subcommand_usage(spec, "!") == "Usage:\n  !omemo status\n  !omemo reset [confirm]"


def test_prefix_parsing_preserves_original_arguments() -> None:
    assert parse_prefixed_command("!BAN Alice@Example.org reason", "!") == (
        "ban",
        ["Alice@Example.org", "reason"],
    )
    assert parse_prefixed_command("hello", "!") is None
    assert parse_prefixed_command("!help", "") is None
    assert parse_prefixed_command("!", "!") == ("", [])


def test_command_help_document_preserves_inline_layout() -> None:
    from envs_xmpp_core.commands import CommandHelpDocument, render_command_help_document

    spec: CommandSpec[object] = CommandSpec(name="whoami", usage="{prefix}whoami")
    document = CommandHelpDocument(spec, inline=True)
    assert render_command_help_document(document, "!") == "Usage: !whoami"
    assert render_command_help_document(document, "//") == "Usage: //whoami"


def test_command_help_document_renders_examples_and_literal_notes() -> None:
    from envs_xmpp_core.commands import CommandHelpDocument, render_command_help_document

    spec: CommandSpec[object] = CommandSpec(
        name="policy",
        subcommands=(
            SubcommandSpec("show", "{prefix}policy show", ""),
            SubcommandSpec("set", "{prefix}policy set <text>", ""),
        ),
        examples=(CommandExample("{prefix}policy show"),),
    )
    doc = CommandHelpDocument(spec, notes="Supported placeholders: {prefix}, {room}", trailing_newlines=2)
    assert render_command_help_document(doc, "!") == (
        "Usage:\n  !policy show\n  !policy set <text>\n\n"
        "Examples:\n  !policy show\n\n"
        "Supported placeholders: {prefix}, {room}\n\n"
    )


def test_command_help_document_rejects_invalid_inline_subcommands() -> None:
    from envs_xmpp_core.commands import CommandHelpDocument, render_command_help_document

    spec: CommandSpec[object] = CommandSpec(
        name="demo", usage="{prefix}demo", subcommands=(SubcommandSpec("run", "{prefix}demo run", ""),)
    )
    with pytest.raises(ValueError, match="Inline help"):
        render_command_help_document(CommandHelpDocument(spec, inline=True), "!")


def test_sectioned_command_help_renders_stable_order_and_custom_prefix() -> None:
    from envs_xmpp_core.commands import CommandHelpSection, render_command_help_sections

    sections = (
        CommandHelpSection("Runtime", ("{prefix}status - status", "{prefix}restart confirm - restart")),
        CommandHelpSection("OMEMO", ("{prefix}omemo status - show state",)),
    )
    assert render_command_help_sections(sections, "!") == (
        "Runtime\n!status - status\n!restart confirm - restart\n\nOMEMO\n!omemo status - show state"
    )
    assert render_command_help_sections(sections, "/") == (
        "Runtime\n/status - status\n/restart confirm - restart\n\nOMEMO\n/omemo status - show state"
    )
    assert render_command_help_sections((), "!") == ""
