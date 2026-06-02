"""OMEMO command dispatch."""


class CommandOmemoMixin:
    async def _dispatch_omemo_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        actor_jid = self._actor_jid_from_room_nick(room, nick)
        await self.cmd_omemo(args, room, actor=actor_jid)
