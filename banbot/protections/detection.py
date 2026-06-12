"""Message detection helpers used by protections."""

from __future__ import annotations

import re
from urllib.parse import urlparse

MEDIA_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".svg",
    ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".mp3", ".ogg", ".opus", ".wav", ".flac",
)

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def message_looks_like_media(body: str) -> bool:
    """Best-effort detection for HTTP-upload/media-only spam messages."""
    text = str(body or "").strip()
    if not text:
        return False

    urls = URL_RE.findall(text)
    if not urls:
        return False

    # A first-media protection should be conservative: only trigger when the
    # message is basically media/URLs with little or no human text around it.
    without_urls = URL_RE.sub("", text).strip()
    if without_urls and len(without_urls) > 20:
        return False

    for url in urls:
        parsed = urlparse(url.rstrip(">),.;!?'\""))
        path = parsed.path.lower()
        if any(path.endswith(ext) for ext in MEDIA_EXTENSIONS):
            return True

    return False


def count_mentions(body: str, known_nicks: list[str]) -> int:
    """Count how many distinct known MUC nicks are mentioned in a message."""
    text = str(body or "")
    text_lower = text.lower()
    count = 0
    for nick in known_nicks:
        nick_text = str(nick or "").strip()
        if not nick_text:
            continue
        # Avoid counting tiny/common tokens too aggressively.
        if len(nick_text) < 3:
            pattern = rf"(?<!\w){re.escape(nick_text.lower())}(?!\w)"
        else:
            pattern = rf"(?<!\w){re.escape(nick_text.lower())}(?!\w)"
        if re.search(pattern, text_lower):
            count += 1
    return count


def body_contains_blocked_word(body: str, words: list[str]) -> str | None:
    """Return the first configured blocked word/phrase found in body."""
    text = str(body or "").lower()
    for word in words:
        candidate = str(word or "").strip().lower()
        if not candidate:
            continue
        if candidate in text:
            return candidate
    return None
