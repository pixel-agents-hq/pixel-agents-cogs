"""Dependency composition and lifecycle for the Architect Cog."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from aiohttp import web
from redbot.core.bot import Red

from corridor.domain import (
    AgentRef,
    AgentReplied,
    RegisteredAgent,
    ReplyCategory,
)
from pixelagents.application.office import OfficeService

from ..application import ToolLoopService
from ..application.office_layout_service import OfficeLayoutService
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure import (
    ArchitectAgentExecutor,
    ClientHub,
    CorridorLLMClient,
    NullSeatRepository,
    RedArchitectRepository,
    WebSocketServer,
    WebviewAssetProvider,
    build_agent_card,
)
from ..infrastructure.furniture_styles import FurnitureStyleLoader
from ..infrastructure.office_layout_repository import OfficeLayoutRepository
from ..tools.agent_tool_server import AgentToolServerTool
from ..tools.base import ToolSpec
from ..tools.office_tools import build_office_tools
from ..tools.placeholder_tools import BreakDownTaskTool, ReviewDesignTool

log = logging.getLogger("red.architect")

ARCHITECT_AGENT_KEY = "architect"


async def _mcp_tools(corridor: Any) -> list[ToolSpec]:
    """Every MCP tool suggestionbox (or any future agent-tool-server
    provider) currently makes available to architect, adapted into
    architect's own `ToolSpec` -- fetched fresh every A2A turn (never
    cached), so a bot owner flipping suggestionbox's per-agent toggle
    takes effect on architect's very next message, no cog reload
    required. Same per-entry try/except-and-skip shape pico's own
    `_agent_tools`/`_cross_cog_tools` (`pico/adapters/listener.py`) use,
    so one malformed tool never takes down the whole turn. See
    docs/suggestionbox-design.md §6."""

    tools: list[ToolSpec] = []
    for tool in await corridor.list_agent_tools_for(ARCHITECT_AGENT_KEY):
        try:
            tools.append(AgentToolServerTool(tool))
        except Exception:
            log.warning(
                "architect: could not adapt MCP tool %r, skipping",
                getattr(tool, "name", "?"),
                exc_info=True,
            )
    return tools


# Injected as a `<base href>` at serve time (WebviewAssetProvider.base_href)
# -- mirrors floorplan's own WEBVIEW_BASE_PATH constant, one route per cog.
WEBVIEW_BASE_PATH = "/third-party/architect/static/"

# architect's own fixed identity on corridor's event bus -- architect is
# A2A-reachable, not a Discord bot login, and isn't scoped to one guild,
# so it has neither a real discord_user_id nor a guild_id (see AgentRef's
# docstring and docs/corridor-pubsub-design.md).
ARCHITECT_AGENT_REF = AgentRef(
    discord_user_id=None, guild_id=None, is_bot=True, agent_key="architect"
)

# Conventional path for architect's own bundled avatar image -- passed to
# both corridor.reply_sender() and RegisteredAgent.avatar_path regardless
# of whether a real file exists here yet; existence is checked fresh on
# every send, so dropping a real image at this exact path later needs no
# code change. See docs/reply-identity-design.md.
AVATAR_PATH = Path(__file__).resolve().parent.parent / "assets" / "avatar.png"


class _LazyPixelAgents:
    """Same lazy-lookup shape as `CorridorLLMClient` -- `FurnitureStyleLoader`
    is constructed in `__init__`, but `pixelagents` isn't resolved until
    `cog_load()` runs."""

    def __init__(self, pixelagents_ref: Callable[[], Any]) -> None:
        self._pixelagents_ref = pixelagents_ref

    def furniture_style_manifest(self) -> dict[str, Any] | None:
        return cast("dict[str, Any] | None", self._pixelagents_ref().furniture_style_manifest())

    def webview_bundle_status(self) -> Any:
        return self._pixelagents_ref().webview_bundle_status()


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._repository = RedArchitectRepository.create(self)
        self.config = self._repository.config
        self._corridor: Any = None
        self._reply: Any = None
        # The shared LLM connection lives in corridor (see
        # docs/architect-design.md) -- this proxy defers the actual lookup
        # until each call, since corridor isn't resolved until cog_load().
        llm = CorridorLLMClient(lambda: self._corridor)
        self._tool_loop_service = ToolLoopService(llm)
        # Same lazy-lookup shape as `CorridorLLMClient` above: the style
        # loader is built now (so the office tools can be constructed here
        # too, once, in __init__ -- cog_load() can legitimately run more
        # than once per instance, and self._tools must never grow a
        # duplicate set of office tools on a second run), but the actual
        # `pixelagents` reference isn't resolved until cog_load().
        self._style_loader = FurnitureStyleLoader(_LazyPixelAgents(lambda: self._pixelagents))
        self._office_layout_repository = OfficeLayoutRepository(self._repository)
        self._office_layout_service = OfficeLayoutService(
            self._office_layout_repository, self._style_loader, broadcast=self._broadcast_layout
        )
        self._tools: list[ToolSpec] = [
            ReviewDesignTool(),
            BreakDownTaskTool(),
            *build_office_tools(self._office_layout_service, self._style_loader),
        ]
        self._executor = ArchitectAgentExecutor(
            tool_loop=self._tool_loop_service,
            tools=self._tools,
            settings=self._repository.global_settings,
            llm_settings=lambda: self._corridor.llm_settings(),
            publish_activity=self._publish_activity,
            mcp_tools=lambda: _mcp_tools(self._corridor),
        )
        self._pixelagents: Any = None
        # Root is a placeholder until _sync_webview_assets() resolves
        # pixelagents; base_href never changes, so it's set once here --
        # same shape as floorplan's PixelAgentsBase.__init__.
        self._webview_assets = WebviewAssetProvider(Path(), logger=log)
        self._webview_assets.base_href = WEBVIEW_BASE_PATH
        self._webview_built_commit: str | None = None
        self._webview_build_convention_stale = False
        # architect's own office WebSocket -- independent from floorplan's:
        # see docs/architect-design.md on why this webview must never share
        # a live connection (or its layout) with floorplan's. NullSeatRepository
        # means no seat/palette assignment survives a restart -- it does NOT
        # mean an empty agent roster: PresenceSubscriptionMixin
        # (adapters/presence_subscription.py) feeds this OfficeService
        # instance's genuine-agent roster from corridor's AgentPresenceChanged
        # events, separately from seats/layout.
        self._client_hub = ClientHub(logger=log)
        self._office_service = OfficeService(NullSeatRepository(), self._send)
        self._websocket_server = WebSocketServer(
            clients=self._client_hub,
            on_webview_ready=self._on_webview_ready,
            on_save_layout=self._on_save_layout,
            health_snapshot=self._health_snapshot,
            logger=log,
        )

    async def _send(self, message: Mapping[str, object]) -> None:
        await self._client_hub.broadcast(message)

    async def _broadcast_layout(self, raw: dict[str, Any]) -> None:
        """`OfficeLayoutService`'s broadcast callback -- pushed to every
        connected webview client after a successful mutation, same
        message shape floorplan's own office_gateway.py already uses for
        `saveLayout`."""

        await self._send({"type": "layoutLoaded", "layout": raw})

    async def cog_load(self) -> None:
        """required_cogs in info.json is only a Downloader install hint --
        Red does not auto-load a dependency at runtime just because it's
        declared there, so ensure_corridor_loaded()/ensure_loaded() pull
        corridor/pixelagents back in if either was unloaded independently."""

        from corridor.dependency_loader import ensure_loaded

        self._corridor = await ensure_corridor_loaded(self.bot)
        # So unloading corridor cascades to unload this cog too, instead of
        # leaving it running with a stale corridor reference.
        self._corridor.register_dependent("architect")
        self._reply = self._corridor.reply_sender(
            owner="Architect", avatar_path=AVATAR_PATH, category=ReplyCategory.AGENT
        )
        await self._start_presence_tracking()
        await self._register_with_corridor()
        self._pixelagents = await ensure_loaded(self.bot, "pixelagents", "PixelAgents")
        await self._notify_owners_dashboard_missing_if_unloaded()
        if not await self._start_ws_server():
            await self._notify_owners_ws_failed()

    async def _register_with_corridor(self) -> None:
        """Hands corridor architect's AgentCard + AgentExecutor so it can
        be mounted on corridor's own shared A2A listener -- architect no
        longer binds a listener of its own, see
        docs/agent-directory-design.md. Must never raise: corridor's own
        `register_agent` already never raises for a bind failure (that
        risk lives entirely in corridor now), but a stale/removed corridor
        reference mid-reload shouldn't fail architect's own load either."""

        card = build_agent_card(tools=self._tools)
        try:
            await self._corridor.register_agent(
                RegisteredAgent(
                    agent_key="architect",
                    card=card,
                    executor=self._executor,
                    avatar_path=AVATAR_PATH,
                ),
                owner="architect",
            )
        except Exception:
            log.exception("architect: could not register with corridor's agent directory")

    async def _start_presence_tracking(self) -> None:
        """Overridden by PresenceSubscriptionMixin -- kept as a no-op stub
        here so CogBase alone stays usable without that mixin, same
        pattern as `_start_ws_server`/`_notify_owners_dashboard_missing_if_unloaded`.
        Must run before `_register_with_corridor()`: that call is what now
        triggers corridor's own auto-published "online" AgentPresenceChanged
        for architect's own agent_key (see corridor/adapters/cog_base.py's
        `register_agent`) -- subscribing any later would miss architect's
        own self-registration event on every fresh load."""

    async def _start_ws_server(self) -> bool:
        """Overridden by OfficeGatewayMixin -- kept as a no-op-failure stub
        here so CogBase alone stays usable without that mixin, same
        pattern as `_notify_owners_dashboard_missing_if_unloaded`."""

        return True

    async def _notify_owners_ws_failed(self) -> None:
        """Best-effort DM -- must never raise, same convention as
        `_notify_owners_a2a_failed`. aiohttp's own `WebSocketServer.start`
        already logs the specific bind error; this only needs to point an
        owner at the fix."""

        settings = await self._repository.global_settings()
        message = (
            f"⚠️ architect's office WebSocket server failed to start on "
            f"{settings.ws_host}:{settings.ws_port}. architect is still loaded and its "
            "Discord commands work, but its webview will show no live layout until this "
            "is fixed -- try [p]architect ws host/port once the issue is resolved."
        )
        try:
            await self.bot.send_to_owners(message)
        except Exception:
            log.exception("architect: could not notify owners about the WebSocket server failure")

    def _load_assets(self) -> None:
        self._webview_assets.load_assets()

    async def _sync_webview_assets(self) -> None:
        """Refresh the built-bundle path/status from pixelagents. Same
        shape as floorplan's own `PixelAgentsBase._sync_webview_assets`."""

        status = self._pixelagents.webview_bundle_status()
        self._webview_assets.root = status.dist_path.resolve()
        self._webview_assets.build_status = None if status.ready else status.detail
        if status.ready and status.built_commit != self._webview_built_commit:
            await asyncio.to_thread(self._load_assets)
            self._webview_built_commit = status.built_commit
            self._check_webview_build_convention(getattr(status, "built_base_path", None))
            await self._ensure_layout_seeded()

    async def _ensure_layout_seeded(self) -> None:
        """Seed architect's own layout store from pixelagents' bundled
        default layout, once -- only if nothing is stored yet.

        This is architect's own, independent layout, entirely separate
        from floorplan's per-guild office Config: loading a Pixel Index
        layout into floorplan's office (`[p]floorplan layout view ...` ->
        "Load into office") writes only to floorplan's own storage and
        never touches this one. There is no command to edit this yet --
        only the future layout-editing tools will (see
        docs/architect-design.md)."""

        if await self._repository.layout() is not None:
            return
        default_layout = self._webview_assets.default_layout()
        if default_layout is None:
            return
        await self._repository.set_layout(default_layout)
        log.info("architect: seeded its own layout store from pixelagents' bundled default layout")

    def _check_webview_build_convention(self, built_base_path: str | None) -> None:
        # "./" is pixelagents' own RELATIVE_BASE_PATH build convention --
        # duplicated as a literal here rather than imported, for the same
        # reason WebviewAssetProvider itself is duplicated (see
        # infrastructure/webview.py's module docstring).
        stale = bool(built_base_path) and built_base_path != "./"
        self._webview_build_convention_stale = stale
        if stale:
            log.warning(
                "architect: webview built for %s, not ./ -- assets will 404. Run "
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

    async def cog_unload(self) -> None:
        await self._websocket_server.stop()
        if self._corridor is not None:
            await self._corridor.unregister_agent_owner("architect")
            self._corridor.unregister_dependent("architect")

    async def _publish_activity(self, summary: str) -> None:
        """Reports one tool-use or "thinking" step from architect's own
        tool loop as an AgentReplied -- see AgentReplied's docstring on why
        this overloads that event rather than AgentToolStarted. A publish
        failure must never fail the tool loop itself."""

        try:
            await self._corridor.publish_event(
                AgentReplied(agent=ARCHITECT_AGENT_REF, summary=summary)
            )
        except Exception:
            log.exception("architect: failed to publish tool/thinking activity")

    async def _notify_owners_dashboard_missing_if_unloaded(self) -> None:
        """Overridden by DashboardMixin -- kept as a no-op stub here so
        CogBase alone (e.g. in isolation-focused tests) stays usable
        without the Dashboard-facing mixin, same pattern as floorplan's
        `PixelAgentsBase`."""

    async def _on_webview_ready(self, socket: web.WebSocketResponse) -> None:
        """Overridden by OfficeGatewayMixin -- kept as a no-op stub here so
        CogBase alone stays usable without that mixin."""

    async def _on_save_layout(self, raw_layout: dict[str, Any]) -> None:
        """Overridden by OfficeGatewayMixin -- kept as a no-op stub here so
        CogBase alone stays usable without that mixin."""

    def _health_snapshot(self) -> Mapping[str, object]:
        """Overridden by OfficeGatewayMixin -- kept as a stub here so
        CogBase alone stays usable without that mixin."""

        return {}
