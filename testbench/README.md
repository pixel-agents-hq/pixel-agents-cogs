# testbench

Publish corridor bus events manually, for testing.

## Overview

The bot owner can publish any of corridor's six `Agent*` Pub/Sub events
(`AgentReplied`, `AgentToolStarted`, `AgentStatusChanged`,
`AgentHighlighted`, `AgentUnhighlighted`, `AgentPresenceChanged`) on demand
through a Discord UI, without waiting for a real Discord presence change
or message -- useful for exercising CCTV's webview canvas rendering, or
corridor's own dispatch/error-isolation, in isolation.

The event picker, its per-field inputs, and the modal that collects
whatever's left are all built generically from corridor's own event
catalog (`corridor/event_catalog.py`, the same introspection
`corridor/corridor.yaml` is generated from): adding a new `Agent*` event
type to `corridor/domain/models.py` makes it show up here automatically,
with no code change in this cog. A field typed `AgentRef` becomes a
native Discord user select (the picked member supplies
`discord_user_id`/`is_bot`; `guild_id` comes from the invoking guild), a
`Literal[...]`-typed field becomes a select populated from the literal's
own values, and any remaining scalar fields are collected through a
dynamically-built modal.

CCTV subscribes to all six event types, so every event Testbench
publishes can have a visible effect on either office page. Activity/
highlight/status events require the target agent to already be present in
that page's current roster.

## Commands

Owner-only and guild-only: every corridor event needs a `guild_id` from
the invoking guild, and only the bot owner should be able to publish
arbitrary bus events.

| Command | Description |
|---|---|
| `[p]testbench publish` | Pick an event type and fill in its fields through a Discord UI, then publish it onto corridor's bus |
| `[p]testbench list` | Show every event this cog can publish, auto-derived from corridor's event catalog |

## Configuration

Requires [`corridor`](../corridor) (auto-loaded on `cog_load` via
`dependency_loader.ensure_corridor_loaded()` -- `required_cogs` is only a
Downloader install hint):

```text
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs testbench
[p]load testbench
```

Testbench has no settings of its own to configure -- it holds no state
beyond corridor's own event catalog, which it reads fresh on every
`publish`/`list` invocation.

## Related docs

- [`docs/corridor.md`](../docs/corridor.md) -- how `required_cogs` and
  corridor's dependency-loading work in general.
- [`docs/corridor-pubsub-design.md`](../docs/corridor-pubsub-design.md) --
  the Pub/Sub event bus this cog publishes onto.
- [`docs/cctv-design.md`](../docs/cctv-design.md) -- how CCTV subscribes to
  and renders these same six events.
