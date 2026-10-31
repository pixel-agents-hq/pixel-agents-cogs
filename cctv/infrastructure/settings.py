"""Fresh Red Config storage owned exclusively by CCTV."""

from __future__ import annotations

from math import isfinite
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from redbot.core import Config

from ..domain import GlobalSettings, GuildSettings

CONFIG_IDENTIFIER = 0x636374765F73657474696E6773  # "cctv_settings"

GLOBAL_DEFAULTS: dict[str, object] = {
    "listener_host": "127.0.0.1",
    "listener_port": 3210,
    "discord_clear_delay": 2.0,
    "editor_clear_delay": 2.0,
    "designer_url": "https://pixel-index-staging.nntin.xyz/assets/design",
    "public_base_url": None,
}
GUILD_DEFAULTS: dict[str, object] = {
    "enabled": False,
    "include_bots": True,
    "show_offline": False,
    "ghost_offline": True,
    "ghost_bots": True,
    "broadcast_rich_presence": True,
    "broadcast_messages": True,
}


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def validate_public_base_url(url: str) -> str:
    """`https://host[:port]` (or `http://` for a loopback host) with no
    path, query or fragment -- the origin the Dashboard is served from."""

    clean = url.strip().strip("<>").rstrip("/")
    parsed = urlsplit(clean)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"https", "http"}
        or not host
        or (parsed.scheme == "http" and host not in _LOOPBACK_HOSTS)
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or any(character.isspace() for character in clean)
    ):
        raise ValueError(
            "The public URL must be an https:// origin with no path, e.g. "
            "`https://dashboard.example.com` (http:// only for localhost)."
        )
    try:
        parsed.port  # noqa: B018 -- raises on an out-of-range port
    except ValueError as exc:
        raise ValueError("The public URL has an invalid port.") from exc
    return f"{parsed.scheme}://{parsed.netloc}"


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
            designer_url=cast(str, await self._config.designer_url()),
            public_base_url=cast("str | None", await self._config.public_base_url()),
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
            show_offline=cast(bool, await group.show_offline()),
            ghost_offline=cast(bool, await group.ghost_offline()),
            ghost_bots=cast(bool, await group.ghost_bots()),
            broadcast_rich_presence=cast(bool, await group.broadcast_rich_presence()),
            broadcast_messages=cast(bool, await group.broadcast_messages()),
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

    async def set_designer_url(self, url: str) -> str:
        clean = url.strip().strip("<>")
        if not clean.startswith(("https://", "http://")) or any(c.isspace() for c in clean):
            raise ValueError("The designer URL must be an http(s) link.")
        await self._config.designer_url.set(clean)
        return clean

    async def set_public_base_url(self, url: str) -> str:
        clean = validate_public_base_url(url)
        await self._config.public_base_url.set(clean)
        return clean

    async def set_guild_broadcast_rich_presence(self, guild_id: int, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Rich presence setting must be a boolean.")
        await self._config.guild_from_id(guild_id).broadcast_rich_presence.set(value)

    async def set_guild_broadcast_messages(self, guild_id: int, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Message setting must be a boolean.")
        await self._config.guild_from_id(guild_id).broadcast_messages.set(value)

    async def set_guild_enabled(self, guild_id: int, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Guild enabled setting must be a boolean.")
        await self._config.guild_from_id(guild_id).enabled.set(value)

    async def set_guild_include_bots(self, guild_id: int, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Include-bots setting must be a boolean.")
        await self._config.guild_from_id(guild_id).include_bots.set(value)

    async def set_guild_show_offline(self, guild_id: int, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Show-offline setting must be a boolean.")
        await self._config.guild_from_id(guild_id).show_offline.set(value)

    async def set_guild_ghost_offline(self, guild_id: int, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Ghost-offline setting must be a boolean.")
        await self._config.guild_from_id(guild_id).ghost_offline.set(value)

    async def set_guild_ghost_bots(self, guild_id: int, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Ghost-bots setting must be a boolean.")
        await self._config.guild_from_id(guild_id).ghost_bots.set(value)


__all__ = [
    "CONFIG_IDENTIFIER",
    "GLOBAL_DEFAULTS",
    "GUILD_DEFAULTS",
    "RedSettingsRepository",
    "validate_public_base_url",
]
