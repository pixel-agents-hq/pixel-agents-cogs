"""Pure domain values. Zero framework imports -- no discord.py, no redbot."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

OWNER_KEY = "owner"
EMPLOYEE_KEY = "employee"
RESERVED_GROUP_KEYS = frozenset({OWNER_KEY, EMPLOYEE_KEY})


class ReplyMode(StrEnum):
    TEXT = "text"
    EMBED = "embed"


class IconSource(StrEnum):
    CUSTOM = "custom"
    BOT = "bot"
    SERVER = "server"


@dataclass(frozen=True, slots=True)
class IconPreference:
    source: IconSource
    custom_url: str | None = None


@dataclass(frozen=True, slots=True)
class ReplyPreferences:
    mode: ReplyMode
    show_timestamp: bool
    footer_text: str | None
    icon: IconPreference


@dataclass(frozen=True, slots=True)
class PermissionGroupDef:
    """One admin-configurable permission tier, satisfied by role membership
    and/or a Discord permission -- whichever a member has either counts.

    `key` is the stable identifier dependent cogs reference in code (e.g.
    floorplan hardcodes "keyholder") and never changes once created --
    admins may only rename `label`, the display name shown in UI/messages.

    `permission_names` holds `discord.Permissions` flag names (e.g.
    "kick_members") as plain strings -- this module has zero discord.py
    imports by design, so a raw int bitmask or discord.Permissions object
    would leak a framework type into the domain layer. Translation to/from
    real discord.Permissions happens only at the adapter boundary, same
    pattern ReplyMode/IconSource already follow.
    """

    key: str
    label: str
    role_ids: frozenset[int] = field(default_factory=frozenset)
    permission_names: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class PermissionSettings:
    """Per-guild permission configuration.

    `groups` is an open, admin-managed list of role-backed tiers -- it seeds
    with two defaults (key="building_manager", key="keyholder") but a guild
    admin may add, remove, or rename further groups. `owner_label` and
    `employee_label` are the admin-editable display names for the two
    reserved, non-role-backed tiers (OWNER_KEY / EMPLOYEE_KEY): OWNER
    resolves from bot ownership or guild Administrator permission, EMPLOYEE
    means "no restriction" -- neither is backed by a role-id set.
    """

    groups: tuple[PermissionGroupDef, ...] = ()
    owner_label: str = "Owner"
    employee_label: str = "Employee"

    def group(self, key: str) -> PermissionGroupDef | None:
        return next((g for g in self.groups if g.key == key), None)


@dataclass(frozen=True, slots=True)
class GuildSettings:
    guild_id: int
    reply: ReplyPreferences
    permissions: PermissionSettings


@dataclass(frozen=True, slots=True)
class ReplyField:
    """One embed field -- discord.Embed.add_field's name/value/inline,
    framework-neutral. In ReplyMode.TEXT there's no such thing as an embed
    field, so ReplyService.render flattens each into an extra text line
    instead of dropping it -- see that method for the exact format.

    `code=True` marks `value` as copy-pastable command/config text --
    ReplyService.render fences it in a Discord code block (giving the
    client's native copy button) instead of rendering it as plain text, and
    forces `inline=False` for the rendered field since a fenced block can't
    usefully share a row with other fields."""

    name: str
    value: str
    inline: bool = True
    code: bool = False


@dataclass(frozen=True, slots=True)
class RenderedReply:
    """Framework-neutral description of what to send. The adapter layer turns
    this into a plain ctx.send() or a discord.Embed -- this module never
    touches discord.py types."""

    mode: ReplyMode
    content: str | None
    embed_title: str | None
    embed_description: str | None
    fields: tuple[ReplyField, ...]
    footer_text: str | None
    show_timestamp: bool
    icon_url: str | None


@dataclass(frozen=True, slots=True)
class MemberCapabilities:
    """What one member is allowed to do, computed once per check.

    Permission groups are independent (unranked) tiers -- belonging to one
    role-backed group does not imply another. `is_owner` (bot owner OR guild
    Administrator permission) bypasses every check.
    """

    is_owner: bool
    satisfied_keys: frozenset[str] = frozenset()

    def satisfies(self, key: str) -> bool:
        if self.is_owner:
            return True
        if key == EMPLOYEE_KEY:
            return True
        if key == OWNER_KEY:
            return False  # only is_owner satisfies OWNER_KEY, already handled above
        return key in self.satisfied_keys


# --- corridor's Pub/Sub domain model -----------------------------------
#
# A closed set of Discord-vocabulary events, published/subscribed through
# EventBusService (corridor/application/event_bus_service.py). Deliberately
# never mirrors pixel-agents' own wire vocabulary (agentToolStart,
# ServerMessage, isExternal, ...) -- translating one of these into the
# webview's actual protocol is entirely a subscriber's job (floorplan
# today), never something this module encodes. See
# docs/corridor-pubsub-design.md for the full design rationale.


@dataclass(frozen=True, slots=True)
class AgentRef:
    """A Discord member represented as a webview agent. Deliberately holds
    the *raw* Discord user ID, not floorplan's derived, JS-safe negative
    agent ID -- that derivation (`_discord_id_to_agent_id`) stays
    floorplan's own concern, the only place that currently needs it."""

    discord_user_id: int
    guild_id: int
    is_bot: bool


@dataclass(frozen=True, slots=True)
class AgentReplied:
    """Named for corridor's own verb (`send_reply`), not a generic "spoke".

    Originally scoped to "fires exactly when a publisher sends a reply
    through corridor, nothing broader" -- widened once floorplan's own raw
    Discord message mirroring became a second publisher alongside any cog's
    `send_reply` call: both a cog routing a reply through corridor *and*
    floorplan noticing a tracked member's own raw Discord message publish
    this same event. `summary` always carries the full, untruncated text --
    wording/truncation for the wire is the subscriber's job, never the
    publisher's."""

    agent: AgentRef
    summary: str


@dataclass(frozen=True, slots=True)
class AgentToolStarted:
    agent: AgentRef
    tool_id: str
    status: str  # required on the wire -- human-readable label
    tool_name: str | None = None  # optional on the wire


@dataclass(frozen=True, slots=True)
class AgentStatusChanged:
    """Pixel-agents' own CLI-shaped active/waiting concept -- NOT to be
    confused with `AgentPresenceChanged.status` below, which is Discord's
    own online/idle/dnd/offline concept. Two unrelated status vocabularies
    that happen to share a common English word; kept as deliberately
    different field types (this one a two-value Literal) so they can never
    be confused for one another in code."""

    agent: AgentRef
    status: Literal["active", "waiting"]  # matches AgentActivityStatus exactly
    awaiting_input: bool | None = None  # matches AgentStatus.awaitingInput


@dataclass(frozen=True, slots=True)
class AgentHighlighted:
    """-> agentSelected(id). Named for what it does in Discord vocabulary
    (draw attention to this member's activity), not for the CLI-editor
    action (`agentSelected`'s own wire name) that happens to share the
    mechanism."""

    agent: AgentRef


@dataclass(frozen=True, slots=True)
class AgentUnhighlighted:
    """-> agentDeselected(id). Only takes effect if this agent is still
    the highlighted one -- matches agentDeselected's own no-op-if-stale
    semantics on the wire, so publishing this after a newer AgentHighlighted
    already moved the highlight elsewhere is always safe."""

    agent: AgentRef


@dataclass(frozen=True, slots=True)
class AgentActivity:
    """One Discord rich-presence activity (Spotify, a game, ...), in
    corridor's own vocabulary -- shaped after pixelagents.domain's
    ActivitySnapshot, but corridor must not import pixelagents types, so
    this is a parallel, hand-kept-in-sync copy. Includes `name`:
    floorplan's PresenceService.label() falls back to `activity.name` for
    every non-LISTENING kind (playing/watching/competing/streaming) --
    omitting it here would silently drop those rich-presence bubbles."""

    kind: str
    name: str | None = None
    title: str | None = None
    artist: str | None = None
    details: str | None = None
    state: str | None = None


@dataclass(frozen=True, slots=True)
class AgentPresenceChanged:
    """A full presence snapshot for one Discord member -- enough for a
    subscriber to reconstruct pixelagents.domain.AgentSnapshot and drive
    OfficeService.reconcile()/spawn()/close() unchanged. One rich event per
    presence change, not four granular ones (join/leave/status/activity):
    every one of those needs the same full-snapshot reconstruction to call
    reconcile() anyway, so a granular split would only add event types
    without adding information.

    `status="offline"` covers both a real Discord offline/invisible status
    AND a member actually leaving the guild -- there is no separate "member
    left" event; a member-remove publishes this same shape with
    `status="offline"`. The subscriber maps that to
    `AgentSnapshot(status=None, ...)` before calling `reconcile()`, matching
    that method's own existing `folder is None` branch."""

    agent: AgentRef
    display_name: str
    status: Literal["online", "idle", "dnd", "offline"]
    activities: tuple[AgentActivity, ...] = ()


# --- corridor's cross-cog LLM tool registry -----------------------------
#
# A framework-neutral extension point: any cog can register()/list_tools()
# through ToolRegistryService (corridor/application/tool_registry_service.py)
# without either side needing to know the other exists. See
# docs/corridor-tool-registry-design.md for the full rationale -- in short,
# this is the same "optional cross-cog capability, silent no-op with zero
# consumers" shape as the Pub/Sub bus above, applied to LLM tool-calling
# (pico) instead of Discord-vocabulary events.

ToolHandler = Callable[[object, Mapping[str, object]], Awaitable[Mapping[str, object]]]
ToolAvailabilityCheck = Callable[[object], Awaitable[bool]]

# An additional, externally-installed gate `list_tools_for` evaluates for
# every tool alongside `required_group`/`availability_check` -- see
# CogBase.register_tool_visibility_filter and
# docs/toolbox-command-tool-toggle-design.md. `None`/an installed filter
# raising is treated as "omit this tool", never as "allow it".
ToolVisibilityFilter = Callable[[object, "RegisteredTool"], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """One LLM-callable tool another cog has registered into corridor's
    tool registry. Deliberately framework-neutral, like every other type in
    this module: `parameters` is a plain OpenAI-style JSON Schema dict (no
    pydantic dependency required just to register one), and `handler` takes
    an opaque per-invocation context (`ctx: object` -- typically the
    triggering Discord `commands.Context`, untyped here so this module
    never imports discord.py) plus a plain JSON-object-shaped Mapping of
    arguments, and returns one. Most registrations come from
    `@corridor.domain.llm_tool`-decorated commands (see
    `corridor/adapters/llm_tool_registration.py`), whose handler invokes
    the real command callback with that same `ctx` and forwards a mapping
    it returns (or supplies a status acknowledgement when it returns
    `None`) -- `register_tool` itself stays the lower-level primitive for a
    tool that isn't a Discord command at all. A consumer that wants richer
    typing (pico's ToolLoopService, which is pydantic-based internally)
    adapts this shape itself at its own boundary -- see
    pico/tools/cross_cog.py -- rather than this registry picking a
    framework for every registrant.

    `required_group` gates which invoking member's LLM call is even offered
    this tool, using corridor's own permission-group vocabulary
    (`PermissionGroupDef.key` / `EMPLOYEE_KEY` / ...) -- resolved the same
    way `require_permission` resolves it for a Discord command. `None`
    means no group gate. `availability_check`, when present, receives the opaque
    invocation context and adds a second gate. Decorated Discord commands
    use it to delegate to the command's native check pipeline when no
    explicit `required_group` was supplied.

    Not hashable in practice (`parameters`/`handler` aren't) -- store and
    look these up by `name`, never in a set or as a dict key.
    """

    name: str
    description: str
    parameters: Mapping[str, object]
    handler: ToolHandler
    required_group: str | None = None
    availability_check: ToolAvailabilityCheck | None = None


# The closed set of classes a cog actually publish()es/subscribe()s to.
# AgentRef and AgentActivity are value objects referenced *by* these events'
# fields, never published/subscribed to on their own -- deliberately absent
# from this union. Named "...Event" (not "AgentActivity", which an earlier
# draft of this design used for this same union) to avoid colliding with
# the AgentActivity value object above.
AgentActivityEvent = (
    AgentReplied
    | AgentToolStarted
    | AgentStatusChanged
    | AgentHighlighted
    | AgentUnhighlighted
    | AgentPresenceChanged
)
