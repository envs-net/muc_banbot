# RTBL / PubSub Integration

BanBot supports Real-Time Block List (RTBL) PubSub feeds.

## Concepts

Inbound RTBL subscriptions can contain:

* SHA-256 hashes of bare JIDs, compatible with `muc_bans_sha256`
* Plaintext domains, used for domain-based matches

When an RTBL entry matches a current occupant, BanBot applies the ban and can persist the resulting local ban in the main `bans` table with `issuer=rtbl`.

## Commands

```text
!rtbl list
!rtbl add xmppbl.org muc_bans_sha256
!rtbl add xmppbl.org spam_source_domains
!rtbl delete xmppbl.org muc_bans_sha256
!rtbl refresh
!rtbl refresh xmppbl.org muc_bans_sha256
!banlist rtbl all
!rtbl publish status
!rtbl publish sync
```

## Subscription Behavior

* `!rtbl add` validates the service and node.
* The bot attempts to subscribe before storing the subscription.
* The bot refuses to subscribe to its own configured publish nodes.
* Startup and newly added subscriptions fetch current items and scan current occupants immediately.
* Periodic refreshes are quiet when nothing changed.
* Successful refreshes treat the fetched node contents as the current active snapshot.
* If fetch fails, times out, returns malformed data, or pagination loops, stale cleanup is skipped to avoid deleting valid local state from an incomplete refresh.

## Snapshot Reconciliation

On successful refresh:

```text
items present in fetch       -> stay active
items missing from fetch     -> removed locally
stale issuer=rtbl bans       -> automatically unbanned if no active RTBL source remains
```

This works for rolling RTBL nodes such as lists that retain only recent active items, and for larger archival nodes that support pagination.

## Applied Ban Persistence

`!banlist rtbl` shows raw RTBL subscription entries from `rtbl_hashes` and `rtbl_domains`.

`!banlist` shows applied bans from the main `bans` table. RTBL-applied entries use the 🛡️ marker and `by rtbl`.

For RTBL domain matches, the main banlist stores the concrete matched JID with a comment such as:

```text
RTBL domain ban: *.xmpp.earth
```

The RTBL domain rule itself remains in `rtbl_domains`.

## Ignorelist Interaction

* Exact JID ignorelist entries block manual bans, RTBL hash matches, and RTBL domain matches for that JID.
* Domain ignorelist entries block domain-based bans and RTBL domain matches.
* Domain ignorelist entries do not suppress RTBL hash matches for a specifically listed JID hash.

See [Commands](commands.md#ignorelist--whitelist) for ignorelist commands.

## Admin Protection

Admin/owner protection is checked before any RTBL ban is applied. RTBL domain bans are refused if they would affect a protected admin/owner.

See [Admin Protection](admin-protection.md).

## Own Publish Feed

BanBot can optionally publish local non-RTBL bans to your own PubSub nodes:

* SHA-256 bare-JID hashes to `RTBL_PUBLISH_JID_NODE`
* Plaintext domains to `RTBL_PUBLISH_DOMAIN_NODE`

Inbound RTBL bans are not mirrored into the bot's own publish feed.

Commands:

```text
!rtbl publish status
!rtbl publish sync
```

See [Prosody PubSub Setup](pubsub-setup.md) for manual node creation.
