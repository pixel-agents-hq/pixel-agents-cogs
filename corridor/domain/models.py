"""Pure domain values. Zero framework imports -- no discord.py, no redbot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReplyMode(StrEnum):
    TEXT = "text"
    EMBED = "embed"


class IconSource(StrEnum):
    CUSTOM = "custom"
    BOT = "bot"
    SERVER = "server"


class PermissionGroup(StrEnum):
    """Tiers a command can require. MODERATOR and PRIVILEGED are independent
    (unranked) groups -- belonging to one does not imply the other. OWNER and
    ALL are not roles to assign; they're resolved from bot ownership / "no
    restriction" respectively."""

    ALL = "all"
    MODERATOR = "moderator"
    PRIVILEGED = "privileged"
    OWNER = "owner"


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
class PermissionSettings:
    moderator_role_ids: frozenset[int]
    privileged_role_ids: frozenset[int]


@dataclass(frozen=True, slots=True)
class GuildSettings:
    guild_id: int
    reply: ReplyPreferences
    permissions: PermissionSettings


@dataclass(frozen=True, slots=True)
class RenderedReply:
    """Framework-neutral description of what to send. The adapter layer turns
    this into a plain ctx.send() or a discord.Embed -- this module never
    touches discord.py types."""

    mode: ReplyMode
    content: str | None
    embed_title: str | None
    embed_description: str | None
    footer_text: str | None
    show_timestamp: bool
    icon_url: str | None


@dataclass(frozen=True, slots=True)
class MemberCapabilities:
    """What one member is allowed to do, computed once per check."""

    is_owner: bool
    is_moderator: bool
    is_privileged: bool

    def satisfies(self, group: PermissionGroup) -> bool:
        if group is PermissionGroup.ALL or self.is_owner:
            return True
        if group is PermissionGroup.MODERATOR:
            return self.is_moderator
        if group is PermissionGroup.PRIVILEGED:
            return self.is_privileged
        return False  # PermissionGroup.OWNER: only is_owner, already handled above
