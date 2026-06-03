# RTBL / PubSub Integration

BanBot supports Real-Time Block List (RTBL) PubSub feeds.

Inbound RTBL subscriptions can contain:

* SHA-256 hashes of bare JIDs, compatible with nodes such as `muc_bans_sha256`
* plaintext domains for domain-based matches

When an RTBL entry matches a current occupant, BanBot applies the ban and stores the resulting local ban with `issuer=rtbl`.

## Configuration

```python
RTBL_ENABLED = False
RTBL_ANNOUNCE = True
RTBL_REFRESH_INTERVAL = 3600
RTBL_PUBLISH_ENABLED = False
RTBL_PUBLISH_SERVICE = "pubsub.domain.tld"
RTBL_PUBLISH_JID_NODE = "muc_bans_sha256"
RTBL_PUBLISH_DOMAIN_NODE = "muc_bans_domains"
ALERT_ON_RTBL_REFRESH_FAILURES = 3
```

`RTBL_ENABLED` and own publish identity settings are startup-only. Operational settings such as announcements and refresh interval can be reloaded at runtime.

## Commands

```text
!rtbl list [all|page|last]
!rtbl add <service_jid> <node>
!rtbl delete/remove/del/rm <service_jid> [node]
!rtbl refresh [service_jid] [node]
!banlist rtbl [all|page|last]
!rtbl publish status
!rtbl publish sync
```

Use focused help:

```text
!help rtbl
!help rtbl publish
```

## Server Requirements

RTBL support depends on PubSub support on the XMPP server side.

For inbound subscriptions, the bot must be able to access and subscribe to PubSub nodes exposed by the RTBL provider.

For own RTBL publishing, the bot needs a PubSub service where it can create/configure nodes and publish items.

BanBot can manage subscriptions and publish items, but it cannot provide PubSub functionality by itself.

## Public xmppbl.org RTBL Feeds

BanBot can subscribe to public RTBL feeds such as the lists hosted by `xmppbl.org`.

Example subscriptions:

```text
!rtbl add xmppbl.org muc_bans_sha256
!rtbl add xmppbl.org spam_source_domains
```

Common node types:

* `muc_bans_sha256` - SHA-256 hashes of bare JIDs
* `spam_source_domains` - domains associated with recent spam sources

## Subscription Behavior

* `!rtbl add` validates the service and node.
* The bot attempts to subscribe before storing the subscription.
* The bot refuses to subscribe to its own configured publish nodes.
* Startup and newly added subscriptions fetch current items and scan current occupants immediately.
* Periodic refreshes are quiet when nothing changed.
* Successful refreshes treat fetched node contents as the current active snapshot.
* If fetch fails, times out, returns malformed data, or pagination loops, stale cleanup is skipped to avoid deleting valid local state from an incomplete refresh.

## Snapshot Reconciliation

On successful refresh, BanBot reconciles the local RTBL cache with the fetched PubSub snapshot:

* new hashes/domains are added to the local RTBL cache
* removed hashes/domains are removed from the local RTBL cache
* newly matching current occupants are banned
* stale local `issuer=rtbl` bans may be removed when they are no longer covered by any active RTBL hash/domain

## RTBL Apply Behavior

RTBL application uses the same safety principles as manual bans:

* admin/owner protection is checked
* ignorelist entries are respected
* protected-room scope is respected
* audit events are written
* admin-room announcements are controlled by `RTBL_ANNOUNCE`

`!banlist rtbl` shows raw cached RTBL hash/domain entries. Normal `!banlist` shows applied local bans.

## Own RTBL Publish Feed

When enabled, BanBot can publish local non-RTBL bans into your own PubSub nodes:

```text
!rtbl publish status
!rtbl publish sync
```

The publish feed uses two nodes:

* `RTBL_PUBLISH_JID_NODE` for SHA-256 bare-JID hashes
* `RTBL_PUBLISH_DOMAIN_NODE` for plaintext domain bans

At startup, BanBot performs a publish sanity check by publishing, fetching, and retracting a temporary test item. If this fails, own publishing is disabled for the current runtime and the reason is shown in `!status`.

For Prosody setup details, see [Prosody PubSub Setup](rtbl_pubsub-setup.md).

## Alerts

`ALERT_ON_RTBL_REFRESH_FAILURES` controls when repeated refresh failures are announced to the admin room. Set it to `0` to disable RTBL refresh failure alerts.

## Operational Recommendations

* Use `!rtbl refresh` after adding a subscription to force an immediate check.
* Use `!banlist rtbl all` to inspect cached RTBL entries.
* Use `!status` to confirm subscription counts and publish status.
* Keep `RTBL_REFRESH_INTERVAL` non-zero unless you rely entirely on live PubSub events.
