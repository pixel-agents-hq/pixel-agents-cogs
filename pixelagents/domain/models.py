"""Immutable domain snapshots with no framework or transport dependencies.

Discord adapters will normalize gateway objects into these values before the
application services consume them.  Keeping the data immutable prevents a
cached Discord object or mutable Config result from changing underneath a
long-running reconciliation operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

SnowflakeId: TypeAlias = int
AgentId: TypeAlias = int


class PresenceStatus(StrEnum):
    """Discord presence states that produce a visible office agent."""

    ONLINE = "online"
    IDLE = "idle"
    DO_NOT_DISTURB = "dnd"


class ActivityKind(StrEnum):
    """Normalized Discord activity categories."""

    PLAYING = "playing"
    STREAMING = "streaming"
    LISTENING = "listening"
    WATCHING = "watching"
    CUSTOM = "custom"
    COMPETING = "competing"


@dataclass(frozen=True, slots=True)
class AgentKey:
    """Identity of a member within one Discord guild."""

    guild_id: SnowflakeId
    user_id: SnowflakeId


@dataclass(frozen=True, slots=True)
class ActivitySnapshot:
    """The activity fields currently needed to build an office label."""

    kind: ActivityKind
    name: str | None = None
    details: str | None = None
    state: str | None = None
    title: str | None = None
    artist: str | None = None


@dataclass(frozen=True, slots=True)
class AgentSnapshot:
    """A Discord member normalized at the listener boundary."""

    key: AgentKey
    display_name: str
    status: PresenceStatus | None
    is_bot: bool
    activities: tuple[ActivitySnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class TrackedAgent:
    """The minimal state retained after a member enters the office."""

    key: AgentKey
    display_name: str
    status: PresenceStatus


@dataclass(frozen=True, slots=True)
class SeatAssignment:
    """Persisted visual and seating preferences for one rendered agent."""

    agent_id: AgentId
    palette: int | None = None
    hue_shift: int | None = None
    seat_id: str | None = None


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    """Administrator-controlled settings shared across guilds."""

    ws_host: str
    ws_port: int
    message_tool_clear_delay: float
    editor_role_id: SnowflakeId | None
    broadcast_rich_presence: bool
    broadcast_messages: bool
    pixel_index_api_url: str
    pixel_index_web_url: str


@dataclass(frozen=True, slots=True)
class GuildSettings:
    """Presence mirroring settings for one guild."""

    guild_id: SnowflakeId
    enabled: bool
    include_bots: bool


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    """One consistent read of global and per-guild settings."""

    global_settings: GlobalSettings
    guilds: tuple[GuildSettings, ...] = ()

    def for_guild(self, guild_id: SnowflakeId) -> GuildSettings | None:
        """Return the settings for ``guild_id`` when present in the snapshot."""

        return next((guild for guild in self.guilds if guild.guild_id == guild_id), None)
