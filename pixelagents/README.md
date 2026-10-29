# pixelagents

The Pixel Agents bundle, schema, and office-state boundary for this repository.

## Overview

Pixelagents has three responsibilities:

1. clone and build the pinned Pixel Agents webview into Red's writable
   per-cog data directory;
2. expose the only schema-aware facade for the two revisioned office
   aggregates persisted opaquely by Corridor; and
3. deal, store and change the office characters (the look each Discord
   account or genuine agent wears) with the pixatar CLI it builds from
   Pixel Index.

It owns no Dashboard route, WebSocket listener, Discord guild projection,
or Pixel Index catalogue integration -- [`cctv`](../cctv) serves the built bundle and
[`floorplan`](../floorplan) consumes Pixel Index.

### Office-state facade

The public facade selects one of two independent kinds: `discord` or
`editor`. Both states contain `layout`, `seats`, and a monotonically
increasing `revision`. Pixelagents provides reads, field-specific
layout/seat mutations, and atomic watch-and-snapshot registration by
delegating persistence to Corridor.

- Discord layouts are validated against the Pixel Agents wire schema.
- Editor layouts are additionally round-tripped through the Semantic IR
  and furniture-style manifest.
- Seat patches are normalized without replacing unrelated seat fields.
- A missing state initializes lazily from the bundled default with empty
  seats.
- Invalid persisted state raises an explicit validation error and is
  never silently replaced.

Consumers never write Corridor's aggregate directly: CCTV reads both
kinds, Floorplan writes the Discord layout, and Architect and Painter
write the editor layout.

### Office characters

pixatar (`@pixel-index/pixatar`, in pixel-agents-hq/index) turns a seed into
a Pixel Agents character profile: free text deterministically, or a `px.`
seed code exactly. A member who never chose a character wears the one
pixatar deals from their Discord user id; a genuine agent wears the one
dealt from its agent key. The dealt seed code and profile are stored in
Corridor's character records the first time the character is shown, so it
stays the same across restarts and pixatar upgrades. A member who designs a
character in Pixel Index's character designer and copies its seed sets it
with `[p]cctv character set <px.…>`.

`office_character_profile(identity)` is the profile a character wears on the
canvas; `office_character`, `set_office_character_seed`,
`reset_office_character` and `office_character_avatar` back CCTV's
`[p]cctv character` commands. While pixatar can't run, each character
wears one of the built default cast, chosen by its key, and nothing is
stored.

On load, Pixelagents builds the pixatar CLI from the Pixel Index commit
pinned in `infrastructure/pixatar_vendor.commit` (`git`, `node` 22+,
`npm`), into `cog_data_path`, and loads the Python client that drives it
from the same checkout (`packages/pixatar/python`): nothing is
pip-installed. Setting `PIXATAR_CLI` (e.g. `node /path/to/pixatar/dist/cli.js`)
uses that CLI instead of building one, with an installed `pixatar` package
(or an earlier build's checkout) as its client. pixatar draws with the wardrobe of a Pixel Agents
commit, which the vendored webview's pin has to contain; see
[`.claude/skills/advance-vendor/SKILL.md`](../.claude/skills/advance-vendor/SKILL.md).

### Bundle build

On load, Pixelagents checks the pinned upstream commit and builds the
webview with `git`, `node`, `npm`, and Vite if necessary. Build output is
stored under `cog_data_path`, not in the Downloader-managed package tree.
A failed build does not prevent the cog loading; status remains available
and the owner is notified best-effort.

`webview_bundle_status()` exposes the built path, readiness, commit, and
relative base marker. `furniture_style_manifest()` and
`bundled_default_layout()` expose the generated schema inputs needed by
the state facade and agent tools.

## Commands

All commands are bot-owner scoped.

| Command | Description |
|---|---|
| `[p]pixelagents webview commit` | Show the effective upstream commit |
| `[p]pixelagents webview setcommit <commit>` | Override the build commit |
| `[p]pixelagents webview resetcommit` | Restore the source-pinned commit |
| `[p]pixelagents webview rebuild` | Force a fresh clone/build |
| `[p]pixelagents pixatar` | Show the pixatar build and its pinned Pixel Index commit |
| `[p]pixelagents pixatar rebuild` | Force a fresh pixatar clone/build |

## Configuration

Pixelagents has exactly one Red Config factory of its own: the webview
commit override, set through the commands above. Office state is not
Pixelagents' own Config -- it lives behind Corridor's separate, fresh
Config identity, opaque to Corridor and schema-validated only by
Pixelagents' facade.

Every dependent cog (`architect`, `painter`, `floorplan`, `cctv`) reaches
Pixelagents through its cross-cog facade methods
(`office_state`, `set_office_layout`, `set_office_seats`,
`mutate_office_seats`, `watch_office_state`, `webview_bundle_status`,
`furniture_style_manifest`, `bundled_default_layout`, and the
`office_character*` methods) rather than reading Config or the built bundle
directly.

## Related docs

- [`Architecture.md`](Architecture.md) -- internal layering, dependent
  cogs, and key flows.
- [`docs/architect-semantic-ir-design.md`](../docs/architect-semantic-ir-design.md)
  -- the Semantic IR and Pixel Agents JSON codec.
- [`docs/cctv-design.md`](../docs/cctv-design.md) -- browser hosting and
  presence, built on top of this facade.
- [`docs/contract-testing.md`](../docs/contract-testing.md) -- how the
  webview build and outbound message shapes are contract-tested.
