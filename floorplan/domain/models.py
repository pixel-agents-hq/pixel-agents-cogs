"""Immutable domain snapshots with no framework or transport dependencies.

Agent-visualization data (AgentKey, AgentSnapshot, PresenceStatus, etc.) lives
in `pixelagents.domain` -- pixelagents owns the webview these values drive,
and floorplan is one of several cogs expected to consume it. This module
keeps only floorplan's own settings shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

SnowflakeId: TypeAlias = int


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    """Administrator-controlled settings shared across guilds."""

    ws_host: str
    ws_port: int
    message_tool_clear_delay: float
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
    private: bool = False


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    """One consistent read of global and per-guild settings."""

    global_settings: GlobalSettings
    guilds: tuple[GuildSettings, ...] = ()

    def for_guild(self, guild_id: SnowflakeId) -> GuildSettings | None:
        """Return the settings for ``guild_id`` when present in the snapshot."""

        return next((guild for guild in self.guilds if guild.guild_id == guild_id), None)
