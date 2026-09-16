"""Ban metadata helpers shared by moderation, sync, and DB normalization."""

from __future__ import annotations

RECOVERED_BAN_REASON = "Recovered from room"
_REASON_SEPARATOR = " | "


def is_placeholder_ban_reason(reason: str | None) -> bool:
    """Return True for missing/synthetic reasons that may be safely enriched."""
    return not reason or reason.strip() == RECOVERED_BAN_REASON


def merge_ban_reasons(existing: str | None, incoming: str | None) -> str | None:
    """Merge distinct meaningful reasons without retaining recovery placeholders."""
    old = (existing or "").strip()
    new = (incoming or "").strip()

    if is_placeholder_ban_reason(old):
        return new or (old or None)
    if is_placeholder_ban_reason(new):
        return old or None

    parts = [part.strip() for part in old.split(_REASON_SEPARATOR) if part.strip()]
    if new not in parts:
        parts.append(new)
    return _REASON_SEPARATOR.join(parts)
