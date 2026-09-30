"""XMPP groupchat command entry point."""

from typing import TYPE_CHECKING

from envs_xmpp_core.xmpp.messaging import message_context_from_stanza

from envs_xmpp_core.commands import parse_prefixed_command

from .context import bot_nick

if TYPE_CHECKING:
    from ..contracts import CommandEntryPointMixinHost

    class _CommandEntryPointMixinContract(CommandEntryPointMixinHost):
        pass
else:
    class _CommandEntryPointMixinContract:
        pass


class CommandEntryPointMixin(_CommandEntryPointMixinContract):
    def _actor_jid_from_room_nick(self, room: str, nick: str) -> str:
        """Resolve a room occupant nick to the best actor JID for logs/audit."""
        jid = self.occupants.get(room, {}).get(nick, {}).get("jid")
        return str(jid) if jid else nick

    def user_cmds_allowed(self, room: str) -> bool:
        """Check if user commands are allowed in protected rooms."""
        return room in self.protected_rooms and self.allow_user_cmds

    async def on_message(self, msg) -> None:
        """
        Handles incoming messages in MUCs.
        - Ignores own messages
        - Parses commands
        - Delegates to user/admin handlers
        """
        nick = str(msg["mucnick"] or "").strip()
        if not nick:
            return
        if nick.casefold() == bot_nick().casefold():
            return  # Ignore own messages

        encrypted = False
        if hasattr(self, "_decrypt_incoming_omemo_message"):
            msg, encrypted = await self._decrypt_incoming_omemo_message(msg)
            if msg is None:
                return

        context = message_context_from_stanza(msg, encrypted=encrypted)
        room = context.room or ""
        if not room:
            return
        body = context.body.strip()

        if hasattr(self, "_redaction_index_message"):
            await self._redaction_index_message(msg)

        if not body:
            return

        if hasattr(self, "protections_on_message"):
            handled_by_protection = await self.protections_on_message(msg, room, nick, body)
            if handled_by_protection:
                return

        parsed = parse_prefixed_command(body, self.command_prefix)
        if parsed is None:
            return
        cmd, args = parsed

        token = self._set_reply_encryption_context(context.encrypted)
        try:
            handled = await self._handle_user_command(msg, room, nick, cmd, args)
            if handled:
                return

            handled = await self._handle_admin_command(msg, room, nick, cmd, args)
            if handled:
                return

            await self._handle_unknown_command(msg, room, cmd)
        finally:
            self._reset_reply_encryption_context(token)
