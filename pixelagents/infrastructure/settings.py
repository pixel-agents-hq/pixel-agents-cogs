"""Typed persistence adapter for Pixel Agents' Red Config values."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from typing import Any, Protocol, TypeAlias, TypeVar, cast
from urllib.parse import urlparse

from redbot.core import Config

from ..domain import GlobalSettings, GuildSettings

CONFIG_IDENTIFIER = 0x706978656C61
DEFAULT_PIXEL_INDEX_API_URL = "https://pixel-index-api-staging.nntin.xyz"
DEFAULT_PIXEL_INDEX_WEB_URL = "https://pixel-index.vercel.app"

# These dictionaries are the canonical registration contract. Config keys,
# scopes, and defaults must remain stable because existing installations have
# data stored under this identifier.
GLOBAL_DEFAULTS: dict[str, object] = {
    "ws_host": "0.0.0.0",
    "ws_port": 3210,
    "message_tool_clear_delay": 2.0,
    "editor_role_id": None,
    "broadcast_rich_presence": True,
    "broadcast_messages": True,
    "layout": None,
    "seats": {},
    "pixel_index_api_url": DEFAULT_PIXEL_INDEX_API_URL,
    "pixel_index_web_url": DEFAULT_PIXEL_INDEX_WEB_URL,
}
GUILD_DEFAULTS: dict[str, object] = {
    "enabled": False,
    "include_bots": True,
}

JsonObject: TypeAlias = dict[str, Any]
SeatRecords: TypeAlias = dict[str, JsonObject]
MutationResult = TypeVar("MutationResult")


class GuildReference(Protocol):
    """Minimal framework-neutral shape accepted by Red's guild accessor."""

    id: int


def normalize_http_url(value: str) -> str:
    """Normalize and validate an HTTP(S) base URL."""

    clean = value.strip().rstrip("/")
    parsed = urlparse(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be an absolute HTTP or HTTPS URL.")
    return clean


class RedSettingsRepository:
    """The typed boundary around the cog's existing Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config
        self._seat_lock = asyncio.Lock()

    @classmethod
    def create(cls, cog: object) -> RedSettingsRepository:
        """Create and register the unchanged Pixel Agents Config contract."""

        config = Config.get_conf(
            cog,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
        )
        config.register_global(**GLOBAL_DEFAULTS)
        config.register_guild(**GUILD_DEFAULTS)
        return cls(config)

    @property
    def config(self) -> Any:
        """Expose the raw Config object for the legacy cog compatibility surface."""

        return self._config

    async def global_settings(self) -> GlobalSettings:
        """Read an immutable snapshot of all administrator-facing global settings."""

        return GlobalSettings(
            ws_host=cast(str, await self._config.ws_host()),
            ws_port=cast(int, await self._config.ws_port()),
            message_tool_clear_delay=cast(
                float,
                await self._config.message_tool_clear_delay(),
            ),
            editor_role_id=cast(int | None, await self._config.editor_role_id()),
            broadcast_rich_presence=cast(
                bool,
                await self._config.broadcast_rich_presence(),
            ),
            broadcast_messages=cast(bool, await self._config.broadcast_messages()),
            pixel_index_api_url=cast(str, await self._config.pixel_index_api_url()),
            pixel_index_web_url=cast(str, await self._config.pixel_index_web_url()),
        )

    async def guild_settings(self, guild_ref: int | GuildReference) -> GuildSettings:
        """Read an immutable settings snapshot for one guild."""

        guild_id, guild = self._guild_group(guild_ref)
        return GuildSettings(
            guild_id=guild_id,
            enabled=cast(bool, await guild.enabled()),
            include_bots=cast(bool, await guild.include_bots()),
        )

    def _guild_group(self, guild_ref: int | GuildReference) -> tuple[int, Any]:
        if isinstance(guild_ref, int):
            guild_id = guild_ref
            guild = self._config.guild_from_id(guild_id)
        else:
            guild_id = guild_ref.id
            guild = self._config.guild(guild_ref)
        return guild_id, guild

    async def guild_enabled(self, guild_ref: int | GuildReference) -> bool:
        _, guild = self._guild_group(guild_ref)
        return cast(bool, await guild.enabled())

    async def guild_include_bots(self, guild_ref: int | GuildReference) -> bool:
        _, guild = self._guild_group(guild_ref)
        return cast(bool, await guild.include_bots())

    async def ws_host(self) -> str:
        return cast(str, await self._config.ws_host())

    async def ws_port(self) -> int:
        return cast(int, await self._config.ws_port())

    async def message_tool_clear_delay(self) -> float:
        return cast(float, await self._config.message_tool_clear_delay())

    async def editor_role_id(self) -> int | None:
        return cast(int | None, await self._config.editor_role_id())

    async def broadcast_rich_presence(self) -> bool:
        return cast(bool, await self._config.broadcast_rich_presence())

    async def broadcast_messages(self) -> bool:
        return cast(bool, await self._config.broadcast_messages())

    async def pixel_index_api_url(self) -> str:
        return cast(str, await self._config.pixel_index_api_url())

    async def pixel_index_web_url(self) -> str:
        return cast(str, await self._config.pixel_index_web_url())

    async def layout(self) -> JsonObject | None:
        value = cast(JsonObject | None, await self._config.layout())
        return deepcopy(value)

    async def seats(self) -> SeatRecords:
        value = cast(SeatRecords | None, await self._config.seats())
        return deepcopy(value or {})

    async def set_ws_port(self, port: int) -> None:
        if isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        await self._config.ws_port.set(port)

    async def set_message_tool_clear_delay(self, seconds: float) -> None:
        if isinstance(seconds, bool) or seconds < 0:
            raise ValueError("Delay must be 0 or greater.")
        await self._config.message_tool_clear_delay.set(float(seconds))

    async def set_editor_role_id(self, role_id: int | None) -> None:
        if role_id is not None and (isinstance(role_id, bool) or role_id <= 0):
            raise ValueError("Role ID must be a positive integer or None.")
        await self._config.editor_role_id.set(role_id)

    async def set_broadcast_rich_presence(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Rich presence setting must be a boolean.")
        await self._config.broadcast_rich_presence.set(value)

    async def set_broadcast_messages(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Message broadcasting setting must be a boolean.")
        await self._config.broadcast_messages.set(value)

    async def set_pixel_index_api_url(self, value: str) -> str:
        clean = normalize_http_url(value)
        await self._config.pixel_index_api_url.set(clean)
        return clean

    async def set_pixel_index_web_url(self, value: str) -> str:
        clean = normalize_http_url(value)
        await self._config.pixel_index_web_url.set(clean)
        return clean

    async def set_guild_enabled(self, guild_id: int, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Guild enabled setting must be a boolean.")
        await self._config.guild_from_id(guild_id).enabled.set(value)

    async def set_guild_include_bots(self, guild_id: int, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Include bots setting must be a boolean.")
        await self._config.guild_from_id(guild_id).include_bots.set(value)

    async def set_layout(self, layout: JsonObject | None) -> None:
        await self._config.layout.set(deepcopy(layout))

    async def mutate_seats(
        self,
        mutation: Callable[[SeatRecords], MutationResult],
    ) -> MutationResult:
        """Atomically apply a synchronous read-modify-write seat mutation."""

        async with self._seat_lock:
            seats = await self.seats()
            result = mutation(seats)
            await self._config.seats.set(seats)
            return result
