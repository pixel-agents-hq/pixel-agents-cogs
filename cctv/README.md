# cctv

A live Pixel Agents office for every Discord server, plus the agents'
editor office, served by one cog and one listener.

## Overview

CCTV owns every browser-facing office behavior in this repository: the
Dashboard pages, static asset serving, WebSocket transport, Discord guild
scanning and presence projection, registered-agent projection, display
policy, seat persistence, and browser authorization.

It renders independent, revisioned office states, all read and written
through Pixelagents' office-state facade:

| Page | Dashboard route | WebSocket route | Roster | Write policy |
|---|---|---|---|---|
| Server picker | `/third-party/cctv` | -- | Every open guild office: name, icon, live headcount | -- |
| A guild's office | `/third-party/cctv/office?guild=<server id>` | `/cctv/office/<server id>/ws` | That guild's members plus every registered A2A agent | Bot owner or a member satisfying Corridor's `keyholder` capability in that guild |
| Editor | `/third-party/cctv/editor` | `/cctv/editor/ws` | Registered A2A agents plus the bot's own Discord account | Open to any connected client |

A guild has an office while CCTV is enabled there (`[p]cctv enable`);
`[p]cctv url` gives its link. The offices share static assets and one
aiohttp listener, but each keeps its own client hub, projection service,
current revision, bootstrap lock, and state aggregate, so nothing one
guild's office shows reaches another guild's page or socket. A Pixel Index
load into a guild's office touches no other office, and an Architect or
Painter mutation of the editor office never touches a guild's office. The
old `/third-party/cctv/discord` page redirects to the picker.

Floorplan owns Pixel Index catalogue browsing and loading a selected layout
into the invoking guild's office; it has no Dashboard route or WebSocket of its
own. Architect and Painter own structural and color mutations of the editor
aggregate; neither carries browser transport. See
[Architecture.md](Architecture.md) and
[`docs/cctv-design.md`](../docs/cctv-design.md) for how those pieces fit
together.

## Commands

| Command | Scope | Description |
|---|---|---|
| `[p]cctv url` | Any member | The link to this server's office |
| `[p]cctv status` | Guild admin | Listener, this server's office route/page, assets, revisions/clients, guild settings, and health |
| `[p]cctv dashboard` | Guild admin | Dashboard readiness and the picker, office and editor page links |
| `[p]cctv enable` / `disable` | Guild admin | Open this server's office / close it, disconnecting its viewers |
| `[p]cctv includebots <bool>` | Guild admin | Include or exclude bot accounts from this guild's roster |
| `[p]cctv showoffline <bool>` | Guild admin | Show or hide offline members in this guild's roster |
| `[p]cctv ghostoffline <bool>` | Guild admin | Draw shown offline members translucent or normally |
| `[p]cctv ghostbots <bool>` | Guild admin | Draw bot accounts translucent or normally |
| `[p]cctv richpresence <bool>` | Guild admin | Show or suppress rich-presence activity on this server's office |
| `[p]cctv messages <bool>` | Guild admin | Show or suppress chat-message bubbles on this server's office (they come from every channel the bot can read) |
| `[p]cctv sync` | Guild admin | Force a full resync of this server's office roster |
| `[p]cctv despawnall` | Guild admin | Despawn this server's office roster without disabling it |
| `[p]cctv host <host>` | Bot owner | Set the listener bind host; reload to rebind |
| `[p]cctv port <port>` | Bot owner | Set the listener bind port; reload to rebind |
| `[p]cctv cleardelay <discord\|editor> <seconds>` | Bot owner | Set the activity-clear delay of every guild office or the editor |
| `[p]cctv designer <url>` | Bot owner | Set the character designer `[p]cctv character` links to |
| `[p]cctv publicurl [url]` | Bot owner | Show or set the address browsers reach the Dashboard at, which `[p]cctv url` links from |
| `[p]cctv character` | Any member | Show your office character, its seed code, and a designer link that opens it |
| `[p]cctv character set <px.…>` | Any member | Wear the character a seed code stands for, e.g. one copied from the designer |
| `[p]cctv character reset` | Any member | Go back to the character dealt from your Discord user id |
| `[p]cctv character member <member>` | Building Manager | Show any member's office character, bots and the bot's own account included |
| `[p]cctv character memberset <member> <px.…>` | Building Manager | Dress a member in a seed code's character; the bot's own account also wears it in its Discord replies |
| `[p]cctv character memberreset <member>` | Building Manager | Go back to the character dealt from the member's Discord user id |
| `[p]cctv character agent <agent>` | Bot owner | Show a registered agent's office character (e.g. `architect`) |
| `[p]cctv character agentset <agent> <px.…>` | Bot owner | Dress a registered agent in a seed code's character, on every office and in its Discord replies |
| `[p]cctv character agentreset <agent>` | Bot owner | Go back to the character dealt from the agent's key |

## Characters

Every character on a page wears its own look, and the page's roster lists
exactly the characters shown, each named after its Discord display name
(or agent name). A member who never chose a look wears the character
[pixatar](../pixelagents/README.md#office-characters) deals from their
Discord user id. To change it, design one in Pixel Index's character
designer (`https://pixel-index-staging.nntin.xyz/assets/design` by default),
press "Copy seed", and run `[p]cctv character set <seed>`; every office
re-dresses the member live.

A Building Manager (corridor's `building_manager` group, or the reserved
`owner` group) can change any other member's character, bots included,
with `[p]cctv character memberset <member> <seed>` and undo it with
`[p]cctv character memberreset <member>`.

Agents (pico, architect, painter, animator, every bootcamp agent) wear
characters too, and their Discord replies use that character as their
avatar. pico is the bot's own Discord account, so it is dressed like any
member: `[p]cctv character memberset <bot> <seed>`. The A2A agents wear a
character dealt from their key; the bot owner changes one with
`[p]cctv character agentset <agent> <seed>`.

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
/cctv/office/<server id>/ws
/cctv/editor/ws
```

The same listener answers `GET /cctv/health` with a JSON status snapshot
(listener/assets health and office totals, naming no guild), suitable for an
uptime monitor or reverse-proxy health check that should not depend on
Discord. Set `[p]cctv publicurl https://<dashboard host>` so `[p]cctv url`
hands out absolute links.

CCTV uses its own, fresh Config identity: no Floorplan or Architect setting
is read or migrated into it. Global defaults are listener host
`127.0.0.1`, port `3210`, both activity-clear delays at `2.0` seconds, and
no public URL. Per-guild defaults are `disabled` with bots included and
drawn translucent, offline members hidden (translucent when shown), and
rich-presence and message display on. A newly enabled guild's office
starts from the bundled default layout. See "Who a guild's office shows" in
[`docs/cctv-design.md`](../docs/cctv-design.md) for how presence and
translucency reach the canvas, and the gateway intents showing offline
members needs.

A listener bind failure, a missing webview bundle, an invalid persisted
aggregate, or a missing Dashboard does not unload CCTV. `[p]cctv status`
reports the problem, the affected Dashboard page returns an unavailable
response instead of crashing, and the bot owner is notified best-effort.
Persisted state is never silently reset to recover from an error.

## Related docs

- [Architecture.md](Architecture.md) -- component layout and runtime flows.
- [`docs/cctv-design.md`](../docs/cctv-design.md) -- full design, schemas,
  API reference, and validation/error handling.
