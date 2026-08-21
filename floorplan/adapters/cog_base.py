"""Dependency composition and lifecycle for the Floorplan Cog."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import discord
from aiohttp import web
from redbot.core import commands
from redbot.core.bot import Red

from pixelagents.application.office import OfficeService
from pixelagents.application.presence import PresenceService

from ..application import CatalogueService, SettingsService, TaskSupervisor
from ..contracts.layout import RawOfficeLayout
from ..contracts.websocket import ClientMessage
from ..dependency_loader import ensure_corridor_loaded, ensure_pixelagents_loaded
from ..infrastructure.client_hub import ClientHub, ClientState
from ..infrastructure.pixel_index import PixelIndexClient
from ..infrastructure.settings import RedSettingsRepository
from ..infrastructure.tickets import TicketStore
from ..infrastructure.websocket import WebSocketServer
from ..infrastructure.webview import WebviewAssetProvider

log = logging.getLogger("red.d_cogs.floorplan")

# This cog's route -- injected as a `<base href>` at serve time
# (WebviewAssetProvider.base_href) so pixelagents' shared bundle resolves.
WEBVIEW_BASE_PATH = "/third-party/floorplan/static/"

# What built_base_path reads once pixelagents has built with the current
# convention; a mismatch (a stale pre-convention build) surfaces in
# `[p]floorplan status` rather than only as a 404.
EXPECTED_RELATIVE_BASE_PATH = "./"


class PixelAgentsBase:
    """Wire services once and own resources spanning the Cog lifetime --
    named for the runtime it composes, not the Cog class."""

    bot: Red
    config: Any
    _closing: bool
    _sync_task: asyncio.Task[object] | None
    _assets: dict[str, object]
    _clients: dict[web.WebSocketResponse, ClientState]

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._closing = False
        self._corridor: Any = None
        self._pixelagents: Any = None
        self._task_supervisor = TaskSupervisor(logger=log)
        self._settings_repository = RedSettingsRepository.create(self)
        self.config = self._settings_repository.config
        self._settings_service = SettingsService(
            self._settings_repository,
            clear_rich_presence=self._clear_rich_presence_bubbles,
            reauthorize_editors=self._reauthorize_editors_after_settings_change,
            sync_guild=self._sync_guild_from_settings,
            despawn_guild=self._despawn_guild_from_settings,
        )
        self._presence_service = PresenceService(self._send)
        self._office_service = OfficeService(
            self._settings_repository,
            self._send,
            presence=self._presence_service,
            logger=log,
        )
        self._pixel_index_client = PixelIndexClient(logger=log)
        self._catalogue_service = CatalogueService(
            self._settings_repository,
            self._pixel_index_client,
            can_edit_layout=self._can_edit_layout_user,
            publish_layout=self._publish_catalogue_layout,
        )
        self._sync_task = None
        # Root is a placeholder until _sync_webview_assets() resolves
        # pixelagents; base_href never changes, so it's set once here.
        self._webview_assets = WebviewAssetProvider(Path(), logger=log)
        self._webview_assets.base_href = WEBVIEW_BASE_PATH
        self._webview_built_commit: str | None = None
        self._webview_build_convention_stale = False
        self._assets = self._webview_assets.assets
        self._ticket_store = TicketStore()
        self._client_hub = ClientHub(logger=log)
        self._clients = self._client_hub.clients
        self._websocket_server = WebSocketServer(
            clients=self._client_hub,
            tickets=self._ticket_store,
            authorize=self._authorize_office_client,
            handle_application_message=self._handle_application_message,
            health_snapshot=self._health_snapshot,
            logger=log,
        )

    @property
    def _agents(self) -> dict[tuple[int, int], tuple[str, str]]:
        """Compatibility view of service-owned active agents."""

        return self._office_service.active_agents

    @_agents.setter
    def _agents(self, value: dict[tuple[int, int], tuple[str, str]]) -> None:
        self._office_service.replace_active_agents(value)

    @property
    def _presence_cache(self) -> dict[tuple[int, int], str]:
        """Compatibility view of the presence service's transition cache."""

        return self._presence_service.cache

    @_presence_cache.setter
    def _presence_cache(self, value: dict[tuple[int, int], str]) -> None:
        self._presence_service.replace_cache(value)

    @property
    def _logged_collisions(self) -> set[int]:
        return self._office_service.logged_collisions

    async def _clear_rich_presence_bubbles(self) -> None:
        await self._office_service.clear_presence()

    async def _reauthorize_editors_after_settings_change(self) -> None:
        await self._client_hub.reauthorize(self._check_auth)

    async def _sync_guild_from_settings(self, guild_id: int) -> str:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return "Sync skipped: guild is no longer available."
        return await self._full_sync(guild)

    async def _despawn_guild_from_settings(self, guild_id: int) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is not None:
            await self._despawn_guild(guild)

    async def _send_to(self, socket: web.WebSocketResponse, message: Mapping[str, object]) -> None:
        if not self._closing:
            await self._client_hub.send_to(socket, message)

    async def _send(self, message: Mapping[str, object]) -> None:
        if not self._closing:
            await self._client_hub.broadcast(message)

    def _load_assets(self) -> None:
        self._webview_assets.load_assets()

    async def _sync_webview_assets(self) -> None:
        """Refresh the built-bundle path/status from pixelagents.

        `setup()` only ensures the pixelagents *package* is importable, not
        that the Cog is loaded (see `ensure_pixelagents_importable`'s
        docstring). The real Cog instance is resolved here instead, lazily,
        on first actual use.
        """

        if self._pixelagents is None:
            self._pixelagents = await ensure_pixelagents_loaded(self.bot)
        status = self._pixelagents.webview_bundle_status()
        self._webview_assets.root = status.dist_path.resolve()
        self._webview_assets.build_status = None if status.ready else status.detail
        if status.ready and status.built_commit != self._webview_built_commit:
            await asyncio.to_thread(self._load_assets)
            self._webview_built_commit = status.built_commit
            self._check_webview_build_convention(getattr(status, "built_base_path", None))

    def _check_webview_build_convention(self, built_base_path: str | None) -> None:
        stale = bool(built_base_path) and built_base_path != EXPECTED_RELATIVE_BASE_PATH
        self._webview_build_convention_stale = stale
        if stale:
            log.warning(
                "floorplan: webview built for %s, not %s -- assets will 404. Run "
                "[p]pixelagents webview rebuild.",
                built_base_path,
                EXPECTED_RELATIVE_BASE_PATH,
            )

    def _webview_assets_status(self) -> str:
        """Short, embed-field-sized summary of webview asset health."""

        if self._webview_build_convention_stale:
            return "⚠️ built with an outdated convention — run [p]pixelagents webview rebuild"
        if self._assets.get("characters"):
            return "✅ loaded"
        return self._webview_assets.build_status or "⚠️ missing"

    async def cog_load(self) -> None:
        self._closing = False
        self._corridor = await ensure_corridor_loaded(self.bot)
        self._corridor.register_dependent("floorplan")
        self._task_supervisor.open()
        await self._pixel_index_client.start()
        await self._start_server()
        self._sync_task = self._task_supervisor.create(
            self._initial_sync(), name="floorplan-initial-sync"
        )

    async def _initial_sync(self) -> None:
        wait_until_ready: Callable[[], Awaitable[None]] = self.bot.wait_until_red_ready
        await wait_until_ready()
        await self._sync_all_guilds()

    async def cog_unload(self) -> None:
        self._closing = True
        if self._corridor is not None:
            self._corridor.unregister_dependent("floorplan")
        await self._task_supervisor.shutdown()
        self._sync_task = None
        await self._websocket_server.stop()
        await self._pixel_index_client.close()

    # Cross-adapter hooks resolved by the composed Cog's MRO.
    async def _check_auth(self, user_id: int) -> bool:
        raise NotImplementedError

    async def _authorize_office_client(self, user_id: int) -> bool:
        raise NotImplementedError

    async def _can_edit_layout_user(self, user_id: int) -> bool:
        raise NotImplementedError

    async def _publish_catalogue_layout(self, layout: RawOfficeLayout) -> None:
        raise NotImplementedError

    async def _handle_application_message(
        self, socket: web.WebSocketResponse, message: ClientMessage
    ) -> None:
        raise NotImplementedError

    def _health_snapshot(self) -> Mapping[str, object]:
        raise NotImplementedError

    async def _start_server(self) -> None:
        raise NotImplementedError

    async def _sync_all_guilds(self) -> None:
        raise NotImplementedError

    async def _full_sync(self, guild: discord.Guild) -> str:
        raise NotImplementedError

    async def _despawn_guild(self, guild: discord.Guild) -> None:
        raise NotImplementedError

    async def _reply(
        self, ctx: commands.Context, content: str | None = None, **kwargs: Any
    ) -> None:
        raise NotImplementedError

    async def _send_public(
        self, ctx: commands.Context, content: str | None = None, **kwargs: Any
    ) -> None:
        raise NotImplementedError
