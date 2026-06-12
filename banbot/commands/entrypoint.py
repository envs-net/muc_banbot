"""XMPP groupchat command entry point."""

from .context import bot_nick


class CommandEntryPointMixin:
    def _actor_jid_from_room_nick(self, room: str, nick: str) -> str:
        """Resolve a room occupant nick to the best actor JID for logs/audit."""
        jid = self.occupants.get(room, {}).get(nick, {}).get("jid")
        return jid or nick

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
        if msg["mucnick"].lower() == bot_nick().lower():
            return  # Ignore own messages

        encrypted = False
        if hasattr(self, "_decrypt_incoming_omemo_message"):
            msg, encrypted = await self._decrypt_incoming_omemo_message(msg)
            if msg is None:
                return

        room = msg["from"].bare
        nick = msg["mucnick"]
        body = msg["body"].strip()

        if hasattr(self, "_redaction_index_message"):
            await self._redaction_index_message(msg)

        if not body:
            return

        if hasattr(self, "protections_on_message"):
            handled_by_protection = await self.protections_on_message(msg, room, nick, body)
            if handled_by_protection:
                return

        if not body.startswith(self.command_prefix):
            return

        parts = body.split()
        raw_cmd = parts[0]

        cmd = raw_cmd[len(self.command_prefix):].lower()
        args = parts[1:]

        token = self._set_reply_encryption_context(encrypted)
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
