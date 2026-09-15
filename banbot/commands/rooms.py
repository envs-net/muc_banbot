"""Admin room-management command dispatch."""

from typing import TYPE_CHECKING

from ..locks import ban_state_lock

if TYPE_CHECKING:
    from ..contracts import CommandRoomsMixinHost

    class _CommandRoomsMixinContract(CommandRoomsMixinHost):
        pass
else:
    class _CommandRoomsMixinContract:
        pass


class CommandRoomsMixin(_CommandRoomsMixinContract):
    async def _dispatch_room_command(self, room: str, nick: str, args: list[str], cmd: str) -> None:
        if len(args) < 1:
            await self.bot_send_message(
                mto=room,
                mbody=self._room_usage_text(),
                mtype="groupchat",
            )
            return

        async with ban_state_lock(self):
            await self.cmd_room(args, room)
