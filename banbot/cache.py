"""In-memory ban cache and lookup index helpers."""

from collections.abc import Callable

from .ban_target import BanTarget

BanTuple = tuple[str | None, str | None, int, str | None, str | None]


class CacheMixin:
    """Typed contract for BanBot's in-memory ban indexes."""

    ban_cache: dict[str, BanTuple]
    ban_index_by_jid: dict[str, BanTuple]
    ban_index_by_nick: dict[str, BanTuple]
    ban_index_by_domain: dict[str, list[BanTuple]]
    bare_jid: Callable[[str], str]

    def _build_ban_tuple(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> BanTuple:
        """Return a normalized ban tuple for caches and indexes."""
        target = BanTarget.from_parts(jid, nick)
        return (target.jid, target.nick, until, issuer, comment)


    def _cache_ban(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None:
        """Store a single ban consistently in cache and indexes."""
        target = BanTarget.from_parts(jid, nick)
        ban_tuple = (target.jid, target.nick, until, issuer, comment)

        if target.kind == "jid":
            self.ban_cache[target.value] = ban_tuple
            self.ban_index_by_jid[target.value] = ban_tuple
        elif target.kind == "nick":
            self.ban_cache[target.value] = ban_tuple
            self.ban_index_by_nick[target.value] = ban_tuple
        else:
            self.ban_cache[target.identifier] = ban_tuple
            # One row per domain target; replace instead of appending to avoid stale duplicates after updates.
            self.ban_index_by_domain[target.value] = [ban_tuple]


    def _remove_ban_from_cache(self, identifier: str, ban_jid: str | None = None, ban_nick: str | None = None) -> None:
        """Remove a single JID/nick/domain ban consistently from cache and indexes."""
        candidates = set()
        for value in (identifier, ban_jid, ban_nick):
            if value:
                candidates.add(value.lower())
                if "@" in value and not value.startswith("*."):
                    candidates.add(self.bare_jid(value))

        for candidate in candidates:
            self.ban_cache.pop(candidate, None)
            if candidate.startswith("*."):
                self.ban_index_by_domain.pop(candidate[2:].strip("."), None)
            elif "@" in candidate:
                self.ban_index_by_jid.pop(self.bare_jid(candidate), None)
            else:
                self.ban_index_by_nick.pop(candidate, None)


    def _remove_domain_bans_from_cache(self, domain: str) -> None:
        """Remove all wildcard domain bans associated with a domain from cache and indexes."""
        domain = domain.lower().strip(".")
        wildcard_jid = f"*.{domain}"
        self.ban_cache.pop(wildcard_jid, None)
        self.ban_index_by_domain.pop(domain, None)
