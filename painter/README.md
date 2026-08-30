# painter

Recolors architect's office layout -- tiles, walls, and furniture -- without touching its structure.

painter is a second, A2A-only LLM agent -- never Discord-user-facing,
invoked only by [`pico`](../pico) delegating a sub-task, the same way
[`architect`](../architect) already is. It shares one persistent office
layout with architect: architect knows what tiles/walls/furniture exist
and where, but is colorblind; painter is the color specialist and can
read/change color, but can never add, remove, move, or otherwise
restructure anything -- no such tool exists in its surface at all. See
[`docs/painter-design.md`](../docs/painter-design.md) for the full design,
including the two places this deliberately departs from how the cog was
first proposed (no model vision, A2A-only rather than Discord-facing).

## Installing

Requires [`corridor`](../corridor) and [`pixelagents`](../pixelagents)
(both auto-loaded via `required_cogs`):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs painter
[p]load painter
```

Once loaded, painter registers itself with corridor's shared A2A
listener -- pico automatically gains a `consult_painter` tool with no
pico-side changes needed, the same way `consult_architect` already works.

## Commands

All bot-owner scope, mirroring architect's own settings surface (minus
its WebSocket/webview-specific commands, which painter has no equivalent
of):

| Command | Description |
|---|---|
| `[p]painter status` | Show painter's current settings, A2A registration, and shared layout status |
| `[p]painter maxtoolcalls <count>` | Set the max tool calls painter may make per A2A turn |
| `[p]painter debuglogging <enabled>` | Toggle verbose per-tool-call logging |
| `[p]painter prompt set/reset/show` | Manage painter's system prompt |

## Tools (its own A2A tool-calling loop, not Discord LLM tools)

| Tool | What it does |
|---|---|
| `consult_architect` | Ask architect what tiles/walls/furniture exist and where -- architect is colorblind, never asked about color. |
| `describe_tile_colors` | Current color of every floor/wall tile in a bounded region. |
| `describe_furniture_colors` | Current color of placed furniture, optionally filtered by kind/style. |
| `recolor_tiles` | Recolor every tile in a rectangular region, floor or wall, keeping its kind/pattern. |
| `recolor_furniture` | Recolor a single furniture item by id. |
| `recolor_furniture_by_style` | Recolor every placed item of a given kind+style at once. |

## Docs

See [`docs/painter-design.md`](../docs/painter-design.md) for the full
design and its implementation checklist, and
[`docs/corridor.md`](../docs/corridor.md) for how `required_cogs` and
corridor's dependency-loading work in general.
