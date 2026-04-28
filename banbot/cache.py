"""In-memory ban cache and lookup index helpers."""

from .utils import normalize_ban_target


class CacheMixin:
    def _build_ban_tuple(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> tuple[str | None, str | None, int, str | None, str | None]:
        """Return a normalized ban tuple for caches and indexes."""
        target_type, target, normalized_jid, normalized_nick = normalize_ban_target(jid, nick)
        if target_type == "domain" and normalized_jid is None:
            normalized_jid = f"*.{target}"
        return (normalized_jid, normalized_nick, until, issuer, comment)


    def _cache_ban(
        self,
        jid: str | None,
        nick: str | None,
        until: int,
        issuer: str | None,
        comment: str | None,
    ) -> None:
        """Store a single ban consistently in cache and indexes."""
        target_type, target, normalized_jid, normalized_nick = normalize_ban_target(jid, nick)
        if target_type == "domain" and normalized_jid is None:
            normalized_jid = f"*.{target}"
        ban_tuple = (normalized_jid, normalized_nick, until, issuer, comment)

        if target_type == "jid":
            self.ban_cache[target] = ban_tuple
            self.ban_index_by_jid[target] = ban_tuple
        elif target_type == "nick":
            self.ban_cache[target] = ban_tuple
            self.ban_index_by_nick[target] = ban_tuple
        elif target_type == "domain":
            wildcard = f"*.{target}"
            self.ban_cache[wildcard] = ban_tuple
            # One row per domain target; replace instead of appending to avoid stale duplicates after updates.
            self.ban_index_by_domain[target] = [ban_tuple]


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
