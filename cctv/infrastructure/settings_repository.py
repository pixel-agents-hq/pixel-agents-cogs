"""Red Config-backed storage for cctv's own listener/display settings.

Office layout/seat state is NOT stored here -- it lives in corridor,
reached through pixelagents' OfficeStateFacade (docs/cctv-design.md).
This repository only ever holds cctv's own process-scoped listener
config and the Discord page's per-guild display settings, deliberately
fresh (not migrated from floorplan's/architect's now-retired Config
identifiers, per docs/cctv-design.md §2.9).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from redbot.core import Config

# Filled in by hooks/post_gen_project.py with a freshly rolled random int.
CONFIG_IDENTIFIER = 3391302402

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3210
DEFAULT_CLEAR_DELAY = 2.0

GLOBAL_DEFAULTS: dict[str, object] = {
    "host": DEFAULT_HOST,
    "port": DEFAULT_PORT,
    "discord_clear_delay": DEFAULT_CLEAR_DELAY,
    "editor_clear_delay": DEFAULT_CLEAR_DELAY,
    "broadcast_rich_presence": True,
    "broadcast_messages": True,
}
GUILD_DEFAULTS: dict[str, object] = {
    "enabled": False,
    "include_bots": True,
}


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    host: str
    port: int
    discord_clear_delay: float
    editor_clear_delay: float
    broadcast_rich_presence: bool
    broadcast_messages: bool


@dataclass(frozen=True, slots=True)
class GuildSettings:
    guild_id: int
    enabled: bool
    include_bots: bool


class RedCctvRepository:
    """The typed boundary around cctv's own Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedCctvRepository:
        config = Config.get_conf(cog, identifier=CONFIG_IDENTIFIER, force_registration=True)
        config.register_global(**GLOBAL_DEFAULTS)
        config.register_guild(**GUILD_DEFAULTS)
        return cls(config)

    @property
    def config(self) -> Any:
        return self._config

    async def global_settings(self) -> GlobalSettings:
        return GlobalSettings(
            host=cast(str, await self._config.host()),
            port=cast(int, await self._config.port()),
            discord_clear_delay=cast(float, await self._config.discord_clear_delay()),
            editor_clear_delay=cast(float, await self._config.editor_clear_delay()),
            broadcast_rich_presence=cast(bool, await self._config.broadcast_rich_presence()),
            broadcast_messages=cast(bool, await self._config.broadcast_messages()),
        )

    async def guild_settings(self, guild_id: int) -> GuildSettings:
        guild = self._config.guild_from_id(guild_id)
        return GuildSettings(
            guild_id=guild_id,
            enabled=cast(bool, await guild.enabled()),
            include_bots=cast(bool, await guild.include_bots()),
        )

    async def guild_enabled(self, guild_id: int) -> bool:
        return cast(bool, await self._config.guild_from_id(guild_id).enabled())

    async def guild_include_bots(self, guild_id: int) -> bool:
        return cast(bool, await self._config.guild_from_id(guild_id).include_bots())

    async def set_host(self, value: str) -> None:
        await self._config.host.set(value)

    async def set_port(self, value: int) -> None:
        if isinstance(value, bool) or not 1 <= value <= 65535:
            raise ValueError("Port must be an integer from 1 through 65535.")
        await self._config.port.set(value)

    async def set_discord_clear_delay(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("Delay must be 0 or greater.")
        await self._config.discord_clear_delay.set(seconds)

    async def set_editor_clear_delay(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("Delay must be 0 or greater.")
        await self._config.editor_clear_delay.set(seconds)

    async def set_broadcast_rich_presence(self, value: bool) -> None:
        await self._config.broadcast_rich_presence.set(value)

    async def set_broadcast_messages(self, value: bool) -> None:
        await self._config.broadcast_messages.set(value)

    async def set_guild_enabled(self, guild_id: int, value: bool) -> None:
        await self._config.guild_from_id(guild_id).enabled.set(value)

    async def set_guild_include_bots(self, guild_id: int, value: bool) -> None:
        await self._config.guild_from_id(guild_id).include_bots.set(value)


__all__ = ["CONFIG_IDENTIFIER", "GlobalSettings", "GuildSettings", "RedCctvRepository"]
