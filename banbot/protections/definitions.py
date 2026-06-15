"""Protection definitions and default configuration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

PROTECTION_ORDER = (
    "FloodSpamProtection",
    "FirstMessageMediaProtection",
    "MentionLimitProtection",
    "WordListNewJoinerProtection",
    "JoinWaveShortCircuitProtection",
    "TrustedReporters",
    "PolicyChangeNotification",
)

PROTECTION_DEFAULTS: dict[str, dict[str, Any]] = {
    "FloodSpamProtection": {
        "enabled": False,
        "window_seconds": 60,
        "max_messages": 10,
        "action": "ban",
        "tempban_seconds": 86400,
        "reason": "spam/flood detected",
        "redact": True,
    },
    "FirstMessageMediaProtection": {
        "enabled": False,
        "join_grace_seconds": 600,
        "action": "tempban",
        "tempban_seconds": 86400,
        "reason": "first message was media spam",
        "redact": True,
    },
    "MentionLimitProtection": {
        "enabled": False,
        "max_mentions": 5,
        "action": "tempban",
        "tempban_seconds": 3600,
        "reason": "too many mentions",
        "redact": True,
    },
    "WordListNewJoinerProtection": {
        "enabled": False,
        "join_grace_seconds": 900,
        "words": [],
        "action": "tempban",
        "tempban_seconds": 86400,
        "reason": "blocked word from new joiner",
        "redact": True,
    },
    "JoinWaveShortCircuitProtection": {
        "enabled": False,
        "window_seconds": 60,
        "max_joins": 8,
        "action": "lockdown",
        "cooldown_seconds": 60,
        "startup_grace_seconds": 30,
        "rejoin_grace_seconds": 300,
        "ignore_member_affiliations": True,
        "lockdown_seconds": 900,
        "members_only": True,
        "moderated": True,
        "reason": "join wave detected",
        "notify_only": False,
    },
    "TrustedReporters": {
        "enabled": False,
        "reporters": [],
        "threshold": 2,
        "window_seconds": 900,
        "action": "tempban",
        "tempban_seconds": 86400,
        "reason": "trusted reporter threshold reached",
        "redact": True,
    },
    "PolicyChangeNotification": {
        "enabled": True,
        "notify_bans": True,
        "notify_unbans": True,
        "notify_config": True,
    },
}


PROTECTION_DISPLAY_ALIASES = {
    "FloodSpamProtection": "flood",
    "FirstMessageMediaProtection": "media",
    "MentionLimitProtection": "mentions",
    "WordListNewJoinerProtection": "wordlist",
    "JoinWaveShortCircuitProtection": "joinwave",
    "TrustedReporters": "reporters",
    "PolicyChangeNotification": "policy",
}

PROTECTION_ALIASES = {
    "flood": "FloodSpamProtection",
    "spam": "FloodSpamProtection",
    "floodspam": "FloodSpamProtection",
    "floodspamprotection": "FloodSpamProtection",
    "media": "FirstMessageMediaProtection",
    "firstmedia": "FirstMessageMediaProtection",
    "firstmessagemedia": "FirstMessageMediaProtection",
    "firstmessageismedia": "FirstMessageMediaProtection",
    "firstmessageimage": "FirstMessageMediaProtection",
    "firstmessageisimage": "FirstMessageMediaProtection",
    "firstmessageisimageprotection": "FirstMessageMediaProtection",
    "firstmessagemediaprotection": "FirstMessageMediaProtection",
    "mention": "MentionLimitProtection",
    "mentions": "MentionLimitProtection",
    "mentionlimit": "MentionLimitProtection",
    "mentionlimitprotection": "MentionLimitProtection",
    "wordlist": "WordListNewJoinerProtection",
    "words": "WordListNewJoinerProtection",
    "newjoinerwords": "WordListNewJoinerProtection",
    "wordlistnewjoiner": "WordListNewJoinerProtection",
    "wordlistnewjoinerprotection": "WordListNewJoinerProtection",
    "joinwave": "JoinWaveShortCircuitProtection",
    "joinwaveshortcircuit": "JoinWaveShortCircuitProtection",
    "joinwaveshortcircuitprotection": "JoinWaveShortCircuitProtection",
    "trustedreporters": "TrustedReporters",
    "reports": "TrustedReporters",
    "reporters": "TrustedReporters",
    "policy": "PolicyChangeNotification",
    "policychange": "PolicyChangeNotification",
    "policychangenotification": "PolicyChangeNotification",
    "notifications": "PolicyChangeNotification",
}

PROTECTION_ALLOWED_ACTIONS = {"notify", "warn", "kick", "tempban", "ban"}


def default_protection_config() -> dict[str, dict[str, Any]]:
    """Return a deep copy of the default protection configuration."""
    return deepcopy(PROTECTION_DEFAULTS)


def canonical_protection_name(name: str) -> str | None:
    """Return the canonical protection name for a user-provided name/alias."""
    normalized = "".join(ch for ch in str(name or "").lower() if ch.isalnum())
    return PROTECTION_ALIASES.get(normalized)
