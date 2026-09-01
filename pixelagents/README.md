# pixelagents

The Pixel Agents bundle, schema, and office-state boundary for this repository.

Pixelagents has two responsibilities:

1. clone and build the pinned Pixel Agents webview into Red's writable per-cog
   data directory; and
2. expose the only schema-aware facade for the two revisioned office
   aggregates persisted opaquely by Corridor.

It owns no Dashboard route, WebSocket listener, Discord guild projection, or
Pixel Index integration. CCTV serves the bundle; Floorplan consumes Pixel Index.

## Office-state facade

The public facade selects one of two independent kinds: `discord` or `editor`.
Both states contain `layout`, `seats`, and a monotonically increasing `revision`.
Pixelagents provides reads, field-specific layout/seat mutations, and atomic
watch-and-snapshot registration by delegating persistence to Corridor.

- Discord layouts are validated against the Pixel Agents wire schema.
- Editor layouts are additionally round-tripped through the Semantic IR and
  furniture-style manifest.
- Seat patches are normalized without replacing unrelated seat fields.
- A missing state initializes lazily from the bundled default with empty seats.
- Invalid persisted state raises an explicit validation error and is never
  silently replaced.

Consumers never write Corridor's aggregate directly. CCTV uses both kinds,
Floorplan writes the Discord layout, and Architect/Painter write the editor
layout.

## Bundle build

On load, Pixelagents checks the pinned upstream commit and builds the webview
with `git`, `node`, `npm`, and Vite if necessary. Build output is stored under
`cog_data_path`, not in the Downloader-managed package tree. A failed build does
not prevent the cog loading; status remains available and the owner is notified
best-effort.

`webview_bundle_status()` exposes the built path, readiness, commit, and relative
base marker. `furniture_style_manifest()` and `bundled_default_layout()` expose
the generated schema inputs needed by the state facade and agent tools.

## Commands

All commands are bot-owner scoped.

| Command | Description |
|---|---|
| `[p]pixelagents webview commit` | Show the effective upstream commit |
| `[p]pixelagents webview setcommit <commit>` | Override the build commit |
| `[p]pixelagents webview resetcommit` | Restore the source-pinned commit |
| `[p]pixelagents webview rebuild` | Force a fresh clone/build |

See [Architecture.md](Architecture.md),
[`docs/cctv-design.md`](../docs/cctv-design.md), and
[`docs/contract-testing.md`](../docs/contract-testing.md).
