"""Settings use cases shared by commands and interactive adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from math import isfinite
from typing import Protocol

from ..domain import GlobalSettings, GuildSettings, normalize_http_url

ClearPresenceCallback = Callable[[], Awaitable[None]]
ReauthorizeCallback = Callable[[], Awaitable[None]]
SyncGuildCallback = Callable[[int], Awaitable[str]]
DespawnGuildCallback = Callable[[int], Awaitable[None]]


class SettingsRepository(Protocol):
    """Persistence operations required by settings use cases."""

    async def global_settings(self) -> GlobalSettings: ...

    async def guild_settings(self, guild_id: int) -> GuildSettings: ...

    async def set_ws_port(self, port: int) -> None: ...

    async def set_message_tool_clear_delay(self, seconds: float) -> None: ...

    async def set_broadcast_rich_presence(self, value: bool) -> None: ...

    async def set_broadcast_messages(self, value: bool) -> None: ...

    async def set_guild_enabled(self, guild_id: int, value: bool) -> None: ...

    async def set_guild_include_bots(self, guild_id: int, value: bool) -> None: ...

    async def set_pixel_index_api_url(self, value: str) -> str: ...

    async def set_pixel_index_web_url(self, value: str) -> str: ...


class SettingsService:
    """Validate setting changes and coordinate their required runtime effects."""

    def __init__(
        self,
        repository: SettingsRepository,
        *,
        clear_rich_presence: ClearPresenceCallback,
        reauthorize_editors: ReauthorizeCallback,
        sync_guild: SyncGuildCallback,
        despawn_guild: DespawnGuildCallback,
    ) -> None:
        self._repository = repository
        self._clear_rich_presence = clear_rich_presence
        self._reauthorize_editors = reauthorize_editors
        self._sync_guild = sync_guild
        self._despawn_guild = despawn_guild

    async def global_settings(self) -> GlobalSettings:
        return await self._repository.global_settings()

    async def guild_settings(self, guild_id: int) -> GuildSettings:
        return await self._repository.guild_settings(guild_id)

    async def set_ws_port(self, port: int) -> None:
        if isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        await self._repository.set_ws_port(port)

    async def set_message_tool_clear_delay(self, seconds: float) -> None:
        if isinstance(seconds, bool) or not isfinite(seconds) or seconds < 0:
            raise ValueError("Delay must be 0 or greater.")
        await self._repository.set_message_tool_clear_delay(seconds)

    async def set_broadcast_rich_presence(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Rich presence setting must be a boolean.")
        await self._repository.set_broadcast_rich_presence(value)
        if not value:
            await self._clear_rich_presence()

    async def set_broadcast_messages(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Message broadcasting setting must be a boolean.")
        await self._repository.set_broadcast_messages(value)

    async def enable_guild(self, guild_id: int) -> str:
        await self._repository.set_guild_enabled(guild_id, True)
        await self._reauthorize_editors()
        return await self._sync_guild(guild_id)

    async def disable_guild(self, guild_id: int) -> None:
        await self._repository.set_guild_enabled(guild_id, False)
        await self._reauthorize_editors()
        await self._despawn_guild(guild_id)

    async def set_include_bots(self, guild_id: int, value: bool) -> str | None:
        if not isinstance(value, bool):
            raise ValueError("Include bots setting must be a boolean.")
        await self._repository.set_guild_include_bots(guild_id, value)
        settings = await self._repository.guild_settings(guild_id)
        if settings.enabled:
            return await self._sync_guild(guild_id)
        return None

    async def set_pixel_index_api_url(self, value: str) -> str:
        clean = normalize_http_url(value)
        return await self._repository.set_pixel_index_api_url(clean)

    async def set_pixel_index_web_url(self, value: str) -> str:
        clean = normalize_http_url(value)
        return await self._repository.set_pixel_index_web_url(clean)
