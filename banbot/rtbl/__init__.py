"""RTBL package."""

from typing import TYPE_CHECKING

from .utils import RTBL_PUBLISH_SANITY_CHECK_REASON

# Exported lazily via __getattr__ to keep banbot.rtbl.utils importable even
# when optional runtime dependencies for the full RTBL mixin are unavailable.
if TYPE_CHECKING:
    from .mixin import RtblMixin as _RtblMixin

RtblMixin: type["_RtblMixin"]


def __getattr__(name: str):
    if name == "RtblMixin":
        from .mixin import RtblMixin

        return RtblMixin

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "RTBL_PUBLISH_SANITY_CHECK_REASON",
    "RtblMixin",
]
