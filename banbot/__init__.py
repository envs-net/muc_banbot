"""BanBot package for XMPP multi-room ban management.

The heavy XMPP client class is imported lazily so pure helper modules can be
imported in tests and tooling without requiring runtime-only dependencies to be
installed first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bot import BanBot

__all__ = ["BanBot"]


def __getattr__(name: str) -> Any:
    if name == "BanBot":
        from .bot import BanBot as _BanBot

        return _BanBot
    raise AttributeError(name)
