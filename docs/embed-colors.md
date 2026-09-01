# Embed colors: a shared category scheme

**Status: implemented.**

## 1. Problem

Before this change, `discord.Embed` was constructed at exactly one place
in the whole codebase -- `build_reply_payload`
(`corridor/adapters/api.py`), shared by every cog's replies since
docs/reply-identity-design.md consolidated embed-building there -- and it
never set a `color`, so every cog's reply embeds rendered as Discord's own
default gray. The only place in the codebase that set an explicit color at
all was `floorplan/adapters/settings_panel.py`'s Components V2 settings
panel (`discord.ui.Container(accent_colour=discord.Color.blurple())`), an
isolated, one-off choice unrelated to the reply-embed pipeline. There was
no scheme to preserve; this establishes one from scratch.

## 2. Decision

Cogs are grouped into three visual categories, each with one shared color.
A cog with no clear category gets no color (Discord's default gray) rather
than being forced into a bucket that doesn't fit:

| Category  | Color            | Hex       | Cogs                                   |
|-----------|------------------|-----------|------------------------------------------|
| Agent     | Discord blurple  | `#5865F2` | pico, architect, painter                 |
| Room      | Discord teal     | `#1ABC9C` | corridor, floorplan, cctv                |
| Furniture | Discord gold     | `#F1C40F` | toolbox, testbench, deskutils            |
| *(none)*  | Discord default gray | —    | pixelagents, suggestionbox              |

deskutils' commands are utilities (time, text-counting, message-quoting) in
the same vein as toolbox/testbench, so it's folded into Furniture. `painter`
(a later, color-only A2A agent, added after this scheme shipped) followed
architect into Agent, and `cctv` (the office dashboards extracted out of
floorplan, see `docs/cctv-design.md`) followed floorplan into Room --
both new cogs just passed `category=` at their own binding site, exactly
as this design anticipated. pixelagents (vendors/builds the webview for
CCTV to serve, with almost no chat-facing surface of its own) and
`suggestionbox` (an MCP feedback server, likewise reply-light) still don't
clearly fit Agent, Room, or Furniture -- rather than guess, both are left
uncategorized. Either can be folded into a category later without any
structural change: just pass `category=` at its own binding site (see
§4).

The `floorplan/adapters/settings_panel.py` Container's `accent_colour` is
a separate UI surface (Components V2, not a classic embed) and is out of
scope here -- this scheme covers `discord.Embed` colors only.

## 3. Where the mapping lives

`corridor/domain/models.py` adds `ReplyCategory` (a `StrEnum`: `AGENT`,
`ROOM`, `FURNITURE`) and a `category: ReplyCategory | None` field on
`RenderedReply`. `corridor/domain/reply_colors.py` holds the actual
mapping, `REPLY_CATEGORY_COLORS: dict[ReplyCategory, int]` -- plain hex
ints, not `discord.Colour`, matching this package's "zero framework
imports" convention (see `agent_directory.py`'s docstring for the one
deliberate exception). `corridor/adapters/api.py`'s `build_reply_payload`
is the only place that turns a `category` into a real embed `color`, via
`REPLY_CATEGORY_COLORS.get(reply.category)`.

Both are corridor's to own: corridor already is "shared reply style and
permission tiers for office-cogs" (`corridor/info.json`), and every
category color is applied through corridor's one shared
`build_reply_payload`.

## 4. Why `category` is independent of `ReplyIdentity`

`ReplyIdentity` (owner name + avatar) and `category` (embed color) are
deliberately separate parameters throughout the reply pipeline
(`ReplyService.render`, `CogBase.render_reply`/`send_reply`,
`ReplySender`), not one derived from the other. A cog can bind one without
the other -- pixelagents binds an identity with no category (stays
uncategorized), and nothing stops a future cog from wanting a category
with no author identity. Folding `category` into `ReplyIdentity` would
make that impossible without a workaround.

Every cog binds its own category exactly once, at the one place it already
binds everything else about how its replies render, so no call site ever
repeats it:

- `ReplySender` (`corridor/adapters/reply_sender.py`) binds `category`
  once per cog, alongside `owner`/`avatar_path`, via
  `CogBase.reply_sender(owner=..., avatar_path=..., category=...)`.
  pico, architect, painter, testbench, toolbox, deskutils, and
  **corridor itself** (bound in `CogBase.__init__`, since corridor is a
  Room cog like floorplan) each pass their own category at that one
  binding site. corridor's own commands (`corridor/adapters/commands.py`)
  go through `self._reply.send_reply(...)` the same way every dependent
  cog's commands go through their own bound `self._reply` -- not
  `self.send_reply(...)` directly -- so `category=` is never repeated
  across its reply call sites. `suggestionbox` also binds via
  `reply_sender` but passes no `category`, staying uncategorized like
  pixelagents below.
- `floorplan/adapters/replies.py` and `cctv/adapters/replies.py` both
  bypass `ReplySender` (each needs interaction-aware dispatch) but still
  bind `category=ReplyCategory.ROOM` at the one `render_reply(...)` call
  every one of their commands funnels through.
- `pixelagents/adapters/replies.py` passes no `category`, so its replies
  stay the default gray -- the same neutral default every omitted
  `category` argument produces, not a special case.

`category` is `None` in `ReplyMode.TEXT` (no embed exists to color, same
as `author_name`/`author_icon_attachment`).
