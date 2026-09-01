# testbench

Publish corridor bus events manually, for testing.

The bot owner can publish any of corridor's Pub/Sub events
(`AgentReplied`, `AgentPresenceChanged`, ...) on demand through a Discord
UI, without waiting for a real Discord presence change or message --
useful for exercising CCTV's webview canvas rendering, or corridor's
own dispatch/error-isolation, in isolation.

The event picker, its per-field inputs, and the modal that collects
whatever's left are all built **generically from corridor's own event
catalog** (`corridor/event_catalog.py`, mirrored into
`corridor/corridor.yaml`) -- adding a new event type to
`corridor/domain/models.py` makes it show up here automatically, with no
code change in this cog.

CCTV subscribes to all six event types, so every event Testbench publishes can
have a visible effect on either office page. Activity/highlight/status events
require the target agent to be present in that page's current roster.

## Installing

Requires [`corridor`](../corridor) (auto-loaded via `required_cogs`):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs testbench
[p]load testbench
```

## Commands

Owner-only and guild-only: every corridor event needs a `guild_id` from
the invoking guild, and only the bot owner should be able to publish
arbitrary bus events.

| Command | Description |
|---|---|
| `[p]testbench publish` | Pick an event type and fill in its fields through a Discord UI, then publish it onto corridor's bus |
| `[p]testbench list` | Show every event this cog can publish, auto-derived from corridor's event catalog |

## Docs

See [`docs/corridor.md`](../docs/corridor.md) for how `required_cogs` and
corridor's dependency-loading work in general, and
[`docs/corridor-pubsub-design.md`](../docs/corridor-pubsub-design.md) for
the Pub/Sub event bus this cog publishes onto.
