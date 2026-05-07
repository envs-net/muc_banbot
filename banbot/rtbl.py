"""RTBL (Real-Time Block List) support composed from focused mixins.

Two item types are handled within RTBL nodes:
  - JID bans   : SHA-256 hashed bare JIDs (64 lowercase hex chars)
                 Compatible with xmppbl.org muc_bans_sha256.
  - Domain bans: plaintext domain names (e.g. 'spam.example.org')
                 Applied as wildcard bans (*.domain).

Item type is detected automatically from the item ID format when receiving
from any subscribed node.

Own publish feed (optional, requires a PubSub service on the local server):
  - JID bans    -> RTBL_PUBLISH_JID_NODE    (default: muc_bans_sha256)
  - Domain bans -> RTBL_PUBLISH_DOMAIN_NODE (default: muc_bans_domains)

Bans from subscribed RTBL feeds are stored in separate lookup tables
(rtbl_hashes, rtbl_domains). When an RTBL entry is actually applied to a
protected room, the resulting ban is also persisted in the main bans table so
!banlist, !why and sync state stay consistent.
Admin/owner protection is always enforced before any RTBL ban is applied.
"""

from .rtbl_apply import RtblApplyMixin
from .rtbl_commands import RtblCommandMixin
from .rtbl_db import RtblDatabaseMixin
from .rtbl_pubsub import RtblPubSubMixin
from .rtbl_publish import RtblPublishMixin
from .rtbl_utils import _rtbl_build_payload, _rtbl_extract_reason, _rtbl_hash_jid


class RtblMixin(
    RtblDatabaseMixin,
    RtblPubSubMixin,
    RtblApplyMixin,
    RtblCommandMixin,
    RtblPublishMixin,
):
    """RTBL support composed from focused mixins."""

    _rtbl_hash_jid = staticmethod(_rtbl_hash_jid)
    _rtbl_extract_reason = staticmethod(_rtbl_extract_reason)
    _rtbl_build_payload = staticmethod(_rtbl_build_payload)
