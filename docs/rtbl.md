# RTBL / PubSub Integration

BanBot supports Real-Time Block List (RTBL) PubSub feeds.

## Concepts

Inbound RTBL subscriptions can contain:

* SHA-256 hashes of bare JIDs, compatible with `muc_bans_sha256`
* Plaintext domains, used for domain-based matches

When an RTBL entry matches a current occupant, BanBot applies the ban and stores the resulting local ban in the main `bans` table with `issuer=rtbl`.

## Server Requirements

RTBL support depends on PubSub support on the XMPP server side.

For inbound subscriptions, the bot must be able to access and subscribe to PubSub nodes exposed by the RTBL provider. For own RTBL publishing, the bot needs a PubSub service where it can create/configure nodes and publish items.

BanBot can manage subscriptions and publish items, but it cannot provide PubSub functionality by itself. The XMPP server must provide the required PubSub/RTBL infrastructure.

For Prosody setups, make sure a PubSub component is configured and that the bot account has the required permissions for the actions you want to use:

* subscribing to external RTBL nodes
* fetching PubSub items
* creating/configuring own publish nodes
* publishing and retracting own RTBL items

## Public xmppbl.org RTBL Feeds

BanBot can subscribe to public RTBL feeds such as the lists hosted by `xmppbl.org`.

Commonly useful nodes are:

```text
xmppbl.org / muc_bans_sha256
```

SHA-256 hashes of JIDs that have been identified as sources of spam in public channels.

```text
xmppbl.org / spam_source_domains
```

XMPP domains that have been identified as recently sending unsolicited spam to users.

Example subscriptions:

```text
!rtbl add xmppbl.org muc_bans_sha256
!rtbl add xmppbl.org spam_source_domains
```

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

BanBot configures own publish nodes with dynamic `pubsub#max_items` retention.
The minimum is 1000 items per node. If the number of active local publish bans
exceeds that, the value is rounded up in 1000-item steps, for example:

```text
0-1000 active items   -> pubsub#max_items = 1000
1001-2000 active items -> pubsub#max_items = 2000
2001-3000 active items -> pubsub#max_items = 3000
```

The bot also auto-grows the matching node when publishing a new local ban would
exceed the currently configured retention limit.

On startup, BanBot performs a publish sanity check for the configured publish
nodes. It publishes a temporary test item, fetches it again, and retracts it.
If this check fails, own RTBL publishing is disabled for the current runtime,
but the bot continues to run. The runtime-disable reason is shown in `!status`
so admins can distinguish a configured-disabled publish feed from a feed that
was disabled because the startup sanity check failed. This usually indicates a
PubSub configuration or permission problem, such as missing node ownership,
missing publish/retract rights, or incompatible access/publish models.

Commands:

```text
!rtbl publish status
!rtbl publish sync
```

See [Prosody PubSub Setup](rtbl_pubsub-setup.md) for manual node creation.
