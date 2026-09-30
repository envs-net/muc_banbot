"""BanBot adapter contracts for shared command metadata and help resolution."""

from banbot.commands.help import CommandHelpMixin
from banbot.commands.help_specs import HELP_COMMAND_SPECS, HELP_TOPIC_METHODS, find_help_command
from banbot.commands.usage import CommandUsageMixin


class HelpBot(CommandHelpMixin, CommandUsageMixin):
    command_prefix = "!"


def test_help_spec_registry_is_complete_and_distinct() -> None:
    assert set(HELP_TOPIC_METHODS) == {spec.name for spec in HELP_COMMAND_SPECS}
    assert len(HELP_COMMAND_SPECS) == len(HELP_TOPIC_METHODS)


def test_alias_and_nested_help_uses_shared_topic_normalizer() -> None:
    bot = HelpBot()
    assert find_help_command("ROOMS invite").name == "room invite"
    assert find_help_command("room invites").name == "room invite"
    assert find_help_command("rooms invites").name == "room invite"
    assert find_help_command("RTBL pub").name == "rtbl publish"
    assert find_help_command("whitelist").name == "ignore"
    assert find_help_command("missing") is None
    assert "!room invite accept <id>" in bot._admin_topic_help_text("ROOMS invite")
    assert "!omemo reset [confirm]" in bot._admin_topic_help_text("omemo")
    assert "Unknown help topic" in bot._admin_topic_help_text("unknown topic")


def test_structured_usage_keeps_existing_banbot_help_text() -> None:
    bot = HelpBot()
    assert bot._omemo_usage_text() == (
        "Usage:\n  !omemo status\n  !omemo devices\n  !omemo reset [confirm]\n  !omemo help"
    )
    assert bot._room_invite_usage_text() == (
        "Usage:\n  !room invite list [all|page|last]\n  !room invite accept <id>\n"
        "  !room invite decline/remove/delete/del/rm <id>\n  !room invite cleanup [expired]"
    )


def test_structured_catalog_matches_legacy_help_for_all_commands_and_prefixes() -> None:
    """Phase 3B: preserve every existing help response byte-for-byte."""
    import json
    from pathlib import Path

    from banbot.commands.usage_specs import USAGE_DOCUMENTS

    expected = json.loads((Path(__file__).parent / "fixtures" / "command_usage_legacy.json").read_text("utf-8"))
    bot = HelpBot()
    assert len(expected) == len(USAGE_DOCUMENTS) == 35
    for method, prefixes in expected.items():
        for prefix, legacy in prefixes.items():
            bot.command_prefix = prefix
            assert getattr(bot, method)() == legacy, (method, prefix)


def test_help_registry_now_carries_real_command_usage_metadata() -> None:
    from banbot.commands.usage_specs import USAGE_DOCUMENTS

    assert len(HELP_COMMAND_SPECS) == 36
    assert find_help_command("room invite").subcommands
    assert find_help_command("ban").usage == "{prefix}ban <jid|nick|*.domain.tld> [comment]"
    assert find_help_command("protections").subcommands == USAGE_DOCUMENTS["protection"].command.subcommands
    assert find_help_command("whitelist").name == "ignore"


def test_sectioned_admin_help_matches_legacy_text_and_custom_prefixes() -> None:
    """Keep the full admin help list stable while converting it to sections."""
    import json
    from pathlib import Path

    from banbot.commands.help_sections import ADMIN_HELP_SECTIONS

    expected = json.loads((Path(__file__).parent / "fixtures" / "admin_help_legacy.json").read_text("utf-8"))
    assert len(ADMIN_HELP_SECTIONS) == 11
    bot = HelpBot()
    for prefix, old in expected.items():
        bot.command_prefix = prefix
        assert bot._admin_help_text() == old
