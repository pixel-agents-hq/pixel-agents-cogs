# corridor: shared permissions and reply style

[`corridor/`](../corridor/) is a `SHARED_LIBRARY`-type cog (hidden, but
actually loaded and running — unlike [`contracts/`](../contracts/), which
is a consumer-driven contract *testing* package other cogs never import at
runtime). Every cog generated from [`.cookiecutter/cog-cookiecutter`](../.cookiecutter/cog-cookiecutter)
declares it as a `required_cogs` dependency and calls into it for two
things: **who is allowed to run a command**, and **how a reply gets
formatted**. Neither is reinvented per cog.

## Why a shared cog instead of per-cog settings

Before corridor, each cog would have needed its own moderator/privileged
role configuration and its own text-vs-embed reply logic — meaning a server
admin configuring "who counts as a moderator" once per cog, and every cog
maintaining its own copy of embed-building boilerplate. corridor owns one
guild-wide `Config` store instead: configure the role tiers and reply style
once, and every cog that depends on corridor respects it immediately. This
is the kind of sharing that's worth the `required_cogs` coupling — it's
guild-wide *state* that genuinely needs one source of truth, not shared UI
code wrapped around independent per-cog logic.

## The permission model

Defined in [`corridor/domain/models.py`](../corridor/domain/models.py) as
`PermissionGroup`, an `enum.StrEnum` with four values:

| Group | Who satisfies it |
|---|---|
| `ALL` | Everyone. Never restricts. |
| `MODERATOR` | Members holding one of the guild's configured moderator roles. |
| `PRIVILEGED` | Members holding one of the guild's configured privileged roles. |
| `OWNER` | The bot owner (Red's owner concept, not guild owner). |

**`MODERATOR` and `PRIVILEGED` are independent, unranked tiers** — holding
one does not imply the other. A member with only the moderator role fails a
`require_permission(PRIVILEGED)` check, and vice versa. This was a
deliberate choice over a linear hierarchy (`ALL < PRIVILEGED < MODERATOR <
OWNER`): "privileged" is meant as a distinct special-access group, not a
lesser form of moderator. The bot owner bypasses every check regardless of
group, and `ALL` never restricts.

Each tier's role set is a `frozenset[int]` of Discord role IDs — a guild can
assign more than one role per tier, and the sets are mutable at any time
(add/remove a role without redefining the whole tier). This is computed per
check via `MemberCapabilities` (`corridor/domain/models.py`), which the
pure `PermissionService`
([`corridor/application/permission_service.py`](../corridor/application/permission_service.py))
resolves from a member's role IDs and the bot's owner ID set — no discord.py
or Red imports in that resolution logic, so it's unit-tested with plain
fakes, no framework stubbing needed.

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

if not await corridor.require_permission(ctx, PermissionGroup.MODERATOR):
    return
```

`send_reply` picks text vs. embed per the guild's stored preference and
resolves the icon; `require_permission` runs the check and sends the
decline message itself on failure, so a command just returns early on
`False`.

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
is where a guild sets its moderator/privileged role sets and reply
preferences. Two ways to reach it:

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

corridor doesn't touch Discord's own permission system (role permission
bits, channel overwrites) — the moderator/privileged tiers are an
office-cogs-specific concept layered on top, deliberately decoupled from
whether a role happens to have "Manage Server" or similar. It also isn't a
general shared-code library for UI or business logic: the earlier idea of
extracting Components V2 boilerplate into a shared package was deliberately
rejected as premature abstraction. What justified corridor specifically is
that reply style and permission tiers are genuinely guild-wide *state*, not
reusable code — every other cog-specific concern stays in that cog's own
domain/application/infrastructure layers.
