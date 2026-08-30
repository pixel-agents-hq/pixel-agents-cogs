"""Dependency composition and lifecycle for the Painter Cog.

A deliberate parallel copy of `architect/adapters/cog_base.py`'s shape,
stripped of everything architect needs that painter doesn't: no WebSocket
server, no webview, no Dashboard route, no presence tracking -- painter
serves no browser-facing surface of its own at all, it only registers
with corridor's shared A2A listener and edits the one shared office
layout directly. See docs/painter-design.md.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from redbot.core.bot import Red

from corridor.domain import AgentRef, AgentReplied, RegisteredAgent, ReplyCategory
from pixelagents.infrastructure.furniture_styles import FurnitureStyleLoader
from pixelagents.infrastructure.office_layout_settings import RedOfficeLayoutSettings

from ..application import ToolLoopService
from ..application.painter_layout_service import PainterLayoutService
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure import PainterAgentExecutor, RedPainterRepository, build_agent_card
from ..infrastructure.architect_client import ArchitectClient
from ..infrastructure.corridor_llm import CorridorLLMClient
from ..infrastructure.office_layout_repository import OfficeLayoutRepository
from ..tools.agent_tool_server import AgentToolServerTool
from ..tools.base import ToolSpec
from ..tools.consult_architect_tool import ConsultArchitectTool
from ..tools.painter_tools import build_painter_tools

log = logging.getLogger("red.painter")

PAINTER_AGENT_KEY = "painter"

# painter's own fixed identity on corridor's event bus -- A2A-reachable,
# not a Discord bot login, and isn't scoped to one guild, same shape
# architect's own ARCHITECT_AGENT_REF uses.
PAINTER_AGENT_REF = AgentRef(discord_user_id=None, guild_id=None, is_bot=True, agent_key="painter")

# Conventional path for painter's own bundled avatar image -- passed to
# both corridor.reply_sender() and RegisteredAgent.avatar_path regardless
# of whether a real file exists here yet; existence is checked fresh on
# every send. See docs/reply-identity-design.md.
AVATAR_PATH = Path(__file__).resolve().parent.parent / "assets" / "avatar.png"


async def _mcp_tools(corridor: Any) -> list[ToolSpec]:
    """Every MCP tool suggestionbox (or any future agent-tool-server
    provider) currently makes available to painter -- same shape
    architect's own `_mcp_tools` uses. See docs/suggestionbox-design.md §6."""

    tools: list[ToolSpec] = []
    for tool in await corridor.list_agent_tools_for(PAINTER_AGENT_KEY):
        try:
            tools.append(AgentToolServerTool(tool))
        except Exception:
            log.warning(
                "painter: could not adapt MCP tool %r, skipping",
                getattr(tool, "name", "?"),
                exc_info=True,
            )
    return tools


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._repository = RedPainterRepository.create(self)
        self.config = self._repository.config
        self._corridor: Any = None
        self._reply: Any = None
        self._pixelagents: Any = None
        # Same lazy-lookup shape as architect's own CogBase -- corridor
        # isn't resolved until cog_load().
        llm = CorridorLLMClient(lambda: self._corridor)
        self._tool_loop_service = ToolLoopService(llm)
        self._style_loader = FurnitureStyleLoader(_LazyPixelAgents(lambda: self._pixelagents))
        # The shared office layout -- same store architect reads/writes,
        # reached independently (not through a live architect reference):
        # see docs/painter-design.md part A.
        self._office_layout_settings = RedOfficeLayoutSettings.create()
        self._office_layout_repository = OfficeLayoutRepository(self._office_layout_settings)
        self._painter_layout_service = PainterLayoutService(
            self._office_layout_repository,
            self._style_loader,
            on_layout_changed=self._notify_architect_layout_changed,
        )
        self._architect_client = ArchitectClient()
        self._tools: list[ToolSpec] = [
            ConsultArchitectTool(self._architect_client, _LazyCorridor(lambda: self._corridor)),
            *build_painter_tools(self._painter_layout_service),
        ]
        self._executor = PainterAgentExecutor(
            tool_loop=self._tool_loop_service,
            tools=self._tools,
            settings=self._repository.global_settings,
            llm_settings=lambda: self._corridor.llm_settings(),
            publish_activity=self._publish_activity,
            mcp_tools=lambda: _mcp_tools(self._corridor),
        )

    async def cog_load(self) -> None:
        """required_cogs in info.json is only a Downloader install hint --
        Red does not auto-load a dependency at runtime just because it's
        declared there, so ensure_corridor_loaded()/ensure_loaded() pull
        corridor/pixelagents back in if either was unloaded independently."""

        from corridor.dependency_loader import ensure_loaded

        self._corridor = await ensure_corridor_loaded(self.bot)
        # So unloading corridor cascades to unload this cog too, instead of
        # leaving it running with a stale corridor reference.
        self._corridor.register_dependent("painter")
        self._reply = self._corridor.reply_sender(
            owner="Painter", avatar_path=AVATAR_PATH, category=ReplyCategory.AGENT
        )
        await self._register_with_corridor()
        self._pixelagents = await ensure_loaded(self.bot, "pixelagents", "PixelAgents")

    async def cog_unload(self) -> None:
        if self._corridor is not None:
            await self._corridor.unregister_agent_owner("painter")
            self._corridor.unregister_dependent("painter")

    async def _register_with_corridor(self) -> None:
        """Hands corridor painter's AgentCard + AgentExecutor so it can be
        mounted on corridor's own shared A2A listener -- same shape
        architect's own `_register_with_corridor` uses. Must never raise:
        corridor's own `register_agent` already never raises for a bind
        failure, but a stale/removed corridor reference mid-reload
        shouldn't fail painter's own load either."""

        card = build_agent_card(tools=self._tools)
        try:
            await self._corridor.register_agent(
                RegisteredAgent(
                    agent_key="painter", card=card, executor=self._executor, avatar_path=AVATAR_PATH
                ),
                owner="painter",
            )
        except Exception:
            log.exception("painter: could not register with corridor's agent directory")

    async def _publish_activity(self, summary: str) -> None:
        """Reports one tool-use or "thinking" step from painter's own tool
        loop as an AgentReplied -- same shape architect's own
        `_publish_activity` uses. A publish failure must never fail the
        tool loop itself."""

        try:
            await self._corridor.publish_event(
                AgentReplied(agent=PAINTER_AGENT_REF, summary=summary)
            )
        except Exception:
            log.exception("painter: failed to publish tool/thinking activity")

    async def _notify_architect_layout_changed(self) -> None:
        """Best-effort: painter has no WebSocket server of its own to push
        a live update through the way architect's own writes already do
        synchronously as part of the same mutation
        (`OfficeLayoutService._persist`) -- without this, a painter-made
        recolor is real and persisted immediately, but a browser already
        showing the office only picks it up on its next manual reload.
        `bot.get_cog("Architect")` is the same lazy, optional cross-cog
        lookup shape used throughout this repo (e.g.
        `dependency_loader.py`'s `ensure_loaded`) -- if architect isn't
        currently loaded, or the lookup/call fails for any other reason,
        this silently does nothing rather than failing the recolor that
        already succeeded and persisted before this callback ever runs."""

        try:
            architect = self.bot.get_cog("Architect")
            if architect is None:
                return
            await architect.notify_shared_layout_changed()
        except Exception:
            log.exception("painter: could not notify architect to refresh its webview clients")


class _LazyPixelAgents:
    """Same lazy-lookup shape as `CorridorLLMClient` -- `FurnitureStyleLoader`
    is constructed in `__init__`, but the actual `pixelagents` reference
    isn't resolved until `cog_load()`."""

    def __init__(self, pixelagents_ref: Any) -> None:
        self._pixelagents_ref = pixelagents_ref

    def furniture_style_manifest(self) -> dict[str, Any] | None:
        return self._pixelagents_ref().furniture_style_manifest()  # type: ignore[no-any-return]

    def webview_bundle_status(self) -> Any:
        return self._pixelagents_ref().webview_bundle_status()


class _LazyCorridor:
    """Same lazy-lookup shape as `_LazyPixelAgents` -- `ConsultArchitectTool`
    is constructed in `__init__`, but the actual `corridor` reference
    isn't resolved until `cog_load()`."""

    def __init__(self, corridor_ref: Any) -> None:
        self._corridor_ref = corridor_ref

    def list_agents(self) -> Any:
        return self._corridor_ref().list_agents()


__all__ = ["CogBase"]
