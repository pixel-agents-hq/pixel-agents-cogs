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

## Per-cog reply identity

`send_reply`/`render_reply` also carry a per-cog **identity** rather than
sending every reply anonymously. A dependent cog calls
`corridor.reply_sender(owner="MyCog", avatar_path=<cog_package>/assets/avatar.png)`
once (typically alongside `register_dependent`/`register_agent` in its own
`cog_load`) and gets back a bound `ReplySender`
([`corridor/adapters/reply_sender.py`](../corridor/adapters/reply_sender.py))
whose own `send_reply`/`render_reply` forward to `CogBase`'s with that
identity attached — every one of the many existing call sites keeps its
exact `title`/`description`/`content`/`fields`/`code` signature; only
*which object* they call changes. In `ReplyMode.EMBED` the owner name
always shows as the embed author, regardless of whether an avatar exists;
`avatar_path` is a conventional, git-committed path
(`<cog_package>/assets/avatar.png`) whose existence is checked fresh on
every send and, when present, attached as a real `discord.File` for the
author icon. In `ReplyMode.TEXT`, which has no embed at all, the owner
name instead prefixes the rendered content (`"**MyCog:** ..."`). Corridor
binds its own identity the same way (`owner="Corridor"`, see
`corridor/adapters/cog_base.py`'s `__init__`) — its commands are no longer
anonymous. Avatar images are currently committed for architect, corridor,
deskutils, floorplan, pico, pixelagents, testbench, and toolbox; `painter`
(a newer A2A-only cog) already passes its conventional avatar path but has
no image dropped in yet, so its replies show name-only until one is added.

`pico`'s `ConsultAgentTool` additionally passes a one-off
`FooterOverride(name, icon_url)` per call, which replaces the guild's
configured footer — for that one message only — with the identity of the
agent it just consulted (architect or painter), distinct from pico's own
author identity on the same message. See
[`docs/reply-identity-design.md`](reply-identity-design.md) for the full
design, rollout, and implementation checklist.

## Cross-cog LLM tool registry

A third thing corridor centralizes, same shape as its Pub/Sub event bus:
any cog can register a command as an LLM-callable tool — normally by
applying `@corridor.domain.llm_tool()` directly to the command's
callback and calling `corridor.register_llm_tools(self, owner=...)` from
`cog_load` — so `pico` (if loaded) can invoke it directly from its
tool-calling loop instead of a user needing to run the command by hand —
without `pico` and the registering cog ever depending on each other. See
[`docs/corridor-tool-registry-design.md`](corridor-tool-registry-design.md)
for the inferred metadata and permission behavior, full lifecycle, and the
framework-neutral (plain JSON-Schema dict, not pydantic) contract this uses.

## Cross-cog A2A agent directory

Corridor also runs **one process-wide A2A listener**
([`corridor/infrastructure/a2a_server.py`](../corridor/infrastructure/a2a_server.py) —
a relocation of architect's own former per-agent listener, generalized to
mount whatever's currently registered) instead of every LLM agent binding
a socket of its own. An agent cog calls
`corridor.register_agent(RegisteredAgent(agent_key=..., card=..., executor=...,
avatar_path=...), owner=...)` from its own `cog_load` and
`corridor.unregister_agent_owner(owner)` from `cog_unload`; corridor mounts
the agent's real `a2a-sdk` `AgentExecutor` under `/<agent_key>/` on the
shared listener, rewriting the card's `supported_interfaces[0].url` (and
`icon_url`, when an avatar path was given) to the actual, corridor-configured
host/port before storing it — the registering agent has no way to know that
address itself. Registering/unregistering an agent also publishes
`AgentPresenceChanged` on corridor's own event bus (below), so a directory
membership and an office-canvas presence stay one event, not two things a
cog must remember to keep in sync.

Three A2A agents exist today: `architect` (every structural layout
mutation) and `painter` (every color mutation on the same shared layout)
are both A2A-only — no Discord bot login, no guild scope — and reachable
as `consult_architect`/`consult_painter` tools `pico` builds fresh every
turn from `corridor.list_agents()`; `pico` is the sole A2A **coordinator**,
the one agent with a real Discord bot login, consulting the other two
rather than being consulted itself. `[p]corridor a2a host`/
`[p]corridor a2a port` (bot owner only) reconfigure and live-restart the
shared listener, re-mounting every already-registered agent. See
[`docs/agent-directory-design.md`](agent-directory-design.md) for the full
design.

## MCP tool-server bridge

Distinct from the LLM tool registry above (which exposes *Discord
commands* as tools): corridor also bridges a cog-owned **MCP tools
server** into a registered A2A agent's own tool-calling loop, so e.g.
`suggestionbox`'s `report_error`/`suggest_improvement` tools reach
architect's and painter's tool loops without either cog importing the
other or corridor hosting a second listener of its own. A providing cog
calls `corridor.register_mcp_server(RegisteredMcpServer(owner=...,
base_url=..., agent_allowed=...), owner=...)` from its own `cog_load`;
corridor connects to that server's own Streamable HTTP endpoint and caches
its tool list at registration time (not re-fetched on a schedule). An
agent's own tool loop calls `corridor.list_agent_tools_for(agent_key)`
fresh every turn to get every tool it's currently allowed to use — gated
per `agent_key` by the *registering* cog's own `agent_allowed` check, not
by corridor's Discord permission groups. See
[`docs/suggestionbox-design.md`](suggestionbox-design.md) §6 for the full
design.

## Pub/Sub event bus

Another thing corridor centralizes, same in-process-registry shape as the
tool registry/A2A directory/MCP bridge above:
`corridor.publish_event(event)` / `corridor.subscribe_event(event_type,
handler, owner=...)` / `corridor.unsubscribe_owner(owner)` dispatch a
closed set of `Agent*` dataclasses (`AgentReplied`, `AgentPresenceChanged`,
`AgentStatusChanged`, `AgentToolStarted`, `AgentHighlighted`,
`AgentUnhighlighted`) by concrete type, synchronously, with per-subscriber
exception isolation — a raising handler is logged, never propagated back
to the publisher. Corridor itself publishes presence (its own Discord
gateway listeners, plus `register_agent`/`unregister_agent_owner` for any
A2A agent above), `pico`/`architect`/`painter` publish `AgentReplied` for
their own replies and tool-use/thinking steps, and `cctv` is the current
sole subscriber, rendering the shared office canvas from whatever the bus
delivers (floorplan was the original subscriber before the office
dashboards moved into `cctv`). See
[`docs/corridor-pubsub-design.md`](corridor-pubsub-design.md) for the full
design and event catalog, generated into
[`corridor/corridor.yaml`](../corridor/corridor.yaml) by
`corridor/event_catalog.py`.

## Revisioned office state

Corridor also persists two independent, opaque-to-corridor aggregates it
calls `discord` and `editor` — each a Pixel Agents layout, avatar-seat
records, and a monotonically increasing revision — behind their own fresh
`Config` identity
([`corridor/infrastructure/office_state_repository.py`](../corridor/infrastructure/office_state_repository.py)),
unrelated to corridor's own settings `Config` and to every former
Floorplan/Architect/Pixelagents layout store. `OfficeStateService`
([`corridor/application/office_state_service.py`](../corridor/application/office_state_service.py))
makes each kind's reads/writes atomic per-kind (a per-kind `asyncio.Lock`,
not one lock shared across both) and publishes a complete
`OfficeStateChanged` after every successful mutation, once the lock is
released.

Corridor deliberately never interprets either JSON schema itself —
[`pixelagents`](../pixelagents) owns the Semantic IR domain model and is
the one facade `architect`/`painter` actually call through
(`office_state`/`set_office_layout`/`set_office_seats`, delegating straight
into corridor underneath). This is the one Config store genuinely owned by
corridor among the office-layout-adjacent cogs: pixelagents' *own* `Config`
identity holds only one unrelated value, a webview build-commit override
(see `pixelagents/infrastructure/settings.py`'s own comment on which cog
owns which slice of Config: CCTV owns browser/display settings, Floorplan
owns Pixel Index URLs, corridor owns this opaque office state). Its
generated contract is committed as
[`corridor/office_state.yaml`](../corridor/office_state.yaml).

## What a dependent cog calls

Everything a generated cog needs is exposed on the loaded `Corridor` Cog
instance, fetched via `bot.get_cog("Corridor")`
(see [`corridor/adapters/cog_base.py`](../corridor/adapters/cog_base.py)):

```python
await corridor.send_reply(ctx, title="Count", description=str(snapshot.count))

if not await corridor.require_permission(ctx, "keyholder"):
    return

corridor.register_llm_tools(self, owner="MyCog")  # in cog_load, scans self for @llm_tool commands
corridor.unregister_tool_owner("MyCog")           # in cog_unload
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

Both `send_reply` and `render_reply` resolve `ctx.clean_prefix` themselves
and substitute it for any literal `[p]` in `title`/`description`/`content`/
every field value — Red only expands `[p]` in command docstrings (what
`[p]help` shows), never in reply text a cog builds by hand, so a
hand-written `` `[p]foo` `` would otherwise reach Discord unexpanded. This
is why `render_reply` takes `ctx` rather than a bare `guild_id`: resolving
the prefix is corridor's job alone, not something every caller should have
to remember to do itself.

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
`corridor.render_reply(ctx, title=..., description=..., fields=...)`
instead: same `ReplyMode` rendering, returned as a `RenderedReply` DTO
instead of sent, so the caller does its own send. floorplan's `ReplyMixin`
([`floorplan/adapters/replies.py`](../floorplan/adapters/replies.py)) is
the reference example. Every command handler across
architect/cctv/corridor/deskutils/floorplan/painter/pico/pixelagents/
suggestionbox/testbench/toolbox is checked
for this by
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

### Bot-owner-only commands: `[p]corridor llm`/`[p]corridor a2a`/`[p]corridor status`

Separate from the per-guild settings panel above, `[p]corridor` (also in
[`corridor/adapters/commands.py`](../corridor/adapters/commands.py)) is a
bot-owner-only group configuring the process-wide infrastructure every
guild shares: `[p]corridor llm endpoint`/`key`/`model` set the one LiteLLM
connection `pico`/`architect`/`painter` all read via `corridor.llm_settings()`
(moved here from pico's former `[p]pico llm ...` group), and
`[p]corridor a2a host`/`port` set the shared A2A listener's bind
host/port, live-restarting it and re-mounting every already-registered
agent (moved here from architect's former per-agent `[p]architect a2a ...`
group — see [`docs/agent-directory-design.md`](agent-directory-design.md)).
`[p]corridor status` (any user) shows the current LLM endpoint/model, the
A2A listener's host/port and running state, and every currently registered
agent key.

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
