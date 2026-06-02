"""Backup, restore, and OMEMO command dispatch."""


class CommandBackupMixin:
    async def _dispatch_backup_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        actor_jid = self._actor_jid_from_room_nick(room, nick)
        await self.cmd_backup(args, room, actor=actor_jid)

    async def _dispatch_restore_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        actor_jid = self._actor_jid_from_room_nick(room, nick)
        await self.cmd_restore(args, room, actor=actor_jid)

    async def _dispatch_omemo_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        actor_jid = self._actor_jid_from_room_nick(room, nick)
        await self.cmd_omemo(args, room, actor=actor_jid)
