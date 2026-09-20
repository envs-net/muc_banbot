"""Neutral protection matches and deterministic action arbitration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .definitions import PROTECTION_PUNITIVE_ACTIONS, ProtectionActionOutcome

_ACTION_STRENGTH = {"kick": 1, "tempban": 2, "ban": 3}


@dataclass(frozen=True, slots=True)
class ProtectionMatch:
    """A protection match with all policy inputs snapshotted before execution."""

    protection: str
    room: str
    nick: str
    target: str
    action: str
    reason: str
    tempban_seconds: int
    redact: bool
    observe: bool
    msg: Any = field(default=None, compare=False, repr=False)
    details: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def punitive(self) -> bool:
        """Return whether the configured action is punitive when enforcing."""
        return self.action in PROTECTION_PUNITIVE_ACTIONS

    @property
    def strength(self) -> int:
        """Return the ordering used to compare punitive actions."""
        return protection_action_strength(self.action)


@dataclass(frozen=True, slots=True)
class ProtectionDecision:
    """Arbitration result for one protection match."""

    match: ProtectionMatch
    outcome: ProtectionActionOutcome
    suppression_reason: str | None = None

    @property
    def should_execute(self) -> bool:
        """Return whether the match should produce its configured side effects."""
        return self.outcome is not ProtectionActionOutcome.PUNITIVE_SUPPRESSED

    @property
    def stops_pipeline(self) -> bool:
        """Return whether this decision stops later message protections."""
        return self.outcome is ProtectionActionOutcome.PUNITIVE_EXECUTED


def protection_action_strength(action: str) -> int:
    """Return an ordering where stronger punitive actions have larger values."""
    return _ACTION_STRENGTH.get(str(action or "").lower(), 0)


def arbitrate_protection_match(
    match: ProtectionMatch,
    *,
    cooldown_entry: tuple[float, int] | None,
    now: float,
) -> ProtectionDecision:
    """Decide one match without performing any protection side effects.

    The ordering intentionally preserves the existing BanBot policy: observe and
    non-punitive matches execute and fall through; an enforcing punitive match
    stops the pipeline unless an active equal/stronger cooldown suppresses it.
    A stronger punitive match may bypass that cooldown.
    """
    if match.observe or not match.punitive:
        return ProtectionDecision(
            match=match,
            outcome=ProtectionActionOutcome.NON_PUNITIVE,
        )

    if cooldown_entry is not None:
        until, previous_strength = cooldown_entry
        if until > now and match.strength <= previous_strength:
            return ProtectionDecision(
                match=match,
                outcome=ProtectionActionOutcome.PUNITIVE_SUPPRESSED,
                suppression_reason="active equal-or-stronger action cooldown",
            )

    return ProtectionDecision(
        match=match,
        outcome=ProtectionActionOutcome.PUNITIVE_EXECUTED,
    )
