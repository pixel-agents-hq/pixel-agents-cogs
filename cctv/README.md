# cctv

Two live Pixel Agents office pages served by one cog and one listener.

CCTV owns all browser-facing office behavior: Dashboard routes, static assets,
WebSocket connections, guild scanning, Discord presence/activity projection,
registered-agent projection, display policy, seat persistence, and browser
authorization. It observes two independent revisioned office states through
Pixelagents:

| Page | Dashboard route | WebSocket route | Roster | Write policy |
|---|---|---|---|---|
| Discord | `/third-party/cctv/discord` | `/cctv/discord/ws` | Enabled-guild members plus registered A2A agents | Bot owner or keyholder in an enabled guild |
| Editor | `/third-party/cctv/editor` | `/cctv/editor/ws` | Registered A2A agents plus the bot account | Open to connected clients |

The pages share static assets and a TCP listener, but each has its own client
hub, projection service, current revision, bootstrap lock, clear delay, and
state aggregate.

## Installing and routing

```text
[p]cog install pixel-agents-cogs cctv
[p]load cctv
```

Corridor and Pixelagents are required and loaded on demand. Red Web Dashboard is
needed for the page routes. The aiohttp listener binds `127.0.0.1:3210` by
default; route these public WebSocket paths to it:

```text
/cctv/discord/ws
/cctv/editor/ws
```

The former `/ws`, `/architect/ws`, `/third-party/floorplan`, and
`/third-party/architect` routes are intentionally absent. There are no redirects
or compatibility aliases.

## Commands

| Command | Scope | Description |
|---|---|---|
| `[p]cctv status` | Guild admin | Listener, routes, assets, revisions, clients, policy, and health |
| `[p]cctv dashboard` | Guild admin | Dashboard readiness and page paths |
| `[p]cctv enable` / `disable` | Guild admin | Enable or disable the guild's Discord roster |
| `[p]cctv includebots <bool>` | Guild admin | Include or exclude bot accounts |
| `[p]cctv richpresence <bool>` | Guild admin | Show or suppress rich-presence activity |
| `[p]cctv messages <bool>` | Guild admin | Show or suppress message activity |
| `[p]cctv sync` / `despawnall` | Guild admin | Reconcile or clear this guild's roster |
| `[p]cctv host <host>` / `port <port>` | Bot owner | Configure the listener; reload to rebind |
| `[p]cctv cleardelay <discord\|editor> <seconds>` | Bot owner | Set page-specific activity clearing |

CCTV uses a fresh Config identity. No Floorplan or Architect setting is migrated.

## Degraded operation

A listener bind failure, missing bundle/default, invalid aggregate, or missing
Dashboard does not unload CCTV. Status reports the problem, affected Dashboard
pages return an unavailable response, and the bot owner is notified
best-effort. State is never silently reset.

See [Architecture.md](Architecture.md) and
[`docs/cctv-design.md`](../docs/cctv-design.md).
