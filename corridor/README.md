# corridor

A central, shared permission system — plus shared reply-style formatting —
that every other cog in this repo depends on instead of reinventing its own.

`corridor` owns one guild-wide `Config` store for two things every cog
otherwise needs and would end up rebuilding: **who is allowed to run a
command** (admin-configurable permission tiers, resolved by Discord role
and/or Discord permission) and **how a reply gets formatted** (plain text vs.
rich embed, timestamp, footer, icon source). Configure either once per guild
and every dependent cog respects it immediately.

It's hidden (`"hidden": true` in `info.json`) because it's infrastructure,
not something a server admin interacts with directly beyond
`[p]corridorsettings` — but it is a real `COG`-type cog, auto-loaded and
running, not a `SHARED_LIBRARY` (that's [`contracts/`](../contracts/), a
different, CI-only package).

## Installing

corridor has no dependencies of its own. Other cogs in this repo (currently
`floorplan`, `pixelagents`, `toolbox`) declare it via `required_cogs` and auto-load it
through `dependency_loader.ensure_corridor_loaded()` if it isn't already
running, so you don't need to load it manually — but you can:

```
[p]load corridor
```

## Configuring it

```
[p]corridorsettings
```

Requires Discord's "Manage Server" permission, a Red-registered admin role,
or bot ownership. From the Components V2 settings panel a guild admin can:

- Add, remove, or rename permission groups, and assign Discord roles and/or
  Discord permissions to each one (roles and permissions are independent,
  OR'd criteria — a member needs to match either, not both).
- Configure reply style: text vs. embed, and if embed, timestamp/footer/icon.

Two built-in groups seed by default: **Building Manager**
(`building_manager`) and **Keyholder** (`keyholder`). Two reserved,
non-role-backed tiers always exist: **Owner** (bot owner, or a member with
guild Administrator permission — bypasses every check) and **Employee**
(everyone — never restricts).

Any cog's own settings command can also embed the same controls inline via
`build_shared_settings_container()`, rather than sending users to a separate
command.

corridor also hosts a cross-cog **LLM tool registry**: any cog can register
one of its commands as a tool `pico` (if loaded) can call directly from its
tool-calling loop, gated by the same permission groups above. See
[`docs/corridor-tool-registry-design.md`](../docs/corridor-tool-registry-design.md).

## What a dependent cog calls

```python
corridor = bot.get_cog("Corridor")

await corridor.send_reply(ctx, title="Count", description=str(snapshot.count))

if not await corridor.require_permission(ctx, "keyholder"):
    return

corridor.register_tool(my_registered_tool, owner="MyCog")  # in cog_load
corridor.unregister_tool_owner("MyCog")  # in cog_unload
```

`send_reply` renders per the guild's stored reply preference and sends it;
`require_permission` runs the permission check and sends its own decline
message on failure, so the caller just returns early on `False`;
`register_tool`/`unregister_tool_owner` add/remove an LLM-callable tool
from corridor's cross-cog registry.

## Docs

See [`docs/corridor.md`](../docs/corridor.md) for the full permission model,
reply-rendering details, and the reasoning behind sharing this as one cog
instead of per-cog settings.
