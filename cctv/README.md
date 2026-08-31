# cctv

The only dashboard-hosting cog in this repo. One aiohttp listener serves
two fully independent Pixel Agents pages over Red Dashboard:

- **`/third-party/cctv/discord`** — a live Discord-presence canvas,
  editing gated to the bot owner or a `keyholder`-capability member of an
  enabled guild (a ticket-based `/session` handshake, mirroring
  floorplan's former editor model).
- **`/third-party/cctv/editor`** — an open structural/color editor over
  the office layout `architect`/`painter` mutate through their LLM
  tools, with no editor-authorization concept at all (mirroring
  architect's former dashboard model).

Both pages read/write through corridor's `OfficeState` store
(`kind="discord"`/`"editor"`) via `pixelagents`' `OfficeStateFacade` — the
one validated choke point for layout/seat state, never a private Config
store of cctv's own. Loading `cctv` is the only way either page exists;
`floorplan`, `architect`, and `painter` keep working fully (presence
sync, LLM tool-driven layout mutation) with zero dashboard code of their
own. See [`docs/cctv-design.md`](../docs/cctv-design.md) for the full
design.

## Installing

Requires [`corridor`](../corridor) and [`pixelagents`](../pixelagents)
(both auto-loaded via `required_cogs`):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs cctv
[p]load cctv
```

Set up [Red Web Dashboard](https://red-web-dashboard.readthedocs.io/en/latest/)
separately — `cctv` registers its two pages with it, but does not bundle
or replace it. Configure who may edit the Discord page via
`[p]corridorsettings` (the `keyholder` permission tier), then enable a
guild with `[p]cctv enable`.

## Commands

| Command | Description |
|---|---|
| `[p]cctv status` | Show the listener, dashboard, and per-page status |
| `[p]cctv host <host>` / `[p]cctv port <port>` | Bot-owner only; persist-only, reload to rebind |
| `[p]cctv discordcleardelay <seconds>` / `[p]cctv editorcleardelay <seconds>` | How long a message-activity indicator stays visible on each page |
| `[p]cctv richpresence <true\|false>` | Whether rich presence is shown on the Discord page |
| `[p]cctv messages <true\|false>` | Whether Discord messages appear as tool bubbles on the Discord page |
| `[p]cctv enable` / `[p]cctv disable` | Enable/disable Discord-page presence mirroring for this guild |
| `[p]cctv includebots <true\|false>` | Whether bot users are mirrored on the Discord page |
| `[p]cctv sync` | Manually reconcile this guild's members against Discord presence |
| `[p]cctv despawnall` | Despawn this guild's tracked agents without disabling the cog |

## Docs

- [`docs/cctv-design.md`](../docs/cctv-design.md) — the full design: why
  this extraction happened, corridor's `OfficeState`/`OfficeStateChanged`
  surface, pixelagents' facade, and the two-pipeline internal shape this
  cog implements.
- [`docs/corridor.md`](../docs/corridor.md) — how `required_cogs` and
  corridor's dependency-loading work in general.
