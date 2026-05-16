# Prosody PubSub Setup for RTBL Publish Nodes

If BanBot cannot create or configure its own RTBL publish nodes, create them manually in the Prosody shell.

Replace these values:

* `pubsub.example.org` with `RTBL_PUBLISH_SERVICE`
* `muc_bans_sha256` with `RTBL_PUBLISH_JID_NODE`
* `muc_bans_domains` with `RTBL_PUBLISH_DOMAIN_NODE`
* `adminbot@example.org` with the bot bare JID

## Requirements

Own RTBL publishing requires server-side PubSub support.

On Prosody this means you need a working PubSub component, for example:

```lua
Component "pubsub.example.org" "pubsub"
```

The bot account must have enough permissions to create/configure nodes and publish items on the configured PubSub service.

Inbound RTBL subscriptions only require that the remote provider exposes compatible PubSub nodes and that your server/client can access them.

BanBot can manage subscriptions and publish/retract items, but it is not a PubSub server. The required PubSub infrastructure must be provided by the XMPP server.

## Create Nodes

```lua
pubsub:create_node("pubsub.example.org", "muc_bans_sha256")
pubsub:create_node("pubsub.example.org", "muc_bans_domains")
```

## Set Bot as Owner

```lua
local service = hosts["pubsub.example.org"].modules.pubsub.service
service:set_affiliation("muc_bans_sha256", true, "adminbot@example.org", "owner")
service:set_affiliation("muc_bans_domains", true, "adminbot@example.org", "owner")
```

## Restrict Publishing

```lua
pubsub:set_node_config_option("pubsub.example.org", "muc_bans_sha256", "pubsub#publish_model", "publishers")
pubsub:set_node_config_option("pubsub.example.org", "muc_bans_domains", "pubsub#publish_model", "publishers")
```

Use `publishers`, not `open`, so arbitrary users cannot publish into your RTBL feed.

## Increase Retained Items

BanBot configures `pubsub#max_items` automatically when it has permission to
configure the publish nodes. The minimum is 1000 retained items per node, and
the bot rounds up in 1000-item steps when more local bans need to be published.

If you create or maintain nodes manually, choose a value large enough for the
number of active local bans you publish. For example, 1000 is enough for small
installations, while 2000 keeps room for 1001-2000 active published items:

```lua
pubsub:set_node_config_option("pubsub.example.org", "muc_bans_sha256", "pubsub#max_items", "2000")
pubsub:set_node_config_option("pubsub.example.org", "muc_bans_domains", "pubsub#max_items", "1000")
```

## Startup Publish Sanity Check

When own RTBL publishing is enabled, BanBot checks the configured publish nodes
at startup before syncing local bans. For each node it publishes a temporary
test item, fetches it again, and retracts it.

If the check fails, BanBot disables own RTBL publishing for the current runtime
and continues to start normally. This protects the bot from silently claiming to
publish a feed that other instances cannot read. The runtime-disable reason is
shown in `!status` and logged during startup.

If the startup sanity check fails, verify:

* the PubSub service exists and is reachable
* the configured nodes exist or can be created by the bot
* the bot account may publish and retract items
* `pubsub#publish_model` allows the bot to publish
* `pubsub#access_model` allows subscribers to read items
* the bot has the required affiliation/role on the publish nodes

BanBot can manage subscriptions and publish/retract items, but it is not a
PubSub server. The required PubSub infrastructure must be provided by the XMPP
server.

## Optional Cleanup When Recreating Test Nodes

```lua
pubsub:delete_node("pubsub.example.org", "muc_bans_sha256")
pubsub:delete_node("pubsub.example.org", "muc_bans_domains")
```

## Notes

* Some Prosody setups require the full XEP-0060 field name, e.g. `pubsub#publish_model`, not just `publish_model`.
* Node creation/configuration permissions depend on your Prosody component settings.
* BanBot refuses to subscribe to its own publish nodes as inbound RTBL feeds.
