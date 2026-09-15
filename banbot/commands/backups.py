"""Backup and restore command dispatch."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..contracts import CommandBackupMixinHost

    class _CommandBackupMixinContract(CommandBackupMixinHost):
        pass
else:
    class _CommandBackupMixinContract:
        pass


class CommandBackupMixin(_CommandBackupMixinContract):
    async def _dispatch_backup_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        actor_jid = self._actor_jid_from_room_nick(room, nick)
        await self.cmd_backup(args, room, actor=actor_jid)

    async def _dispatch_restore_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        actor_jid = self._actor_jid_from_room_nick(room, nick)
        await self.cmd_restore(args, room, actor=actor_jid)
