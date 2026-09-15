"""Canonical BanBot ban-target identities.

The database, caches, commands and MUC synchronization all deal with the same
three target kinds.  Keeping their normalization in one immutable value object
prevents subtle differences such as ``*.Example.Org.`` vs ``*.example.org`` or
resource-bearing JIDs leaking into one subsystem while another uses bare JIDs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from envs_xmpp_core.xmpp.jid import bare_jid

BanTargetKind = Literal["jid", "nick", "domain"]

# U+200B ZERO WIDTH SPACE and U+FEFF ZERO WIDTH NO-BREAK SPACE/BOM
# can be introduced by copied/pasted presentation text. They are not part of
# the logical BanBot JID/domain identity, so remove them before canonicalizing
# those target kinds. Keep this deliberately narrow instead of stripping every
# Unicode format character, because some format-code points have contextual
# meaning in internationalized identifiers.
_INVISIBLE_IDENTITY_TRANSLATION = str.maketrans({"\u200b": None, "\ufeff": None})


def _canonical_identity_text(value: object | None) -> str:
    """Return normalized text for a JID/domain identity input."""
    return str(value or "").translate(_INVISIBLE_IDENTITY_TRANSLATION).strip().lower()


@dataclass(frozen=True, slots=True)
class BanTarget:
    """One canonical JID, nick, or wildcard-domain ban identity."""

    kind: BanTargetKind
    value: str
    jid: str | None = None
    nick: str | None = None

    @property
    def identifier(self) -> str:
        """Return the canonical command/display identifier for this target."""
        if self.kind == "domain":
            return f"*.{self.value}"
        return self.value

    @property
    def outcast_jid(self) -> str | None:
        """Return the JID value used for a MUC outcast affiliation."""
        if self.kind == "domain":
            return self.value
        if self.kind == "jid":
            return self.value
        return None

    def as_legacy_tuple(self) -> tuple[BanTargetKind, str, str | None, str | None]:
        """Return the historical normalize_ban_target() tuple shape."""
        return self.kind, self.value, self.jid, self.nick

    @classmethod
    def from_parts(
        cls,
        jid: str | None = None,
        nick: str | None = None,
    ) -> BanTarget:
        """Build a canonical target from the database/API JID+nick shape."""
        raw_jid = _canonical_identity_text(jid)
        normalized_nick = str(nick).strip().lower() if nick else None

        if raw_jid.startswith("*."):
            domain = raw_jid[2:].strip(".")
            if not domain:
                raise ValueError("Ban target requires a non-empty wildcard domain")
            return cls(
                kind="domain",
                value=domain,
                jid=f"*.{domain}",
                nick=normalized_nick,
            )

        if raw_jid:
            normalized_jid = bare_jid(raw_jid)
            if not normalized_jid:
                raise ValueError("Ban target requires a non-empty JID")
            return cls(
                kind="jid",
                value=normalized_jid,
                jid=normalized_jid,
                nick=normalized_nick,
            )

        if normalized_nick:
            return cls(
                kind="nick",
                value=normalized_nick,
                jid=None,
                nick=normalized_nick,
            )

        raise ValueError("Ban target requires jid/domain or nick")

    @classmethod
    def from_storage(
        cls,
        kind: str,
        value: str,
        *,
        jid: str | None = None,
        nick: str | None = None,
    ) -> BanTarget:
        """Rebuild a canonical target from BanBot's persisted row shape."""
        normalized_kind = str(kind).strip().lower()
        if normalized_kind == "domain":
            raw_domain = _canonical_identity_text(value)
            if raw_domain.startswith("*."):
                raw = raw_domain
            else:
                raw = f"*.{raw_domain}"
            return cls.from_parts(raw, nick)
        if normalized_kind == "jid":
            return cls.from_parts(jid or value, nick)
        if normalized_kind == "nick":
            return cls.from_parts(None, nick or value)
        raise ValueError(f"unsupported ban target type: {kind}")

    @classmethod
    def from_identifier(
        cls,
        identifier: str,
        *,
        plain_domain: bool = False,
    ) -> BanTarget:
        """Parse a command/database identifier into one canonical target.

        Bare dotted strings are nick targets by default because BanBot commands
        historically require ``*.domain.tld`` for an unambiguous domain ban.
        Callers that have already established domain intent may pass
        ``plain_domain=True``.
        """
        raw_value = str(identifier or "").strip().lower()
        value = _canonical_identity_text(raw_value)
        if not value:
            raise ValueError("Ban target identifier must not be empty")
        if value.startswith("*."):
            return cls.from_parts(value)
        if "@" in value:
            return cls.from_parts(value)
        if plain_domain:
            domain = value.strip(".")
            if not domain:
                raise ValueError("Ban target requires a non-empty domain")
            return cls(kind="domain", value=domain, jid=f"*.{domain}")
        return cls.from_parts(None, raw_value)
