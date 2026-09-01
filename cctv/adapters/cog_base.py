"""Dependency composition and lifecycle for the Cctv Cog.

cctv is the only dashboard-hosting cog (docs/cctv-design.md) -- it runs
one aiohttp listener with two fully independent pipelines (`discord`,
`editor`), each with its own `ClientHub`/`OfficeService`, sharing only
the listener itself and the static asset provider. All office-state
reads/writes go through `pixelagents.office_state()` (never a private
Config store of cctv's own) -- see docs/cctv-design.md §2.6.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine, Mapping
from pathlib import Path
from typing import Any, cast

from redbot.core.bot import Red

from pixelagents.application.office import OfficeService, SeatRecords, SeatRepository
from pixelagents.application.presence import PresenceService
from pixelagents.infrastructure.furniture_styles import FurnitureStyleLoader

from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure import (
    Pipeline,
    RedCctvRepository,
    TicketStore,
    WebSocketServer,
    WebviewAssetProvider,
)
from ..infrastructure.client_hub import ClientHub

log = logging.getLogger("red.cctv")

# Injected as `<base href>` at serve time -- one shared static route both
# Dashboard pages resolve their assets against (docs/cctv-design.md §2.7).
WEBVIEW_BASE_PATH = "/third-party/cctv/static/"


class _LazyPixelAgents:
    """Same lazy-lookup shape architect's own `CorridorLLMClient`/
    `_LazyPixelAgents` already use -- constructed in `__init__`, but
    `pixelagents` isn't resolved until `cog_load()`."""

    def __init__(self, pixelagents_ref: Any) -> None:
        self._pixelagents_ref = pixelagents_ref

    def furniture_style_manifest(self) -> dict[str, Any] | None:
        return cast("dict[str, Any] | None", self._pixelagents_ref().furniture_style_manifest())

    def webview_bundle_status(self) -> Any:
        return self._pixelagents_ref().webview_bundle_status()


class _LazySeatRepository:
    """A `SeatRepository` bound to one office-state kind, deferring to
    pixelagents' facade at call time -- `pixelagents` isn't resolved
    until `cog_load()`, but both `OfficeService` instances are
    constructed in `__init__` (cog_load can legitimately run more than
    once per instance; constructing services fresh each time would grow
    duplicate state)."""

    def __init__(self, pixelagents_ref: Any, kind: str) -> None:
        self._pixelagents_ref = pixelagents_ref
        self._kind = kind

    async def seats(self) -> SeatRecords:
        result = await self._pixelagents_ref().office_state().seat_repository(self._kind).seats()
        return cast("SeatRecords", result)

    async def mutate_seats(self, mutation: Any) -> Any:
        repository = self._pixelagents_ref().office_state().seat_repository(self._kind)
        return await repository.mutate_seats(mutation)


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._repository = RedCctvRepository.create(self)
        self.config = self._repository.config
        self._corridor: Any = None
        self._pixelagents: Any = None
        self._reply: Any = None

        self._style_loader = FurnitureStyleLoader(_LazyPixelAgents(lambda: self._pixelagents))

        # Root is a placeholder until _sync_webview_assets() resolves
        # pixelagents; base_href never changes, so it's set once here.
        self._webview_assets = WebviewAssetProvider(Path(), logger=log)
        self._webview_built_commit: str | None = None
        self._webview_build_convention_stale = False

        self._discord_client_hub = ClientHub(logger=log)
        self._editor_client_hub = ClientHub(logger=log)
        self._tickets = TicketStore()

        discord_seats: SeatRepository = _LazySeatRepository(lambda: self._pixelagents, "discord")
        editor_seats: SeatRepository = _LazySeatRepository(lambda: self._pixelagents, "editor")
        self._discord_office_service = OfficeService(
            discord_seats,
            self._send_discord,
            presence=PresenceService(self._send_discord),
            logger=log,
        )
        # No PresenceService for the editor pipeline -- it never renders
        # real Discord rich-presence activities, only genuine-agent
        # tool/reply activity (event_subscriptions_editor.py), the same
        # shape architect's own former OfficeService construction used.
        self._editor_office_service = OfficeService(editor_seats, self._send_editor, logger=log)

        # Serializes each pipeline's own bootstrap (one connecting socket)
        # against its own live OfficeStateChanged delivery (broadcast to
        # every socket) so the two can never interleave on the wire and
        # leave a client displaying a stale snapshot after a newer one
        # already arrived (docs/cctv-design.md §3.3/§3.4). Paired with a
        # last-applied revision so either side drops an update that isn't
        # strictly newer than what this pipeline already applied.
        self._discord_state_lock = asyncio.Lock()
        self._discord_last_revision = -1
        self._editor_state_lock = asyncio.Lock()
        self._editor_last_revision = -1

        self._background_tasks: set[asyncio.Task[object]] = set()
        self._websocket_server = WebSocketServer(
            discord=Pipeline(
                path="/cctv/discord/ws",
                clients=self._discord_client_hub,
                on_webview_ready=self._on_webview_ready_discord,
                on_save_layout=self._on_save_layout_discord,
                on_save_seats=self._on_save_seats_discord,
                tickets=self._tickets,
                authorize=self._authorize_discord_client,
            ),
            editor=Pipeline(
                path="/cctv/editor/ws",
                clients=self._editor_client_hub,
                on_webview_ready=self._on_webview_ready_editor,
                on_save_layout=self._on_save_layout_editor,
                on_save_seats=self._on_save_seats_editor,
            ),
            health_snapshot=self._health_snapshot,
            logger=log,
        )

    async def _send_discord(self, message: Mapping[str, object]) -> None:
        await self._discord_client_hub.broadcast(message)

    async def _send_editor(self, message: Mapping[str, object]) -> None:
        await self._editor_client_hub.broadcast(message)

    def _health_snapshot(self) -> dict[str, object]:
        return {
            "status": "ok",
            "discord_clients": self._discord_client_hub.client_count,
            "discord_editors": self._discord_client_hub.editor_count,
            "editor_clients": self._editor_client_hub.client_count,
            "assets": sorted(self._webview_assets.assets),
        }

    def _load_assets(self) -> None:
        self._webview_assets.load_assets()

    async def _sync_webview_assets(self) -> None:
        """Refresh the built-bundle path/status from pixelagents. Same
        shape floorplan's/architect's own former `_sync_webview_assets`
        used, minus per-cog layout seeding -- that now happens lazily
        inside `OfficeStateFacade` itself the first time either aggregate
        is actually touched (docs/cctv-design.md)."""

        status = self._pixelagents.webview_bundle_status()
        self._webview_assets.root = status.dist_path.resolve()
        self._webview_assets.build_status = None if status.ready else status.detail
        if status.ready and status.built_commit != self._webview_built_commit:
            await asyncio.to_thread(self._load_assets)
            self._webview_built_commit = status.built_commit
            self._check_webview_build_convention(getattr(status, "built_base_path", None))

    def _check_webview_build_convention(self, built_base_path: str | None) -> None:
        stale = bool(built_base_path) and built_base_path != "./"
        self._webview_build_convention_stale = stale
        if stale:
            log.warning(
                "cctv: webview built for %s, not ./ -- assets will 404. Run "
                "[p]pixelagents webview rebuild.",
                built_base_path,
            )

    def _webview_assets_status(self) -> str:
        """Short, embed-field-sized summary of webview asset health."""

        if self._webview_build_convention_stale:
            return "⚠️ built with an outdated convention — run [p]pixelagents webview rebuild"
        if self._webview_assets.assets.get("characters"):
            return "✅ loaded"
        return self._webview_assets.build_status or "⚠️ missing"

    async def _start_server(self) -> bool:
        settings = await self._repository.global_settings()
        return await self._websocket_server.start(settings.host, settings.port)

    async def _notify_owners_ws_failed(self) -> None:
        """Best-effort DM -- must never raise (docs/cctv-design.md §2.11:
        a bind failure keeps cctv loaded, never fails cog_load)."""

        message = (
            "⚠️ cctv's office server failed to bind. Both dashboard pages will show a "
            "connection error until this is fixed -- check `[p]cctv status` and try "
            "`[p]cctv host`/`[p]cctv port` once the issue is resolved."
        )
        try:
            await self.bot.send_to_owners(message)
        except Exception:
            log.exception("cctv: could not notify owners about the office server bind failure")

    async def _notify_owners_dashboard_missing_if_unloaded(self) -> None:
        """Deliberately a one-shot check at `cog_load` time -- dashboard
        loading *after* cctv is already handled by
        `DashboardMixin.on_dashboard_cog_add`. Must never raise."""

        from .dashboard import dashboard_cog_loaded, dashboard_not_loaded_notification

        if dashboard_cog_loaded(self.bot):
            return
        try:
            await self.bot.send_to_owners(dashboard_not_loaded_notification())
        except Exception:
            log.exception("cctv: could not notify owners about the missing dashboard cog")

    async def cog_load(self) -> None:
        """`required_cogs` in info.json is only a Downloader install hint
        -- Red does not auto-load a dependency at runtime just because
        it's declared there, so `ensure_corridor_loaded`/`ensure_loaded`
        pull corridor/pixelagents back in if either was unloaded
        independently."""

        from corridor.dependency_loader import ensure_loaded

        self._corridor = await ensure_corridor_loaded(self.bot)
        self._corridor.register_dependent("cctv")
        self._reply = self._corridor.reply_sender(owner="Cctv")
        self._pixelagents = await ensure_loaded(self.bot, "pixelagents", "PixelAgents")
        await self._notify_owners_dashboard_missing_if_unloaded()
        await self._sync_webview_assets()
        if not await self._start_server():
            await self._notify_owners_ws_failed()

    async def cog_unload(self) -> None:
        await self._websocket_server.stop()
        tasks = tuple(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._corridor is not None:
            # Belt-and-suspenders with corridor's own on_cog_remove
            # defensive cleanup (docs/corridor-pubsub-design.md) -- both
            # event-subscription mixins, and both office-gateway mixins'
            # OfficeStateChanged watches, register under this same owner.
            self._corridor.unsubscribe_owner("Cctv")
            self._corridor.unwatch_office_state_owner("Cctv")
            self._corridor.unregister_dependent("cctv")

    def _create_background_task(
        self, coroutine: Coroutine[Any, Any, object], *, name: str
    ) -> asyncio.Task[object]:
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    # --- overridden by office_gateway_discord.py / office_gateway_editor.py ---

    async def _on_webview_ready_discord(self, socket: Any) -> None:
        raise NotImplementedError

    async def _on_save_layout_discord(self, raw: dict[str, object]) -> None:
        raise NotImplementedError

    async def _on_save_seats_discord(self, incoming: Mapping[str, object]) -> None:
        raise NotImplementedError

    async def _authorize_discord_client(self, user_id: int) -> bool:
        raise NotImplementedError

    async def _on_webview_ready_editor(self, socket: Any) -> None:
        raise NotImplementedError

    async def _on_save_layout_editor(self, raw: dict[str, object]) -> None:
        raise NotImplementedError

    async def _on_save_seats_editor(self, incoming: Mapping[str, object]) -> None:
        raise NotImplementedError

    # --- overridden by discord_gateway.py ---

    async def _sync_all_guilds(self) -> None:
        raise NotImplementedError

    async def _full_sync(self, guild: Any) -> str:
        raise NotImplementedError

    async def _despawn_guild(self, guild: Any) -> None:
        raise NotImplementedError


__all__ = ["WEBVIEW_BASE_PATH", "CogBase"]
