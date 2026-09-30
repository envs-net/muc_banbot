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
