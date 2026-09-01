# corridor

A central provider for permissions, reply formatting, LLM/A2A infrastructure,
events, tool registries, and revisioned office-state persistence.

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

corridor has no cogs as dependencies. Every other runtime cog in this repository
declares it via `required_cogs` and resolves it at load time, so it normally
starts automatically. It can also be loaded directly:

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

corridor also hosts a cross-cog **LLM tool registry**: apply
`@corridor.domain.llm_tool()` directly to a command's callback and it
becomes a tool `pico` (if loaded) can call directly from its tool-calling
loop. The decorator can infer the tool name from the qualified Discord
command, its description from the callback docstring, parameter
descriptions from their names, and availability from the command's native
checks; every value can still be overridden explicitly. See
[`docs/corridor-tool-registry-design.md`](../docs/corridor-tool-registry-design.md).

## Revisioned office state

Corridor persists two independent opaque aggregates, `discord` and `editor`.
Each contains a Pixel Agents layout, avatar-seat records, and a monotonically
increasing revision. Corridor deliberately does not interpret either JSON
schema; [`pixelagents`](../pixelagents) provides the validated public facade.

The persistence service supports idempotent initialization, complete reads,
field-specific layout/seat updates, and atomic watch-and-snapshot registration.
Every successful field mutation preserves the other field, advances the
revision, then publishes a complete `OfficeStateChanged` after releasing the
state lock. Subscribers are awaited sequentially with exception isolation and a
five-second timeout. A failed subscriber never rolls back persisted state.

Office state has a fresh Config identity and no migration/fallback path from the
former Floorplan, Architect, or Pixelagents stores. Its generated contract is
committed as [`office_state.yaml`](office_state.yaml).

## What a dependent cog calls

```python
corridor = bot.get_cog("Corridor")

await corridor.send_reply(ctx, title="Count", description=str(snapshot.count))

if not await corridor.require_permission(ctx, "keyholder"):
    return

corridor.register_llm_tools(self, owner="MyCog")  # in cog_load, scans self for @llm_tool commands
corridor.unregister_tool_owner("MyCog")  # in cog_unload
```

`send_reply` renders per the guild's stored reply preference and sends it;
`require_permission` runs the permission check and sends its own decline
message on failure, so the caller just returns early on `False`;
`register_llm_tools`/`unregister_tool_owner` add/remove every
`@llm_tool`-decorated command on a cog from corridor's cross-cog registry
in one call.

## Docs

See [`docs/corridor.md`](../docs/corridor.md) for the full permission model,
reply-rendering details, and the reasoning behind sharing this as one cog
instead of per-cog settings.
