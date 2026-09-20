"""Shared protocol primitives used across BanBot contract domains."""

from __future__ import annotations

from typing import Protocol


class ActorJidResolverHost(Protocol):
    """Canonical room/nick actor resolution shared by command mixins."""

    def _actor_jid_from_room_nick(self, room: str, nick: str) -> str: ...
