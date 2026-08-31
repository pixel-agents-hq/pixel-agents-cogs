"""Fresh Red Config storage owned exclusively by CCTV."""

from __future__ import annotations

from math import isfinite
from typing import Any, Protocol, cast

from redbot.core import Config

from ..domain import GlobalSettings, GuildSettings

CONFIG_IDENTIFIER = 0x636374765F73657474696E6773  # "cctv_settings"

GLOBAL_DEFAULTS: dict[str, object] = {
    "listener_host": "127.0.0.1",
    "listener_port": 3210,
    "discord_clear_delay": 2.0,
    "editor_clear_delay": 2.0,
    "broadcast_rich_presence": True,
    "broadcast_messages": True,
}
GUILD_DEFAULTS: dict[str, object] = {"enabled": False, "include_bots": True}


class GuildReference(Protocol):
    id: int


class RedSettingsRepository:
    def __init__(self, config: Any) -> None:
        self._config = config

    @classmethod
    def create(cls, cog: object) -> RedSettingsRepository:
        config = Config.get_conf(
            cog,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
            cog_name="cctv",
        )
        config.register_global(**GLOBAL_DEFAULTS)
        config.register_guild(**GUILD_DEFAULTS)
        return cls(config)

    @property
    def config(self) -> Any:
        return self._config

    async def global_settings(self) -> GlobalSettings:
        return GlobalSettings(
            listener_host=cast(str, await self._config.listener_host()),
            listener_port=cast(int, await self._config.listener_port()),
            discord_clear_delay=cast(float, await self._config.discord_clear_delay()),
            editor_clear_delay=cast(float, await self._config.editor_clear_delay()),
            broadcast_rich_presence=cast(bool, await self._config.broadcast_rich_presence()),
            broadcast_messages=cast(bool, await self._config.broadcast_messages()),
        )

    def _guild(self, guild: int | GuildReference) -> tuple[int, Any]:
        if isinstance(guild, int):
            return guild, self._config.guild_from_id(guild)
        return guild.id, self._config.guild(guild)

    async def guild_settings(self, guild: int | GuildReference) -> GuildSettings:
        guild_id, group = self._guild(guild)
        return GuildSettings(
            guild_id=guild_id,
            enabled=cast(bool, await group.enabled()),
            include_bots=cast(bool, await group.include_bots()),
        )

    async def guild_enabled(self, guild: int | GuildReference) -> bool:
        _, group = self._guild(guild)
        return cast(bool, await group.enabled())

    async def set_listener_host(self, host: str) -> str:
        clean = host.strip()
        if not clean or any(character.isspace() for character in clean):
            raise ValueError("Listener host must be a non-empty hostname or address.")
        await self._config.listener_host.set(clean)
        return clean

    async def set_listener_port(self, port: int) -> None:
        if isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        await self._config.listener_port.set(port)

    async def set_clear_delay(self, page: str, seconds: float) -> None:
        if page not in {"discord", "editor"}:
            raise ValueError("Unknown CCTV page.")
        if isinstance(seconds, bool) or not isfinite(seconds) or seconds < 0:
            raise ValueError("Delay must be 0 or greater.")
        await getattr(self._config, f"{page}_clear_delay").set(float(seconds))

    async def set_broadcast_rich_presence(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Rich presence setting must be a boolean.")
        await self._config.broadcast_rich_presence.set(value)

    async def set_broadcast_messages(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Message setting must be a boolean.")
        await self._config.broadcast_messages.set(value)

    async def set_guild_enabled(self, guild_id: int, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Guild enabled setting must be a boolean.")
        await self._config.guild_from_id(guild_id).enabled.set(value)

    async def set_guild_include_bots(self, guild_id: int, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Include-bots setting must be a boolean.")
        await self._config.guild_from_id(guild_id).include_bots.set(value)


__all__ = [
    "CONFIG_IDENTIFIER",
    "GLOBAL_DEFAULTS",
    "GUILD_DEFAULTS",
    "RedSettingsRepository",
]
