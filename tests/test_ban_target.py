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
