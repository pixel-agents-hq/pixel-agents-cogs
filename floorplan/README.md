# floorplan

Browse Pixel Index and load catalogue layouts into the Discord CCTV office.

Floorplan owns only Pixel Index API/Web configuration, catalogue browsing, and
the authorized load action. It does not host a Dashboard page, bind a WebSocket
listener, scan Discord guilds, mirror presence, or store office state. Those
browser and projection responsibilities belong to [`cctv`](../cctv).

Selected layouts are validated and written through [`pixelagents`](../pixelagents)
to the revisioned `discord` aggregate persisted by Corridor. A load preserves
the aggregate's avatar-seat records and does not affect the separate editor
aggregate used by Architect and Painter.

## Installing

```text
[p]cog install pixel-agents-cogs floorplan
[p]load floorplan
```

Corridor and Pixelagents are required and loaded on demand. CCTV is not a
dependency: browsing and loading continue to work when no browser surface is
loaded.

## Commands

| Command | Description |
|---|---|
| `[p]floorplan status` | Show Pixel Index endpoints and API health |
| `[p]floorplan index` | Show endpoint configuration and health |
| `[p]floorplan index set <url>` | Set the Pixel Index API base URL |
| `[p]floorplan index setweb <url>` | Set the Pixel Index web base URL |
| `[p]floorplan layout search [query] [tag] [sort]` | Browse catalogue layouts |
| `[p]floorplan layout view <slug>` | View a layout and offer the load action |

The load button is allowed for the bot owner or a member satisfying Corridor's
`keyholder` capability in any guild the bot can resolve. The public search and
view commands are also registered as Corridor LLM tools.

Floorplan uses a fresh Config identity containing only the two Pixel Index URLs.
Previous Floorplan settings and office state are intentionally not migrated.

See [Architecture.md](Architecture.md), [PERMISSIONS.md](PERMISSIONS.md), and
[`docs/contract-testing.md`](../docs/contract-testing.md).
