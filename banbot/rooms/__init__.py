from config import ADMIN_ROOM, NICK

"""Protected room package."""

from .invites import RoomInviteMixin
from .protected import ProtectedRoomMixin


class RoomMixin(ProtectedRoomMixin):
    """Protected room command and persistence mixin."""


__all__ = ["RoomMixin", "RoomInviteMixin", "ProtectedRoomMixin"]
