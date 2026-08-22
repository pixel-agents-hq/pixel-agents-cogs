# corridor: shared permissions and reply style

[`corridor/`](../corridor/) is a hidden, `COG`-type cog that's actually
loaded and running — unlike [`contracts/`](../contracts/), which is a
`SHARED_LIBRARY`-type consumer-driven contract *testing* package other cogs
never import at runtime. Every cog generated from [`.cookiecutter/cog-cookiecutter`](../.cookiecutter/cog-cookiecutter)
declares it as a `required_cogs` dependency and calls into it for two
things: **who is allowed to run a command**, and **how a reply gets
formatted**. Neither is reinvented per cog.

## Why a shared cog instead of per-cog settings

Before corridor, each cog would have needed its own permission-role
configuration and its own text-vs-embed reply logic — meaning a server
admin configuring "who counts as a keyholder" once per cog, and every cog
maintaining its own copy of embed-building boilerplate. corridor owns one
guild-wide `Config` store instead: configure the permission groups and
reply style once, and every cog that depends on corridor respects it
immediately. This is the kind of sharing that's worth the `required_cogs`
coupling — it's guild-wide *state* that genuinely needs one source of
truth, not shared UI code wrapped around independent per-cog logic.

## The permission model

Defined in [`corridor/domain/models.py`](../corridor/domain/models.py) as an
open, admin-configurable group model rather than a fixed enum:
`PermissionGroupDef` (`key`/`label`/`role_ids`/`permission_names`) is one
tier satisfied by role membership and/or a Discord permission, and
`PermissionSettings` holds a per-guild, admin-managed tuple of those groups
plus the two reserved, non-role-backed keys below. Groups seed by default
with `building_manager` ("Building Manager") and `keyholder` ("Keyholder")
— see `DEFAULT_PERMISSION_GROUPS` in
[`corridor/infrastructure/settings_repository.py`](../corridor/infrastructure/settings_repository.py)
— but a guild admin can add, remove, or rename further groups at any time
from the settings panel (see "Configuring it" below).

| Group | Key | Who satisfies it |
|---|---|---|
| Owner | `owner` (`OWNER_KEY`, reserved) | The bot owner (Red's owner concept, not guild owner) OR a member with guild Administrator permission. |
| Employee | `employee` (`EMPLOYEE_KEY`, reserved) | Everyone. Never restricts. |
| Building Manager *(default)* | `building_manager` | Members holding one of the roles, or one of the Discord permissions, a guild admin has assigned to this group. |
| Keyholder *(default)* | `keyholder` | Members holding one of the roles, or one of the Discord permissions, a guild admin has assigned to this group. |
| *(any admin-added group)* | *(admin-chosen at creation, stable thereafter)* | Members holding one of the roles, or one of the Discord permissions, a guild admin has assigned to that group. |

Dependent cogs reference a group by its plain string `key` — e.g.
floorplan hardcodes `"keyholder"` — not an enum member.
`corridor/adapters/cog_base.py`'s `capabilities_satisfy(member, group_key:
str)` / `require_permission(ctx, group_key: str)` both take `str`.

**Groups are independent, unranked tiers** — holding one does not imply
another. A member with only a Keyholder role fails a
`require_permission("building_manager")` check, and vice versa. `owner`
bypasses every check regardless of group, and `employee` never restricts.
A group can have any number of roles and any number of permissions
assigned, including zero of either — new groups start with none of either
until an admin assigns some, and a group's `key` is stable once created
while its `label` (the display name) can be renamed freely.

**Roles and permissions are two independent, OR'd criteria** — a member
satisfies a group by matching *either* one, not both. Holding any one of
the group's assigned roles is enough regardless of permissions, and having
any one of the group's assigned Discord permissions is enough regardless of
roles; a group configured with both roles and permissions is satisfied by a
member who matches only one of the two. `permission_names` is a curated
subset of `discord.Permissions` flags relevant to a moderation/management
tier (e.g. `kick_members`, `ban_members`, `manage_roles`) exposed in the
settings panel — not all ~40 flags, both because a single Discord select
maxes out at 25 options and because most flags aren't relevant to this use
case; see `CURATED_PERMISSIONS` in
[`corridor/adapters/settings_ui.py`](../corridor/adapters/settings_ui.py)
for the exact list.

Each group's role set is a `frozenset[int]` of Discord role IDs, and its
permission set a `frozenset[str]` of `discord.Permissions` flag names (kept
as plain strings, not a raw bitmask or `discord.Permissions` object, so the
domain layer stays free of discord.py imports — translation to/from real
`discord.Permissions` happens only at the adapter boundary) — a guild can
assign more than one role or permission per group, and either set is
mutable at any time (add/remove without redefining the whole group). This
is computed per check via `MemberCapabilities` (`corridor/domain/models.py`),
which the pure `PermissionService`
([`corridor/application/permission_service.py`](../corridor/application/permission_service.py))
resolves from a member's role IDs, granted permission names, and the bot's
owner ID set — no discord.py or Red imports in that resolution logic, so
it's unit-tested with plain fakes, no framework stubbing needed.

## Reply style

Also guild-wide, also independent of any specific cog: whether replies go
out as plain text or a rich embed, and if embed, whether it shows a
timestamp, a footer, and where its icon comes from — a custom URL, the
bot's own avatar, or the server's icon
([`ReplyPreferences`](../corridor/domain/models.py)). The pure
`ReplyService`
([`corridor/application/reply_service.py`](../corridor/application/reply_service.py))
turns preferences plus message content into a `RenderedReply` DTO; only the
adapter layer
([`corridor/adapters/api.py`](../corridor/adapters/api.py)) turns that into
an actual `discord.Embed`/`ctx.send()` call. Components V2 (`LayoutView`)
is unrelated to this — it's Discord's interactive-UI system, used only for
corridor's *settings panel*, not for the embeds a reply renders as (Discord
doesn't allow mixing V1 embeds and V2 components in the same message).

## What a dependent cog calls

Everything a generated cog needs is exposed on the loaded `Corridor` Cog
instance, fetched via `bot.get_cog("Corridor")`
(see [`corridor/adapters/cog_base.py`](../corridor/adapters/cog_base.py)):

```python
await corridor.send_reply(ctx, title="Count", description=str(snapshot.count))

if not await corridor.require_permission(ctx, "keyholder"):
    return
```

`send_reply` picks text vs. embed per the guild's stored preference and
resolves the icon; `require_permission` runs the check and sends the
decline message itself on failure, so a command just returns early on
`False`.

Both also take an optional `fields=[ReplyField(name, value, inline), ...]` —
`discord.Embed.add_field`'s shape, framework-neutral — for a reply that's
more than one title/description, e.g. a multi-field status command. In
`ReplyMode.EMBED` each becomes a real embed field; in `ReplyMode.TEXT`,
where there's no such thing as an embed field, each instead becomes an
extra `**name:** value` line. This is what lets a cog send one rich,
multi-field reply through a single `send_reply`/`render_reply` call instead
of hand-building its own `discord.Embed` (which would both duplicate
corridor's rendering and silently stop respecting `ReplyMode` the moment
someone does) — see `floorplan/adapters/admin_commands.py`'s `cmd_status`.

### `[p]` substitution and copy-pastable text

`send_reply` resolves `ctx.clean_prefix` itself and substitutes it for any
literal `[p]` in `title`/`description`/`content`/every field value — Red
only expands `[p]` in command docstrings (what `[p]help` shows), never in
reply text a cog builds by hand, so a hand-written `` `[p]foo` `` would
otherwise reach Discord unexpanded. `render_reply` requires the caller to
pass `prefix` explicitly (typically `ctx.clean_prefix`) since it has no
`ctx` of its own.

An optional `code=["[p]floorplan enable"]` (or `ReplyField(..., code=True)`
for a whole field's value) renders that string in its own fenced Discord
code block instead of inline text, after prefix substitution — giving the
client's native copy button. Keep prose describing *why* to run a command
in `description`/`content` as plain text, and pass only the exact
copy-pastable string itself via `code`: a fenced block is always
block-level, so folding it into the middle of a sentence would force ugly
line breaks around it instead of a clean button. `ReplyField(..., code=True)`
also forces that field to render non-inline in `ReplyMode.EMBED`, since a
fenced block doesn't fit a narrow inline column.

A cog that needs its own interaction-aware dispatch on top of that (an
ephemeral slash-command response, a deferred followup, ...) — something
`send_reply`'s plain `ctx.send()` doesn't support — calls the lower-level
`corridor.render_reply(guild_id, title=..., description=..., fields=...)`
instead: same `ReplyMode` rendering, returned as a `RenderedReply` DTO
instead of sent, so the caller does its own send. floorplan's `ReplyMixin`
([`floorplan/adapters/replies.py`](../floorplan/adapters/replies.py)) is
the reference example. Every command handler across corridor/floorplan/pixelagents/
toolbox is checked for this by
[`contracts/discord_replies/lint_reply_channel.py`](../contracts/discord_replies/lint_reply_channel.py)
(run in CI by `cogs-quality.yml`), which fails on any raw
`ctx.send()`/`interaction.response.send_message()`/`interaction.followup.send()`
reachable from a command handler without a `send_reply`/`render_reply` call
along the way.

### required_cogs does not auto-load the dependency

`info.json`'s `required_cogs` field is a **Downloader install hint only** —
it tells Red what to install alongside a cog, not what to load at runtime.
Unloading corridor and then loading a dependent cog would otherwise fail
outright. Every generated cog instead calls
`ensure_corridor_loaded()` from its own `dependency_loader.py` inside
`setup()` (before importing anything that touches corridor at module scope)
and again defensively in `cog_load()`, which resolves corridor through
Red's cog manager and loads it if it isn't already, raising a clear
`CogLoadError` if corridor isn't installed at all.

## Configuring it: `[p]corridorsettings`

The Components V2 settings panel
([`corridor/adapters/settings_ui.py`](../corridor/adapters/settings_ui.py))
is where a guild sets its permission groups' role and permission
assignments (add, remove, or rename a group; assign or clear its roles;
select from the curated permission list) and reply preferences. Group
management is UI-only — there is no text-command equivalent. Two ways to
reach it:

- **Standalone**: `[p]corridorsettings` — corridor's own command.
- **Mounted**: any cog's own settings command can embed the exact same
  controls via `build_shared_settings_container()`, appearing alongside
  that cog's own custom settings fields in one panel.

`[p]corridorsettings` requires Discord's "Manage Server" permission, a
Red-registered admin role (`[p]set addadminrole`), or bot ownership
(`@commands.admin_or_permissions(manage_guild=True)`
in [`corridor/adapters/commands.py`](../corridor/adapters/commands.py)). A
user without one of those gets no response and no error message — that's
Red's default behavior for a failed permission check, not a bug.

## What this is not

corridor doesn't touch Discord's own permission system (it never grants,
revokes, or checks channel overwrites) — a group's `permission_names` is an
*optional extra way to satisfy* an office-cogs-specific tier, read-only off
`member.guild_permissions`, not a mechanism for managing Discord
permissions themselves. A group with no `permission_names` configured is
still purely role-backed, exactly as before. It also isn't a
general shared-code library for UI or business logic: the earlier idea of
extracting Components V2 boilerplate into a shared package was deliberately
rejected as premature abstraction. What justified corridor specifically is
that reply style and permission tiers are genuinely guild-wide *state*, not
reusable code — every other cog-specific concern stays in that cog's own
domain/application/infrastructure layers.
