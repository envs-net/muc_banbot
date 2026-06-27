"""Protection definitions and default configuration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

PROTECTION_ORDER = (
    "FloodSpamProtection",
    "SimilarMessageProtection",
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
        "action_cooldown_seconds": 5,
    },
    "SimilarMessageProtection": {
        "enabled": False,
        "window_seconds": 120,
        "max_similar": 3,
        "similarity_percent": 90,
        "min_length": 20,
        "min_words": 3,
        "action": "tempban",
        "tempban_seconds": 86400,
        "reason": "repeated/similar spam detected",
        "redact": True,
        "action_cooldown_seconds": 5,
    },
    "FirstMessageMediaProtection": {
        "enabled": False,
        "join_grace_seconds": 600,
        "action": "tempban",
        "tempban_seconds": 86400,
        "reason": "first message was media spam",
        "redact": True,
        "action_cooldown_seconds": 5,
    },
    "MentionLimitProtection": {
        "enabled": False,
        "max_mentions": 5,
        "action": "tempban",
        "tempban_seconds": 3600,
        "reason": "too many mentions",
        "redact": True,
        "action_cooldown_seconds": 5,
    },
    "WordListNewJoinerProtection": {
        "enabled": False,
        "join_grace_seconds": 900,
        "words": [],
        "action": "tempban",
        "tempban_seconds": 86400,
        "reason": "blocked word from new joiner",
        "redact": True,
        "action_cooldown_seconds": 5,
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
        "action_cooldown_seconds": 5,
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
    "SimilarMessageProtection": "similar",
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
    "similar": "SimilarMessageProtection",
    "similarspam": "SimilarMessageProtection",
    "repeated": "SimilarMessageProtection",
    "repeat": "SimilarMessageProtection",
    "repeatedmessages": "SimilarMessageProtection",
    "similarmessage": "SimilarMessageProtection",
    "similarmessages": "SimilarMessageProtection",
    "similarmessageprotection": "SimilarMessageProtection",
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

# Protection-specific action restrictions used by command validation.
# Protections not listed here accept PROTECTION_ALLOWED_ACTIONS.
PROTECTION_ACTIONS_BY_PROTECTION = {
    "JoinWaveShortCircuitProtection": {"lockdown", "notify"},
}


def default_protection_config() -> dict[str, dict[str, Any]]:
    """Return a deep copy of the default protection configuration."""
    return deepcopy(PROTECTION_DEFAULTS)


def canonical_protection_name(name: str) -> str | None:
    """Return the canonical protection name for a user-provided name/alias.

    The input is normalized to lowercase alphanumeric characters before lookup.
    Returns the canonical protection class name when a matching alias exists,
    otherwise returns ``None``. Callers should explicitly handle the ``None``
    case for unknown/unsupported names.
    """
    normalized = "".join(ch for ch in str(name or "").lower() if ch.isalnum())
    return PROTECTION_ALIASES.get(normalized)
