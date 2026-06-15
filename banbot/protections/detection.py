"""Message detection helpers used by protections."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

MEDIA_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".svg",
    ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".mp3", ".ogg", ".opus", ".wav", ".flac",
)

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[a-z0-9.-]+\.[a-z]{2,}\b", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-z0-9]+|<url>|<email>", re.IGNORECASE)



def normalize_spam_body(body: str) -> str:
    """Return a stable comparable form for repeated/similar spam checks."""
    text = str(body or "").lower().strip()
    if not text:
        return ""

    text = URL_RE.sub(" <url> ", text)
    text = EMAIL_RE.sub(" <email> ", text)
    text = re.sub(r"[^a-z0-9<>]+", " ", text)
    return " ".join(text.split())


def normalized_word_count(normalized: str) -> int:
    """Return a conservative token count for normalized spam text."""
    return len(TOKEN_RE.findall(str(normalized or "")))


def messages_are_similar(left: str, right: str, *, similarity_percent: int) -> bool:
    """Return True when two normalized messages are equal or very similar."""
    left_text = str(left or "")
    right_text = str(right or "")
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    threshold = max(1, min(100, int(similarity_percent))) / 100.0
    return SequenceMatcher(None, left_text, right_text).ratio() >= threshold

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


def _nick_match_variants(nick: str) -> set[str]:
    """Return conservative body-match variants for a MUC nick."""
    nick_text = str(nick or "").strip()
    if not nick_text:
        return set()

    variants = {nick_text.lower()}

    # Some clients display affiliation prefixes such as ~creme/@alice.  The
    # actual MUC nick in the occupant cache may or may not contain that prefix,
    # so allow both forms for mention detection.
    stripped = nick_text.lstrip("~&@%+").strip()
    if stripped:
        variants.add(stripped.lower())

    # Common textual mention form.
    for variant in list(variants):
        variants.add(f"@{variant}")

    return {variant for variant in variants if variant}


def count_mentions(body: str, known_nicks: list[str]) -> int:
    """Count how many distinct known MUC nicks are mentioned in a message."""
    text_lower = str(body or "").lower()
    count = 0
    seen: set[str] = set()

    for nick in known_nicks:
        nick_text = str(nick or "").strip()
        if not nick_text:
            continue
        nick_key = nick_text.lower()
        if nick_key in seen:
            continue

        for variant in _nick_match_variants(nick_text):
            pattern = rf"(?<!\w){re.escape(variant)}(?!\w)"
            if re.search(pattern, text_lower):
                count += 1
                seen.add(nick_key)
                break

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
