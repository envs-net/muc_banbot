"""Pure RTBL helpers for item classification, PubSub validation, hashing, and payloads."""

import hashlib
import logging
import re

# Matches exactly 64 lowercase hex characters (SHA-256 digest)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Matches a plain domain name or wildcard domain (no @, contains a dot)
_DOMAIN_RE = re.compile(r"^[\w*][\w\-.*]*\.[a-z]{2,}$")
_DOMAIN_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$", re.IGNORECASE)

log = logging.getLogger(__name__)


RTBL_PUBLISH_SANITY_CHECK_REASON = "BanBot RTBL publish sanity check"


def _is_sha256(value: str) -> bool:
    """Return True if value looks like a SHA-256 hex digest."""
    return bool(_SHA256_RE.match(value))


def _is_domain(value: str) -> bool:
    """Return True if value looks like a (wildcard) domain name."""
    return "@" not in value and bool(_DOMAIN_RE.match(value.lstrip("*.")))


def _looks_like_pubsub_service_jid(value: str) -> bool:
    """Return True if value looks like a PubSub service JID or domain JID."""
    value = (value or "").strip().lower()
    if not value or any(ch.isspace() for ch in value) or "/" in value:
        return False

    # PubSub services are usually component/domain JIDs (pubsub.example.org),
    # but allow localpart@domain as well for unusual deployments.
    domain = value.split("@", 1)[1] if "@" in value else value
    if not domain or domain.startswith(".") or domain.endswith("."):
        return False

    labels = domain.split(".")
    if len(labels) < 2:
        return False
    if len(labels[-1]) < 2 or not labels[-1].isalpha():
        return False

    return all(_DOMAIN_LABEL_RE.match(label) for label in labels)


def _looks_like_pubsub_node(value: str) -> bool:
    """Return True if value is a non-empty PubSub node id without whitespace."""
    value = (value or "").strip()
    return bool(value) and not any(ch.isspace() for ch in value)


def _rtbl_hash_jid(jid: str) -> str:
    """Return the SHA-256 hex digest of a bare JID (normalised to lowercase)."""
    return hashlib.sha256(jid.strip().lower().encode()).hexdigest()


def _rtbl_extract_reason(payload) -> str | None:
    """
    Extract a human-readable reason string from a PubSub item payload.
    Supports XEP-0377 <report xmlns='urn:xmpp:reporting:1'> and a
    generic <text> fallback.
    """
    if payload is None:
        return None
    try:
        xml_el = getattr(payload, "xml", payload)
        el = xml_el.find(".//{urn:xmpp:reporting:1}text")
        if el is not None and el.text:
            return el.text.strip()
        el = xml_el.find(".//text")
        if el is not None and el.text:
            return el.text.strip()
    except Exception as exc:
        log.debug("RTBL: could not parse reporting text payload: %s", exc)
    return None


def _rtbl_build_payload(comment: str | None):
    """
    Build a XEP-0377 <report xmlns='urn:xmpp:reporting:1'> XML element
    with an optional <text> child carrying the ban reason.
    """
    from xml.etree import ElementTree as ET

    payload = ET.Element("{urn:xmpp:reporting:1}report")
    if comment:
        text_el = ET.SubElement(payload, "{urn:xmpp:reporting:1}text")
        text_el.text = comment
    return payload
