"""Help text generation for BanBot commands."""

from typing import TYPE_CHECKING

from envs_xmpp_core.commands import render_command_help_sections

from ..utils import get_list_page_size, paginate_lines, resolve_page, wants_all_pages, without_all_pages_arg
from .help_sections import ADMIN_HELP_SECTIONS
from .help_specs import HELP_TOPIC_METHODS, find_help_command

if TYPE_CHECKING:
    from ..contracts import CommandHelpMixinHost

    class _CommandHelpMixinContract(CommandHelpMixinHost):
        pass
else:
    class _CommandHelpMixinContract:
        pass


class CommandHelpMixin(_CommandHelpMixinContract):
    def _admin_topic_help_text(self, topic: str | list[str]) -> str:
        """Return focused help for one admin command topic."""
        raw_topic = " ".join(topic.split()) if isinstance(topic, str) else " ".join(map(str, topic))
        command = find_help_command(topic)
        if command is not None:
            return getattr(self, HELP_TOPIC_METHODS[command.name])()

        return (
            f"❌ Unknown help topic: {raw_topic or topic}\n"
            f"Use {self.command_prefix}help to see available admin commands."
        )

    @staticmethod
    def _is_help_page_arg(arg: str) -> bool:
        value = str(arg).lower().strip()
        return value in {"all", "last"} or value.isdigit()

    def _admin_help_should_paginate(self, args: list[str]) -> bool:
        if wants_all_pages(args):
            return False
        return getattr(self, "help_output_mode", "all") == "paginate"

    def _admin_help_page_from_args(self, args: list[str], total_items: int, per_page: int) -> int:
        args = without_all_pages_arg(args)
        page = 1
        for arg in args:
            value = str(arg).lower().strip()
            if value == "last":
                page = -1
            elif value.isdigit():
                page = int(value)
        return resolve_page(page, total_items, per_page)

    def _admin_help_response(self, args: list[str] | None = None) -> str:
        args = args or []
        if args and not all(self._is_help_page_arg(arg) for arg in args):
            return self._admin_topic_help_text(args)

        help_lines = self._admin_help_text().splitlines()
        if not self._admin_help_should_paginate(args):
            return "\n".join(help_lines)

        per_page = get_list_page_size(self)
        page = self._admin_help_page_from_args(args, len(help_lines), per_page)
        page_lines, current_page, total_pages, _total_items = paginate_lines(help_lines, page, per_page)
        return "\n".join([
            f"🛠️ Admin Help (page {current_page}/{total_pages})",
            *page_lines,
            "",
            f"Use {self.command_prefix}help all for the full output.",
        ])

    async def _user_help_text(self) -> str:
        p = self.command_prefix
        lines = [
            f"{p}help - show this help",
            f"{p}whoami - show your affiliation/role and permissions",
            f"{p}banlist / {p}blacklist [all|page|last] - show temporary bans",
            f"{p}why <jid|nick|domain> - show ban reason",
        ]

        policy_enabled, policy_text = await self.get_public_policy()
        if policy_enabled and policy_text.strip():
            lines.append(f"{p}rules / {p}policy - show room moderation policy")

        if getattr(self, "protection_enabled", lambda _name: False)("TrustedReporters"):
            lines.append(f"{p}report <nick|jid> [reason] - report abuse as trusted reporter")

        return "\n".join(lines)

    def _admin_help_text(self) -> str:
        """Render shared structured sections for BanBot's admin command overview."""
        return render_command_help_sections(ADMIN_HELP_SECTIONS, self.command_prefix, trailing_newline=True)
