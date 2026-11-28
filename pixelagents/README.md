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
The bundle is built with relative asset paths for the
[Red Web Dashboard](https://github.com/AAA3A-AAA3A/Red-Web-Dashboard) (by
AAA3A) third-party static route cctv serves it from, so viewing it in a
browser needs that Dashboard installed and set up; see cctv's
[README](../cctv/README.md#red-web-dashboard).

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
`[p]cctv character` commands. `office_character_avatar_path` renders a
character's avatar once per seed code into `cog_data_path/avatars/`; it is
also registered with corridor as the avatar resolver, so every agent's
Discord replies wear its character (see
[`docs/reply-identity-design.md`](../docs/reply-identity-design.md)). While pixatar can't run, each character
wears one of the built default cast, chosen by its key, and nothing is
stored.

`red_delete_data_for_user` deletes a Discord account's character record,
the avatar PNG rendered from it (unless another character wears the same
seed), and its seat in every stored office, for every requester type
(`application/user_data.py`; see
[`docs/architecture.md`](../docs/architecture.md#end-user-data)).

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
relative base marker.

### Discord Activity build

A consumer can also ask for a Discord Activity build of the same bundle
(`build_activity_bundle()`; CCTV does on `[p]cctv activity enable`). It adds
`activity.html` to the bundle: `index.html` with its module scripts held
back, plus `activity/bootstrap.js`, which runs the
[Embedded App SDK](https://github.com/discord/embedded-app-sdk) handshake
and OAuth sign-in and then starts the webview. The page first loads the
consumer's own `activity-config.js` (served next to it), which names the
application, the route that trades the OAuth code for a token, and an
optional `start` hook; see
`infrastructure/webview_build_scripts/activity/bootstrap.ts`. The
bootstrap is bundled from that file with esbuild, from the exact versions
in its `package-lock.json` (`npm ci --ignore-scripts`), and their licenses
ship as `activity/THIRD_PARTY_LICENSES.txt`. Nothing upstream changes, and
the browser page is untouched.

It needs `node` and `npm`. Once asked for, every webview build rebuilds it;
a failed Activity build never fails the webview build. Until a consumer
asks, nothing is installed or built for it. `activity_bundle_status()`
reports whether the bundle holds a current Activity build. `furniture_style_manifest()` and
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
`build_activity_bundle`, `activity_bundle_status`,
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
