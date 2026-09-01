"""Dependency composition and lifecycle for the Architect Cog.

architect no longer hosts any dashboard/WebSocket surface, and no longer
tracks a presence roster of its own -- both moved to `cctv`
(docs/cctv-design.md). What's left is the A2A agent itself: its LLM tool
loop and its layout-mutation tools, reading/writing the shared "editor"
office aggregate through pixelagents' `OfficeStateFacade`. Live delivery
to any connected `cctv` dashboard page happens automatically via
corridor's own `OfficeStateChanged` publish on every write -- architect
pushes no broadcast of its own.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from redbot.core.bot import Red

from corridor.domain import (
    AgentRef,
    AgentReplied,
    RegisteredAgent,
    ReplyCategory,
)
from pixelagents.application.office_state import OfficeStateFacade
from pixelagents.infrastructure.furniture_styles import FurnitureStyleLoader

from ..application import ToolLoopService
from ..application.office_layout_service import OfficeLayoutService
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure import (
    ArchitectAgentExecutor,
    CorridorLLMClient,
    RedArchitectRepository,
    build_agent_card,
)
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

    def __init__(self, pixelagents_ref: Any) -> None:
        self._pixelagents_ref = pixelagents_ref

    def furniture_style_manifest(self) -> dict[str, Any] | None:
        return cast("dict[str, Any] | None", self._pixelagents_ref().furniture_style_manifest())

    def webview_bundle_status(self) -> Any:
        return self._pixelagents_ref().webview_bundle_status()

    def office_state(self) -> OfficeStateFacade:
        return cast(OfficeStateFacade, self._pixelagents_ref().office_state())


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
        self._pixelagents: Any = None
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
        self._lazy_pixelagents = _LazyPixelAgents(lambda: self._pixelagents)
        self._style_loader = FurnitureStyleLoader(self._lazy_pixelagents)
        # The shared "editor" office aggregate lives in pixelagents'
        # OfficeStateFacade now, not a private Config store of architect's
        # own (docs/cctv-design.md) -- reached lazily, since `pixelagents`
        # isn't resolved until cog_load() but this repository is built
        # here in __init__.
        self._office_layout_repository = OfficeLayoutRepository(self._lazy_pixelagents.office_state)
        self._office_layout_service = OfficeLayoutService(
            self._office_layout_repository, self._style_loader
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
        await self._register_with_corridor()
        self._pixelagents = await ensure_loaded(self.bot, "pixelagents", "PixelAgents")

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

    async def cog_unload(self) -> None:
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


__all__ = ["CogBase"]
