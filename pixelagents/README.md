# pixelagents

Owns the Pixel Agents webview bundle — serves the office webview through
Red's Web Dashboard and mirrors Discord presence into it.

`pixelagents` hosts the pre-built [Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents)
browser bundle as a Red Dashboard third-party page and serves its WebSocket
protocol directly, turning Discord guild presence (online/idle/dnd status,
rich presence, messages) into animated characters in a shared office. It
also browses the public [Pixel Index](https://github.com/pixel-agents-hq/index)
layout catalogue from Discord and can load a selected layout into the
office. Editing the office layout is delegated to corridor's Keyholder
permission tier — pixelagents holds no role IDs of its own.

## Installing

Requires [`corridor`](../corridor) (auto-loaded via `required_cogs`):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs pixelagents
[p]load pixelagents
```

First `cog_load` clones and builds the pinned Pixel Agents webview commit
into Red's per-cog data directory — this requires `git`, `node`, and `npm`
on the host. See [Architecture.md](Architecture.md#building-webview_dist)
if that build fails or a tool is missing; the cog stays loadable either way
and the bot owner gets a DM.

## Configuring

1. Set who may edit the office layout via corridor:
   `[p]corridorsettings` (the Keyholder permission tier).
2. Enable a guild: `[p]pixelagents enable`.
3. The office is served at `/third-party/pixelagents`; route `/ws` on that
   host to the port from `[p]pixelagents wsport` (default `3210`).

## Commands

| Command | Description |
|---|---|
| `[p]pixelagents status` | Configuration, client count, asset state |
| `[p]pixelagents settings` | Components V2 administration panel |
| `[p]pixelagents enable` / `disable` | Guild mirroring on/off |
| `[p]pixelagents sync` / `despawnall` | Reconcile / clear agents |
| `[p]pixelagents wsport <port>` | Office server port |
| `[p]pixelagents index` | Pixel Index endpoints and API health |
| `[p]pixelagents layout search [query] [tag] [sort]` | Browse Pixel Index layouts |
| `[p]pixelagents layout view <slug>` | View and optionally load a layout |

See [Architecture.md](Architecture.md) for the full command list and
configuration keys.

## Docs

- [Architecture.md](Architecture.md) — internal structure, routing, the
  webview build pipeline, and the WebSocket bootstrap sequence.
- [PERMISSIONS.md](PERMISSIONS.md) — how corridor's permission tiers gate
  layout editing here.
- [`docs/contract-testing.md`](../docs/contract-testing.md) — how this cog's
  dependency on the Pixel Index API and the Pixel Agents webview source is
  verified in CI.
