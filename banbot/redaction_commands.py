"""Redaction commands and automatic redaction entry points."""

from __future__ import annotations

from .redaction_common import _RedactionMixinContract
from .utils import bare_jid, validate_jid_format


class RedactionCommandMixin(_RedactionMixinContract):
    async def cmd_redact(self, args: list[str], room: str, actor: str | None = None) -> None:
        """Handle !redact admin command."""
        if not getattr(self, "redaction_enabled", False):
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "❌ Redaction is disabled.\n"
                    f"Set REDACTION_ENABLED=True and run {getattr(self, 'command_prefix', '!')}reloadconfig to use it."
                ),
                mtype="groupchat",
            )
            return

        if not args:
            await self.bot_send_message(
                mto=room,
                mbody=(
                    "Usage:\n"
                    f"  {self.command_prefix}redact <jid> [reason]\n"
                    f"  {self.command_prefix}redact id <room_jid> <stanza_id> [reason]\n"
                    f"  {self.command_prefix}redact cleanup"
                ),
                mtype="groupchat",
            )
            return

        subcmd = args[0].lower()
        if subcmd == "cleanup":
            await self.redact_cleanup(room, actor=actor)
            return

        if subcmd == "id":
            if len(args) < 3:
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Usage: {self.command_prefix}redact id <room_jid> <stanza_id> [reason]",
                    mtype="groupchat",
                )
                return
            room_jid = args[1].strip().lower()
            stanza_id = args[2].strip()
            reason = " ".join(args[3:]).strip() or None
            if room_jid not in self._redaction_protected_rooms():
                await self.bot_send_message(
                    mto=room,
                    mbody=f"❌ Refusing redaction: {room_jid} is not a protected room.",
                    mtype="groupchat",
                )
                return
            await self.redact_single_stanza(room_jid, stanza_id, reason, actor)
            return

        target = bare_jid(args[0])
        if not target or not validate_jid_format(target):
            await self.bot_send_message(
                mto=room,
                mbody=f"❌ Usage: {self.command_prefix}redact <jid> [reason]",
                mtype="groupchat",
            )
            return

        reason = " ".join(args[1:]).strip() or None
        await self.redact_jid_messages(target, reason=reason, actor=actor, announce=True)


    async def _maybe_auto_redact_after_reasoned_jid_ban(
        self,
        jid: str | None,
        comment: str | None,
        *,
        actor: str | None = None,
        title: str = "Auto-redaction completed after ban",
    ) -> None:
        """Run auto-redaction for a JID ban when the reason matches config."""
        if not jid or jid.startswith("*."):
            return
        match = self._redaction_auto_reason_matches(comment)
        if not match:
            return

        await self.redact_jid_messages(
            jid,
            reason=comment or match,
            actor=actor,
            announce=True,
            title=title,
        )


    async def maybe_auto_redact_after_ban(
        self,
        jid: str | None,
        comment: str | None,
        actor: str | None = None,
    ) -> None:
        """Run auto-redaction after a bot-command ban if the reason matches."""
        await self._maybe_auto_redact_after_reasoned_jid_ban(
            jid,
            comment,
            actor=actor,
            title="Auto-redaction completed after ban",
        )


    async def maybe_auto_redact_after_imported_ban(
        self,
        jid: str | None,
        comment: str | None,
        actor: str | None = None,
    ) -> None:
        """Run configured auto-redaction for imported JID bans."""
        if not getattr(self, "auto_redact_on_imported_ban_reason", False):
            return
        await self._maybe_auto_redact_after_reasoned_jid_ban(
            jid,
            comment,
            actor=actor or "import",
            title="Auto-redaction completed after imported ban",
        )


    async def maybe_auto_redact_after_manual_muc_ban(
        self,
        jid: str | None,
        comment: str | None,
        actor: str | None = None,
    ) -> None:
        """Run configured auto-redaction for manually discovered MUC bans."""
        if not getattr(self, "auto_redact_on_manual_muc_ban", False):
            return
        await self._maybe_auto_redact_after_reasoned_jid_ban(
            jid,
            comment,
            actor=actor or "sync",
            title="Auto-redaction completed after manual MUC ban",
        )
