# deskutils

Small stateless utilities for time, text counting, and message quoting.

A utility cog providing Discord-native time output, character/word counts,
and safe quotes of messages the invoking member can access.

## Installing

Requires [`corridor`](../corridor) (auto-loaded on `cog_load` via `dependency_loader.ensure_corridor_loaded()` -- `required_cogs` is only a Downloader install hint):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs deskutils
[p]load deskutils
```

Nothing to configure -- deskutils has no persistent settings.

## Commands

| Command | Description |
|---|---|
| `[p]deskutils time [timezone]` | Show the current time: Discord's native timestamp markup (auto-localized per viewer) plus explicit UTC. Pass an IANA `timezone` (e.g. `America/New_York`) to also show it explicitly in that zone. Requires the `employee` permission tier (unrestricted by default). |
| `[p]deskutils count <text>` | Count all characters (including whitespace) and whitespace-delimited words in text. |
| `[p]deskutils quote [message-link]` | Quote the replied-to message, or a same-server message supplied by link, with its author and source link. Server-only (not usable in DMs). |

All three commands are registered as Corridor LLM tools, so
if [`pico`](../pico) is installed, loaded, and enabled for a guild, a user
can ask for time or text statistics, or reply to a message and ask Pico to
quote it. `count` and `quote` deliberately use bare `@llm_tool()` decorators:
their names come from the qualified Discord command, descriptions from
their docstrings, parameter descriptions from parameter names, and
availability from native command checks. `time` keeps its existing explicit
metadata. Each tool returns the information displayed to the LLM. See
[`docs/corridor-tool-registry-design.md`](../docs/corridor-tool-registry-design.md).
Nothing to configure for this either: registration happens automatically at
`cog_load` and is inert if pico isn't loaded.

## Docs

<!-- TODO: once this cog is more than a scaffold, add an Architecture.md
describing its layer boundaries, resource ownership, and any
boundary-enforcement tests (see corridor/Architecture.md, pixelagents's, or
floorplan's for the expected shape), and link it here. If the cog owns a
permission model beyond corridor's tiers, add a PERMISSIONS.md too. -->

See [`docs/corridor.md`](../docs/corridor.md) for how `required_cogs` and
corridor's dependency-loading work in general, and
[`docs/corridor-tool-registry-design.md`](../docs/corridor-tool-registry-design.md)
for the cross-cog tool registry this cog registers into.
