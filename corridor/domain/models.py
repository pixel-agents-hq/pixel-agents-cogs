"""Pure domain values. Zero framework imports -- no discord.py, no redbot."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

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
