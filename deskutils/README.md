# deskutils

Get the current time in Discord-native and timezone-aware formats.

A utility cog providing a command to fetch the current time, posted to Discord using native per-user localized timestamps and explicit timezone-aware formatting.

## Installing

Requires [`corridor`](../corridor) (auto-loaded via `required_cogs`):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs deskutils
[p]load deskutils
```

Nothing to configure -- deskutils has no persistent settings.

## Commands

| Command | Description |
|---|---|
| `[p]deskutils time [timezone]` | Show the current time: Discord's native timestamp markup (auto-localized per viewer) plus explicit UTC. Pass an IANA `timezone` (e.g. `America/New_York`) to also show it explicitly in that zone. |

## Docs

<!-- TODO: once this cog is more than a scaffold, add an Architecture.md
describing its layer boundaries, resource ownership, and any
boundary-enforcement tests (see corridor/Architecture.md, pixelagents's, or
floorplan's for the expected shape), and link it here. If the cog owns a
permission model beyond corridor's tiers, add a PERMISSIONS.md too. -->

See [`docs/corridor.md`](../docs/corridor.md) for how `required_cogs` and
corridor's dependency-loading work in general.
