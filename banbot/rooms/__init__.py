"""Protected room package."""

from config import ADMIN_ROOM, NICK

from .invites import RoomInviteMixin
from .protected import ProtectedRoomMixin


class RoomMixin(ProtectedRoomMixin):
    """Protected room command and persistence mixin."""


__all__ = ["ADMIN_ROOM", "NICK", "RoomMixin", "RoomInviteMixin", "ProtectedRoomMixin"]
