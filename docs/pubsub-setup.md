# Prosody PubSub Setup for RTBL Publish Nodes

If BanBot cannot create or configure its own RTBL publish nodes, create them manually in the Prosody shell.

Replace these values:

* `pubsub.example.org` with `RTBL_PUBLISH_SERVICE`
* `muc_bans_sha256` with `RTBL_PUBLISH_JID_NODE`
* `muc_bans_domains` with `RTBL_PUBLISH_DOMAIN_NODE`
* `adminbot@example.org` with the bot bare JID

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

```lua
pubsub:set_node_config_option("pubsub.example.org", "muc_bans_sha256", "pubsub#max_items", "1000")
pubsub:set_node_config_option("pubsub.example.org", "muc_bans_domains", "pubsub#max_items", "1000")
```

## Optional Cleanup When Recreating Test Nodes

```lua
pubsub:delete_node("pubsub.example.org", "muc_bans_sha256")
pubsub:delete_node("pubsub.example.org", "muc_bans_domains")
```

## Notes

* Some Prosody setups require the full XEP-0060 field name, e.g. `pubsub#publish_model`, not just `publish_model`.
* Node creation/configuration permissions depend on your Prosody component settings.
* BanBot refuses to subscribe to its own publish nodes as inbound RTBL feeds.
