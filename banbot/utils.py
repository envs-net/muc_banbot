"""Pure helper functions for time formatting, JID handling, domains, and pagination."""

def parse_duration(s: str) -> int:
    """
    Parse a duration string into seconds.
    Supported suffixes: s=seconds, m=minutes, h=hours, d=days
    Example: '10m' -> 600
    """
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if len(s) < 2 or s[-1].lower() not in units:
        raise ValueError("Invalid duration format (use 10s, 10m, 2h, 1d)")
    try:
        value = int(s[:-1])
    except ValueError:
        raise ValueError("Invalid duration number")
    if value <= 0:
        raise ValueError("Duration must be greater than zero")
    return value * units[s[-1].lower()]


def human_time(seconds: int) -> str:
    """
    Convert seconds to human-readable string.
    Example: 3661 -> '1h 1m 1s'
    """
    seconds = int(seconds)
    if seconds <= 0:
        return "permanent"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s:
        parts.append(f"{s}s")
    return " ".join(parts)


def bare_jid(jid: str | None) -> str | None:
    """Return the bare JID without resource."""
    return jid.split("/")[0].lower() if jid else None


def normalize_actor(actor: str | None) -> str | None:
    """Remove an XMPP resource from actor JIDs while preserving symbolic actors."""
    if not actor:
        return actor
    value = str(actor).strip()
    if "@" in value:
        return bare_jid(value)
    return value


def safe_jid(text) -> str:
    """Make JIDs less likely to be auto-linked or pinged in chat clients."""
    return str(text).replace("@", "@\u200b")


def validate_jid_format(jid: str) -> bool:
    """Validate JID format (user@domain.tld)."""
    if not jid or "@" not in jid:
        return False
    parts = jid.split("@")
    if len(parts) != 2:
        return False
    local, domain = parts
    if not local or not domain or "." not in domain:
        return False
    return True


def validate_domain_ban(domain: str) -> tuple[bool, str]:
    """
    Validate domain ban format.
    Input can be either '*.domain.tld' or 'domain.tld'.
    - Blocks: *.tld
    - Allows: *.domain.tld and *.sub.domain.tld
    """
    domain = domain.lower().strip()

    if domain.startswith("*."):
        domain = domain[2:]

    domain = domain.strip(".")
    parts = [p for p in domain.split(".") if p]

    if len(parts) < 2:
        return False, (
            f"❌ Domain '*.{domain}' is too generic. "
            "Specify more precise domain (e.g., *.domain.tld)."
        )

    return True, ""


def domain_matches(user_domain: str | None, banned_domain: str | None) -> bool:
    """Return True if a user domain matches a wildcard ban domain.

    A ban for *.domain.tld matches both domain.tld and sub.domain.tld.
    """
    if not user_domain or not banned_domain:
        return False
    user_domain = user_domain.lower().strip(".")
    banned_domain = banned_domain.lower().strip(".")
    if not user_domain or not banned_domain:
        return False
    return user_domain == banned_domain or user_domain.endswith("." + banned_domain)


def looks_like_domain(text: str | None) -> bool:
    """Return True if text looks like a bare domain without wildcard.

    Bare domains are rejected for ban/unban commands so users do not
    accidentally create nick bans such as 'example.com'.
    """
    if not text:
        return False
    text = text.strip().lower()
    return (
        "." in text
        and "@" not in text
        and "/" not in text
        and not text.startswith("*.")
    )


def normalize_ban_target(jid: str | None = None, nick: str | None = None) -> tuple[str, str, str | None, str | None]:
    """Return (target_type, target, normalized_jid, normalized_nick)."""
    normalized_jid = bare_jid(jid) if jid and not jid.startswith("*.") else (jid.lower() if jid else None)
    normalized_nick = nick.lower().strip() if nick else None

    if normalized_jid and normalized_jid.startswith("*."):
        domain = normalized_jid[2:].strip(".")
        return "domain", domain, normalized_jid, normalized_nick

    if normalized_jid:
        return "jid", normalized_jid, normalized_jid, normalized_nick

    if normalized_nick:
        return "nick", normalized_nick, None, normalized_nick

    raise ValueError("Ban target requires jid/domain or nick")



def wants_all_pages(args: list[str]) -> bool:
    """Return True when command arguments request unpaginated output."""
    return any(str(arg).lower() == "all" for arg in args)


def without_all_pages_arg(args: list[str]) -> list[str]:
    """Return args with any standalone all-paging marker removed."""
    return [arg for arg in args if str(arg).lower() != "all"]


def get_list_page_size(obj=None, default: int = 10) -> int:
    """Return the configured page size for paginated command output."""
    value = getattr(obj, "list_page_size", None) if obj is not None else None
    if value is None:
        try:
            import config  # type: ignore

            value = getattr(config, "LIST_PAGE_SIZE", default)
        except Exception:
            value = default
    try:
        return max(1, int(value))
    except Exception:
        return default

def paginate_lines(lines: list[str], page: int, per_page: int = 10) -> tuple[list[str], int, int, int]:
    """
    Paginate a list of lines.
    Returns: (page_lines, current_page, total_pages, total_items)
    """
    total_items = len(lines)
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    current_page = max(1, min(page, total_pages))
    start = (current_page - 1) * per_page
    end = start + per_page
    return lines[start:end], current_page, total_pages, total_items

def resolve_page(page: int, total_items: int, per_page: int = 10) -> int:
    """
    Resolve a page number, supporting -1 as a sentinel for the last page.

    Args:
        page:        Requested page number, or -1 for the last page.
        total_items: Total number of items to paginate.
        per_page:    Items per page (default: 10).

    Returns:
        Resolved page number (always >= 1).
    """
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    if page == -1:
        return total_pages
    return max(1, min(page, total_pages))
