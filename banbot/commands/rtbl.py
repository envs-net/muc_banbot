"""RTBL admin command dispatch."""

from ..locks import ban_state_lock


class CommandRtblMixin:
    async def _dispatch_rtbl_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        if not getattr(self, "rtbl_enabled", False):
            await self.bot_send_message(
                mto=room,
                mbody="❌ RTBL is disabled. Set RTBL_ENABLED = True in config.py and restart.",
                mtype="groupchat",
            )
            return
        actor_jid = self._actor_jid_from_room_nick(room, nick)
        async with ban_state_lock(self):
            await self.cmd_rtbl(args, room, actor=actor_jid)
