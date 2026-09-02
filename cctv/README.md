# cctv

Two live Pixel Agents office pages, served by one cog and one listener.

## Overview

CCTV owns every browser-facing office behavior in this repository: both
Dashboard pages, static asset serving, WebSocket transport, Discord guild
scanning and presence projection, registered-agent projection, display
policy, seat persistence, and browser authorization.

It renders two independent, revisioned office states, both read and written
through Pixelagents' office-state facade:

| Page | Dashboard route | WebSocket route | Roster | Write policy |
|---|---|---|---|---|
| Discord | `/third-party/cctv/discord` | `/cctv/discord/ws` | Enabled-guild members plus every registered A2A agent | Bot owner or a member satisfying Corridor's `keyholder` capability in an enabled guild |
| Editor | `/third-party/cctv/editor` | `/cctv/editor/ws` | Registered A2A agents plus the bot's own Discord account | Open to any connected client |

The two pages share static assets and one aiohttp listener, but each keeps
its own client hub, projection service, current revision, bootstrap lock,
activity-clear delay, and state aggregate. A Pixel Index load into the
Discord office never touches the editor office, and an Architect or Painter
mutation of the editor office never touches the Discord office.

Floorplan owns Pixel Index catalogue browsing and loading a selected layout
into the Discord aggregate; it has no Dashboard route or WebSocket of its
own. Architect and Painter own structural and color mutations of the editor
aggregate; neither carries browser transport. See
[Architecture.md](Architecture.md) and
[`docs/cctv-design.md`](../docs/cctv-design.md) for how those pieces fit
together.

## Commands

| Command | Scope | Description |
|---|---|---|
| `[p]cctv status` | Guild admin | Listener, routes, assets, pipeline revisions/clients, guild settings, and health |
| `[p]cctv dashboard` | Guild admin | Dashboard readiness and both page paths |
| `[p]cctv enable` / `disable` | Guild admin | Enable or disable this guild's Discord roster |
| `[p]cctv includebots <bool>` | Guild admin | Include or exclude bot accounts from this guild's roster |
| `[p]cctv richpresence <bool>` | Guild admin | Show or suppress rich-presence activity on the Discord page |
| `[p]cctv messages <bool>` | Guild admin | Show or suppress chat-message activity |
| `[p]cctv sync` | Guild admin | Force a full resync of this guild's Discord roster |
| `[p]cctv despawnall` | Guild admin | Despawn this guild's Discord roster without disabling it |
| `[p]cctv host <host>` | Bot owner | Set the listener bind host; reload to rebind |
| `[p]cctv port <port>` | Bot owner | Set the listener bind port; reload to rebind |
| `[p]cctv cleardelay <discord\|editor> <seconds>` | Bot owner | Set that page's activity-clear delay |

## Configuration

```text
[p]cog install pixel-agents-cogs cctv
[p]load cctv
```

Corridor and Pixelagents are required and loaded on demand. Red Web
Dashboard is needed for the page routes to be reachable; CCTV still loads
and its commands still work without it. The aiohttp listener binds
`127.0.0.1:3210` by default. Route these public WebSocket paths to it
through a reverse proxy:

```text
/cctv/discord/ws
/cctv/editor/ws
```

The same listener answers `GET /cctv/health` with a JSON status snapshot
(listener/assets/pipeline health), suitable for an uptime monitor or
reverse-proxy health check that should not depend on Discord.

CCTV uses its own, fresh Config identity: no Floorplan or Architect setting
is read or migrated into it. Global defaults are listener host
`127.0.0.1`, port `3210`, both activity-clear delays at `2.0` seconds, and
rich-presence/message display enabled. Per-guild defaults are `disabled`
with bots included.

A listener bind failure, a missing webview bundle, an invalid persisted
aggregate, or a missing Dashboard does not unload CCTV. `[p]cctv status`
reports the problem, the affected Dashboard page returns an unavailable
response instead of crashing, and the bot owner is notified best-effort.
Persisted state is never silently reset to recover from an error.

## Related docs

- [Architecture.md](Architecture.md) -- component layout and runtime flows.
- [`docs/cctv-design.md`](../docs/cctv-design.md) -- full design, schemas,
  API reference, and validation/error handling.
