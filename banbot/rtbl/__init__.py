"""RTBL package."""

from typing import TYPE_CHECKING

from .utils import (
    RTBL_PUBLISH_SANITY_CHECK_REASON,
    _is_domain,
    _is_sha256,
    _looks_like_pubsub_node,
    _looks_like_pubsub_service_jid,
    _rtbl_build_payload,
    _rtbl_extract_reason,
    _rtbl_hash_jid,
)

if TYPE_CHECKING:
    from .mixin import RtblMixin as _RtblMixin

# Exported lazily via __getattr__ to keep banbot.rtbl.utils importable without
# importing the full RTBL mixin stack and its optional runtime dependencies.
RtblMixin: type["_RtblMixin"]


def __getattr__(name: str):
    if name == "RtblMixin":
        from .mixin import RtblMixin

        return RtblMixin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "RTBL_PUBLISH_SANITY_CHECK_REASON",
    "_is_domain",
    "_is_sha256",
    "_looks_like_pubsub_node",
    "_looks_like_pubsub_service_jid",
    "_rtbl_build_payload",
    "_rtbl_extract_reason",
    "_rtbl_hash_jid",
    "RtblMixin",
]
