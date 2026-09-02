# floorplan

Browse the Pixel Index catalogue and load a selected layout into the Discord
office.

## Overview

Floorplan is a thin Pixel Index HTTP adapter: it owns Pixel Index API/Web
endpoint configuration, catalogue search and detail browsing, and the
authorized action that writes a chosen layout into Pixelagents' `discord`
office-state aggregate. It holds no browser surface of its own — no
Dashboard page, no WebSocket listener, no guild scanning, no presence
mirroring, no office-state storage. [`cctv`](../cctv) serves the webview and
renders the office canvas from that aggregate; floorplan only fills it.

A load through floorplan replaces the aggregate's `layout` field and leaves
its `seats` field untouched; it never touches the separate `editor` aggregate
that Architect and Painter mutate.

## Commands

| Command | Description | Required permission |
|---|---|---|
| `[p]floorplan status` | Show Pixel Index endpoints and API health | Server admin |
| `[p]floorplan index` | Show endpoint configuration and health | Server admin |
| `[p]floorplan index set <url>` | Set the Pixel Index API base URL | Server admin |
| `[p]floorplan index setweb <url>` | Set the Pixel Index web base URL | Server admin |
| `[p]floorplan layout search [query] [tag] [sort]` | Search the catalogue and browse results | Corridor `employee` |
| `[p]floorplan layout view <slug>` | Show one layout and offer a load button | Corridor `employee` (load button: bot owner or `keyholder`) |

`layout search` and `layout view` are also registered as Corridor LLM tools
(`floorplan_layout_search`, `floorplan_layout_view`), gated by the same
`employee` requirement and callable by any agent whose tool loop has them
enabled. See [PERMISSIONS.md](PERMISSIONS.md) for the full permission model.

## Configuration

Floorplan keeps a single, fresh Red Config identity holding exactly two
values:

| Key | Default | Set by |
|---|---|---|
| `pixel_index_api_url` | `https://pixel-index-api-staging.nntin.xyz` | `[p]floorplan index set <url>` |
| `pixel_index_web_url` | `https://pixel-index.vercel.app` | `[p]floorplan index setweb <url>` |

Both values are normalized to an absolute `http`/`https` URL with no trailing
slash before being stored; an invalid URL is rejected with a usage message
instead of being saved. Nothing else lives in this Config identity — no
layout, no seats, no route, no listener setting.

Corridor and Pixelagents are required and auto-loaded on `cog_load`. CCTV is
not a dependency: catalogue browsing and loading work whether or not a
browser surface is loaded.

## Related docs

- [Architecture.md](Architecture.md) — internal layering, dependency edges, and key flows
- [PERMISSIONS.md](PERMISSIONS.md) — permission tiers per command
- [`docs/contract-testing.md`](../docs/contract-testing.md) — the Pixel Index consumer contract generated from this cog
