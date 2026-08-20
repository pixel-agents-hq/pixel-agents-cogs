# floorplan

Serves the Pixel Agents office and mirrors Discord presence into it.

`floorplan` hosts the [Pixel Agents](https://github.com/pixel-agents-hq/pixel-agents)
browser bundle — built by [`pixelagents`](../pixelagents) — as a Red
Dashboard third-party page and serves its WebSocket protocol directly,
turning Discord guild presence (online/idle/dnd status, rich presence,
messages) into animated characters in a shared office. It also browses the
public [Pixel Index](https://github.com/pixel-agents-hq/index) layout
catalogue from Discord and can load a selected layout into the office.
Editing the office layout is delegated to corridor's Keyholder permission
tier — floorplan holds no role IDs of its own.

This cog used to be part of a single combined `pixelagents` Cog; [issue
#21](https://github.com/pixel-agents-hq/pixel-agents-cogs/issues/21) split
vendoring/building (which stayed in `pixelagents`) from everything that
consumes the result (this cog).

## Installing

Requires [`corridor`](../corridor) and [`pixelagents`](../pixelagents)
(both auto-loaded via `required_cogs`):

```
[p]repo add pixel-agents-cogs https://github.com/pixel-agents-hq/pixel-agents-cogs
[p]cog install pixel-agents-cogs floorplan
[p]load floorplan
```

floorplan does not build the webview itself — see
[pixelagents](../pixelagents)'s own README if `[p]floorplan status`'s
Assets field reports it missing (this requires `git`, `node`, and `npm` on
the host; [`toolbox`](../toolbox) can install Node.js/npm for you).

## Configuring

1. Set who may edit the office layout via corridor:
   `[p]corridorsettings` (the Keyholder permission tier).
2. Enable a guild: `[p]floorplan enable`.
3. The office is served at `/third-party/floorplan`; route `/ws` on that
   host to the port from `[p]floorplan wsport` (default `3210`).

## Commands

| Command | Description |
|---|---|
| `[p]floorplan status` | Configuration, client count, asset state |
| `[p]floorplan settings` | Components V2 administration panel |
| `[p]floorplan enable` / `disable` | Guild mirroring on/off |
| `[p]floorplan sync` / `despawnall` | Reconcile / clear agents |
| `[p]floorplan wsport <port>` | Office server port |
| `[p]floorplan index` | Pixel Index endpoints and API health |
| `[p]floorplan layout search [query] [tag] [sort]` | Browse Pixel Index layouts |
| `[p]floorplan layout view <slug>` | View and optionally load a layout |

See [Architecture.md](Architecture.md) for the full command list and
configuration keys.

## Docs

- [Architecture.md](Architecture.md) — internal structure, routing, the
  webview bundle's cross-cog handoff with pixelagents, and the WebSocket
  bootstrap sequence.
- [PERMISSIONS.md](PERMISSIONS.md) — how corridor's permission tiers gate
  layout editing here.
- [`docs/contract-testing.md`](../docs/contract-testing.md) — how this cog's
  dependency on the Pixel Index API is verified in CI.
