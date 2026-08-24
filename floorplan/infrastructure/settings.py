"""Typed persistence adapter for Floorplan's Red Config values."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from copy import deepcopy
from math import isfinite
from typing import Any, Protocol, TypeAlias, TypeVar, cast

from redbot.core import Config

from ..domain import GlobalSettings, GuildSettings, normalize_http_url

# Freshly rolled for this cog -- floorplan owns its own Config store,
# separate from pixelagents' (which now only keeps webview_commit_override).
# Existing pre-split installations' guild-enabled/layout/seats data stayed
# under the old "pixelagents" store and is not carried over.
CONFIG_IDENTIFIER = 8364586608
DEFAULT_PIXEL_INDEX_API_URL = "https://pixel-index-api-staging.nntin.xyz"
DEFAULT_PIXEL_INDEX_WEB_URL = "https://pixel-index.vercel.app"

# These dictionaries are the canonical registration contract. Config keys,
# scopes, and defaults must remain stable because existing installations have
# data stored under this identifier.
#
# `layout`/`seats` used to live here (one shared office for every guild).
# Issue #4 split each guild into its own independently-viewable "universe",
# so they moved to GUILD_DEFAULTS below -- the keys stay registered here,
# unwritten from now on, purely so `_migrate_legacy_global_layout_and_seats`
# can still read a pre-split installation's shared layout/seats exactly once.
GLOBAL_DEFAULTS: dict[str, object] = {
    "ws_host": "0.0.0.0",
    "ws_port": 3210,
    "message_tool_clear_delay": 2.0,
    "broadcast_rich_presence": True,
    "broadcast_messages": True,
    "layout": None,
    "seats": {},
    "pixel_index_api_url": DEFAULT_PIXEL_INDEX_API_URL,
    "pixel_index_web_url": DEFAULT_PIXEL_INDEX_WEB_URL,
    "layout_migrated_to_guild_scope": False,
    # A genuine agent (e.g. architect) has no guild scope at all -- its seat
    # assignment lives here, not in any one guild's own `seats`, since it
    # renders on every connected browser regardless of which guild's office
    # is open. See docs/office-agent-identity-design.md.
    "genuine_agent_seats": {},
}
GUILD_DEFAULTS: dict[str, object] = {
    "enabled": False,
    "include_bots": True,
    "private": False,
    "layout": None,
    "seats": {},
}

JsonObject: TypeAlias = dict[str, Any]
SeatRecords: TypeAlias = dict[str, JsonObject]
MutationResult = TypeVar("MutationResult")


class GuildReference(Protocol):
    """Minimal framework-neutral shape accepted by Red's guild accessor."""

    id: int


class RedSettingsRepository:
    """The typed boundary around the cog's existing Red Config storage."""

    def __init__(self, config: Any) -> None:
        self._config = config
        self._seat_locks: dict[int, asyncio.Lock] = {}

    @classmethod
    def create(cls, cog: object) -> RedSettingsRepository:
        """Create and register Floorplan's Config contract."""

        config = Config.get_conf(
            cog,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
            cog_name="floorplan",
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
            private=cast(bool, await guild.private()),
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

    async def guild_private(self, guild_ref: int | GuildReference) -> bool:
        _, guild = self._guild_group(guild_ref)
        return cast(bool, await guild.private())

    async def ws_host(self) -> str:
        return cast(str, await self._config.ws_host())

    async def ws_port(self) -> int:
        return cast(int, await self._config.ws_port())

    async def message_tool_clear_delay(self) -> float:
        return cast(float, await self._config.message_tool_clear_delay())

    async def broadcast_rich_presence(self) -> bool:
        return cast(bool, await self._config.broadcast_rich_presence())

    async def broadcast_messages(self) -> bool:
        return cast(bool, await self._config.broadcast_messages())

    async def pixel_index_api_url(self) -> str:
        return cast(str, await self._config.pixel_index_api_url())

    async def pixel_index_web_url(self) -> str:
        return cast(str, await self._config.pixel_index_web_url())

    async def guild_layout(self, guild_id: int) -> JsonObject | None:
        value = cast(JsonObject | None, await self._config.guild_from_id(guild_id).layout())
        return deepcopy(value)

    async def guild_seats(self, guild_id: int) -> SeatRecords:
        value = cast(SeatRecords | None, await self._config.guild_from_id(guild_id).seats())
        return deepcopy(value or {})

    async def _legacy_global_layout(self) -> JsonObject | None:
        """Read-only: the pre-issue-#4 shared layout, for one-time migration."""

        value = cast(JsonObject | None, await self._config.layout())
        return deepcopy(value)

    async def _legacy_global_seats(self) -> SeatRecords:
        """Read-only: the pre-issue-#4 shared seats, for one-time migration."""

        value = cast(SeatRecords | None, await self._config.seats())
        return deepcopy(value or {})

    async def set_ws_port(self, port: int) -> None:
        if isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        await self._config.ws_port.set(port)

    async def set_message_tool_clear_delay(self, seconds: float) -> None:
        if isinstance(seconds, bool) or not isfinite(seconds) or seconds < 0:
            raise ValueError("Delay must be 0 or greater.")
        await self._config.message_tool_clear_delay.set(float(seconds))

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

    async def set_guild_private(self, guild_id: int, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValueError("Private setting must be a boolean.")
        await self._config.guild_from_id(guild_id).private.set(value)

    async def set_guild_layout(self, guild_id: int, layout: JsonObject | None) -> None:
        await self._config.guild_from_id(guild_id).layout.set(deepcopy(layout))

    def _seat_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self._seat_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._seat_locks[guild_id] = lock
        return lock

    async def mutate_guild_seats(
        self,
        guild_id: int,
        mutation: Callable[[SeatRecords], MutationResult],
    ) -> MutationResult:
        """Atomically apply a synchronous read-modify-write seat mutation."""

        async with self._seat_lock(guild_id):
            seats = await self.guild_seats(guild_id)
            result = mutation(seats)
            await self._config.guild_from_id(guild_id).seats.set(seats)
            return result

    # Sentinel guild_id (real Discord snowflakes are always positive) --
    # the genuine-agent seat store shares the same lock machinery as
    # per-guild seats without needing a second dict of locks.
    _GENUINE_AGENT_SEAT_LOCK_KEY = 0

    async def genuine_agent_seats(self) -> SeatRecords:
        value = cast(SeatRecords | None, await self._config.genuine_agent_seats())
        return deepcopy(value or {})

    async def mutate_genuine_agent_seats(
        self,
        mutation: Callable[[SeatRecords], MutationResult],
    ) -> MutationResult:
        """Atomically apply a synchronous read-modify-write seat mutation
        for genuine agents (see GLOBAL_DEFAULTS' `genuine_agent_seats`)."""

        async with self._seat_lock(self._GENUINE_AGENT_SEAT_LOCK_KEY):
            seats = await self.genuine_agent_seats()
            result = mutation(seats)
            await self._config.genuine_agent_seats.set(seats)
            return result

    async def migrate_legacy_global_layout_and_seats(self, guild_ids: Sequence[int]) -> None:
        """One-time: seed every given guild's own layout/seats from the
        pre-issue-#4 shared global record, idempotent via a Config flag.

        Only guilds that don't already have their own layout are seeded --
        a guild that was never enabled before the split has nothing to
        inherit, and a guild already migrated (or given its own layout
        since) must never be clobbered by re-running this.
        """

        if cast(bool, await self._config.layout_migrated_to_guild_scope()):
            return
        legacy_layout = await self._legacy_global_layout()
        legacy_seats = await self._legacy_global_seats()
        if legacy_layout is not None:
            for guild_id in guild_ids:
                guild = self._config.guild_from_id(guild_id)
                if cast(JsonObject | None, await guild.layout()) is None:
                    await guild.layout.set(deepcopy(legacy_layout))
                    await guild.seats.set(deepcopy(legacy_seats))
        await self._config.layout_migrated_to_guild_scope.set(True)
