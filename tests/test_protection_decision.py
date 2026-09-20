from __future__ import annotations

import pytest

from banbot.protections.decision import (
    ProtectionMatch,
    arbitrate_protection_match,
    protection_action_strength,
)
from banbot.protections.definitions import ProtectionActionOutcome


def make_match(*, action: str, observe: bool = False) -> ProtectionMatch:
    return ProtectionMatch(
        protection="TestProtection",
        room="room@conference.example.org",
        nick="Spammer",
        target="spam@example.org",
        action=action,
        reason="test reason",
        tempban_seconds=3600,
        redact=False,
        observe=observe,
    )


@pytest.mark.parametrize(
    ("action", "observe", "cooldown", "expected", "suppressed"),
    [
        ("notify", False, None, ProtectionActionOutcome.NON_PUNITIVE, False),
        ("warn", False, (200.0, 3), ProtectionActionOutcome.NON_PUNITIVE, False),
        ("ban", True, (200.0, 3), ProtectionActionOutcome.NON_PUNITIVE, False),
        ("kick", False, None, ProtectionActionOutcome.PUNITIVE_EXECUTED, False),
        ("kick", False, (200.0, 1), ProtectionActionOutcome.PUNITIVE_SUPPRESSED, True),
        ("tempban", False, (200.0, 3), ProtectionActionOutcome.PUNITIVE_SUPPRESSED, True),
        ("ban", False, (200.0, 2), ProtectionActionOutcome.PUNITIVE_EXECUTED, False),
        ("ban", False, (99.0, 3), ProtectionActionOutcome.PUNITIVE_EXECUTED, False),
    ],
)
def test_protection_decision_matrix(
    action: str,
    observe: bool,
    cooldown: tuple[float, int] | None,
    expected: ProtectionActionOutcome,
    suppressed: bool,
) -> None:
    decision = arbitrate_protection_match(
        make_match(action=action, observe=observe),
        cooldown_entry=cooldown,
        now=100.0,
    )

    assert decision.outcome is expected
    assert bool(decision.suppression_reason) is suppressed
    assert decision.should_execute is not suppressed
    assert decision.stops_pipeline is (expected is ProtectionActionOutcome.PUNITIVE_EXECUTED)


def test_match_exposes_stable_action_strength() -> None:
    assert make_match(action="kick").strength == 1
    assert make_match(action="tempban").strength == 2
    assert make_match(action="ban").strength == 3
    assert make_match(action="notify").strength == 0


def test_match_copies_details_at_construction_boundary() -> None:
    details = {"messages": 3}
    match = ProtectionMatch(
        protection="FloodSpamProtection",
        room="room@conference.example.org",
        nick="Spammer",
        target="spam@example.org",
        action="ban",
        reason="spam/flood detected",
        tempban_seconds=3600,
        redact=True,
        observe=False,
        details=dict(details),
    )
    details["messages"] = 99

    assert match.details == {"messages": 3}


def test_active_punitive_cooldown_property() -> None:
    hypothesis = pytest.importorskip("hypothesis")
    strategies = pytest.importorskip("hypothesis.strategies")

    @hypothesis.given(
        action=strategies.sampled_from(["kick", "tempban", "ban"]),
        previous_strength=strategies.integers(min_value=1, max_value=3),
    )
    @hypothesis.settings(max_examples=30)
    def check(action: str, previous_strength: int) -> None:
        decision = arbitrate_protection_match(
            make_match(action=action),
            cooldown_entry=(200.0, previous_strength),
            now=100.0,
        )
        expected_suppressed = protection_action_strength(action) <= previous_strength
        assert (
            decision.outcome is ProtectionActionOutcome.PUNITIVE_SUPPRESSED
        ) is expected_suppressed

    check()
