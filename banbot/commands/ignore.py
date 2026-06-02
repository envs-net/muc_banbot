"""Ignorelist/whitelist admin command dispatch."""

from ..locks import ban_state_lock


class CommandIgnoreMixin:
    async def _dispatch_ignore_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        actor_jid = self._actor_jid_from_room_nick(room, nick)
        async with ban_state_lock(self):
            await self.cmd_ignore(args, room, actor=actor_jid, command_name=cmd)
