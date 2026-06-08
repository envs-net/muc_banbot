"""RTBL package."""

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


def __getattr__(name: str):
    if name == "RtblMixin":
        from .mixin import RtblMixin

        return RtblMixin
    raise AttributeError(name)


__all__ = [
    "RtblMixin",
    "RTBL_PUBLISH_SANITY_CHECK_REASON",
    "_is_domain",
    "_is_sha256",
    "_looks_like_pubsub_node",
    "_looks_like_pubsub_service_jid",
    "_rtbl_build_payload",
    "_rtbl_extract_reason",
    "_rtbl_hash_jid",
]
