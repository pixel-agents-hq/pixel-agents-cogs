# Permissions

floorplan does not own its own permission model — it delegates every
authorization check to [corridor](../corridor), which is auto-loaded on
startup (see `dependency_loader.ensure_corridor_loaded`). This document
describes what each corridor permission tier can do **from floorplan'
point of view**. For how tiers are configured, see corridor's own docs /
`[p]corridorsettings`.

## Tiers and what they grant in floorplan

| Tier | Key | Backed by | Can edit office layout? |
|------|-----|-----------|--------------------------|
| Owner | `owner` | Bot owner OR guild Administrator permission | Yes — always, bypasses every check |
| Building Manager | `building_manager` | Admin-assigned Discord role(s) | No — seeded by corridor as a default group, but floorplan does not check this key anywhere today |
| Keyholder | `keyholder` | Admin-assigned Discord role(s) | Yes — checked via `capabilities_satisfy(member, "keyholder")` |
| Employee | `employee` | No restriction — the default for anyone with no matching role | No — read-only access to the office webview |

## How the check works

`floorplan/adapters/office_gateway.py:_can_edit_layout_user` is the single
gate for layout-editing authorization (used by the office webview's editor
API):

1. If the caller is the bot owner, access is granted immediately.
2. Otherwise, for each guild where floorplan is enabled, floorplan
   resolves the caller to a `discord.Member` and asks corridor:
   `corridor.capabilities_satisfy(member, "keyholder")`.
3. Access is granted the moment any enabled guild's membership satisfies
   `keyholder` (guild Administrator permission also satisfies it, since
   `is_owner` short-circuits corridor's own check — see
   `corridor/application/permission_service.py`).
4. If no guild grants it, the caller falls back to `employee` — i.e.
   view-only access to the rendered office; no layout mutation.

## Configuring who is a Keyholder

Keyholder role assignment is entirely owned by corridor, not floorplan:

```
[p]corridorsettings
```

Guild admins (or anyone with `manage_guild`) use corridor's settings UI to
assign one or more Discord roles to the `keyholder` group. There is no
floorplan-specific permission command — changing corridor's `keyholder`
role set immediately changes who can edit the floorplan office layout.

## Notes

- Permission groups in corridor are **independent, unranked tiers** —
  belonging to `building_manager` does not imply `keyholder`, and vice
  versa. floorplan only ever asks about `keyholder`.
- `building_manager` exists because corridor seeds it as one of two default
  groups for every guild; it is reserved for future use by floorplan or
  other dependent cogs and currently has no effect here.
- Group `key`s (`keyholder`, `building_manager`) are stable identifiers
  hardcoded in dependent cogs; only the display `label` shown in Discord UI
  is admin-renameable.
