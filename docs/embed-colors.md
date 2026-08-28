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

| Category  | Color            | Hex       | Cogs                    |
|-----------|------------------|-----------|--------------------------|
| Agent     | Discord blurple  | `#5865F2` | pico, architect          |
| Room      | Discord teal     | `#1ABC9C` | corridor, floorplan      |
| Furniture | Discord gold     | `#F1C40F` | toolbox, testbench       |
| *(none)*  | Discord default gray | —    | deskutils, pixelagents   |

deskutils (time/text-counting/message-quoting utilities) and pixelagents
(vendors/builds the webview for floorplan to serve, with almost no
chat-facing surface of its own) don't clearly fit Agent, Room, or
Furniture -- rather than guess, both are left uncategorized. Either can be
folded into a category later without any structural change: just pass
`category=` at its own binding site (see §4).

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
`ReplySender`), not one derived from the other. The tempting alternative --
add `category` as a field on `ReplyIdentity` -- doesn't work: corridor's
*own* replies (`corridor/adapters/commands.py`) call `self.send_reply(...)`
directly with no bound identity at all (corridor is its own renderer, see
that file's module docstring), so a category tied to identity would leave
corridor's own embeds uncolored despite corridor being a Room cog. Keeping
`category` a first-class, independently-passed parameter lets corridor
color its own replies Room-teal without also having to invent an author
name/avatar for itself.

Every call site that wants a color states it explicitly:

- `ReplySender` (`corridor/adapters/reply_sender.py`) binds `category`
  once per cog, alongside `owner`/`avatar_path`, via
  `CogBase.reply_sender(owner=..., avatar_path=..., category=...)` --
  pico, architect, testbench, toolbox each pass their own category at
  their one `cog_load` binding site; deskutils omits it (stays
  uncategorized).
- `floorplan/adapters/replies.py` and `corridor/adapters/commands.py`
  bypass `ReplySender` (floorplan needs interaction-aware dispatch;
  corridor is its own renderer) and pass `category=ReplyCategory.ROOM`
  directly at each of their own render/send call sites.
- `pixelagents/adapters/replies.py` passes no `category`, so its replies
  stay the default gray -- the same neutral default every omitted
  `category` argument produces, not a special case.

`category` is `None` in `ReplyMode.TEXT` (no embed exists to color, same
as `author_name`/`author_icon_attachment`).
