from __future__ import annotations

import pytest

from banbot.ban_target import BanTarget


def test_ban_target_canonicalizes_jid_domain_and_nick():
    jid = BanTarget.from_parts("User@Example.Org/Phone", " MixedNick ")
    assert (jid.kind, jid.value, jid.identifier, jid.jid, jid.nick) == (
        "jid", "user@example.org", "user@example.org", "user@example.org", "mixednick"
    )

    domain = BanTarget.from_parts("*.Sub.Example.Org..")
    assert (domain.kind, domain.value, domain.identifier, domain.outcast_jid) == (
        "domain", "sub.example.org", "*.sub.example.org", "sub.example.org"
    )

    nick = BanTarget.from_parts(None, " Nick ")
    assert (nick.kind, nick.value, nick.identifier) == ("nick", "nick", "nick")


def test_ban_target_identifier_keeps_bare_domains_ambiguous_unless_requested():
    assert BanTarget.from_identifier("example.org").kind == "nick"
    target = BanTarget.from_identifier("example.org", plain_domain=True)
    assert (target.kind, target.identifier) == ("domain", "*.example.org")


def test_ban_target_rejects_empty_values():
    with pytest.raises(ValueError):
        BanTarget.from_parts(None, None)
    with pytest.raises(ValueError):
        BanTarget.from_parts("*...")


def test_ban_target_from_storage_preserves_domain_metadata():
    target = BanTarget.from_storage("domain", "Example.Org.", nick=" Reporter ")
    assert (target.kind, target.value, target.identifier, target.nick) == (
        "domain", "example.org", "*.example.org", "reporter"
    )


def test_ban_target_strips_accidental_zero_width_jid_characters():
    observed = BanTarget.from_parts("bulk_be49a07335@\u200bjabber.vg")
    canonical = BanTarget.from_parts("bulk_be49a07335@jabber.vg")
    assert observed == canonical
    assert observed.value == "bulk_be49a07335@jabber.vg"

    bom = BanTarget.from_identifier("User@\ufeffExample.Org/Phone")
    assert bom.value == "user@example.org"


def test_ban_target_strips_zero_width_characters_from_domain_bans_only():
    domain = BanTarget.from_identifier("*.\u200bExample.Org")
    assert (domain.kind, domain.value, domain.identifier) == (
        "domain",
        "example.org",
        "*.example.org",
    )

    # Nick identities remain byte-for-byte meaningful apart from their
    # historical whitespace/case normalization.
    nick = BanTarget.from_identifier("Nick\u200bName")
    assert (nick.kind, nick.value) == ("nick", "nick\u200bname")
