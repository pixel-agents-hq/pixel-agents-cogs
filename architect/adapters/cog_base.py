"""Architect's LLM, A2A, tool, and editor-state composition."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from redbot.core.bot import Red

from corridor.domain import AgentRef, AgentReplied, RegisteredAgent, ReplyCategory
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
ARCHITECT_AGENT_REF = AgentRef(
    discord_user_id=None,
    guild_id=None,
    is_bot=True,
    agent_key=ARCHITECT_AGENT_KEY,
)
AVATAR_PATH = Path(__file__).resolve().parent.parent / "assets" / "avatar.png"


async def _mcp_tools(corridor: Any) -> list[ToolSpec]:
    """Adapt the MCP tools currently enabled for Architect."""

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


class _LazyPixelAgents:
    def __init__(self, pixelagents_ref: Callable[[], Any]) -> None:
        self._pixelagents_ref = pixelagents_ref

    def furniture_style_manifest(self) -> dict[str, Any] | None:
        return cast(
            "dict[str, Any] | None",
            self._pixelagents_ref().furniture_style_manifest(),
        )

    def webview_bundle_status(self) -> Any:
        return self._pixelagents_ref().webview_bundle_status()


class CogBase:
    """Wire Architect once without owning any browser-facing resources."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._repository = RedArchitectRepository.create(self)
        self.config = self._repository.config
        self._corridor: Any = None
        self._pixelagents: Any = None
        self._reply: Any = None

        llm = CorridorLLMClient(lambda: self._corridor)
        self._tool_loop_service = ToolLoopService(llm)
        self._style_loader = FurnitureStyleLoader(_LazyPixelAgents(lambda: self._pixelagents))
        self._office_layout_repository = OfficeLayoutRepository(lambda: self._pixelagents)
        self._office_layout_service = OfficeLayoutService(
            self._office_layout_repository,
            self._style_loader,
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
        from corridor.dependency_loader import ensure_loaded

        try:
            self._corridor = await ensure_corridor_loaded(self.bot)
            self._corridor.register_dependent("architect")
            self._reply = self._corridor.reply_sender(
                owner="Architect",
                avatar_path=AVATAR_PATH,
                category=ReplyCategory.AGENT,
            )
            self._pixelagents = await ensure_loaded(self.bot, "pixelagents", "PixelAgents")
            await self._register_with_corridor()
        except Exception:
            await self.cog_unload()
            raise

    async def cog_unload(self) -> None:
        if self._corridor is not None:
            await self._corridor.unregister_agent_owner("architect")
            self._corridor.unregister_dependent("architect")

    async def refresh_pixelagents(self, pixelagents: Any) -> None:
        """Called by pixelagents itself after it (re)loads, so an
        independent pixelagents reload doesn't leave architect holding a
        stale Cog reference -- `_style_loader`/`_office_layout_repository`
        both already read `self._pixelagents` lazily via a closure, so
        updating this attribute is the whole fix. See pixelagents'
        `_refresh_dependents` docstring."""

        self._pixelagents = pixelagents

    async def _register_with_corridor(self) -> None:
        card = build_agent_card(tools=self._tools)
        try:
            await self._corridor.register_agent(
                RegisteredAgent(
                    agent_key=ARCHITECT_AGENT_KEY,
                    card=card,
                    executor=self._executor,
                    avatar_path=AVATAR_PATH,
                ),
                owner="architect",
            )
        except Exception:
            log.exception("architect: could not register with corridor's agent directory")

    async def _publish_activity(self, summary: str) -> None:
        try:
            await self._corridor.publish_event(
                AgentReplied(agent=ARCHITECT_AGENT_REF, summary=summary)
            )
        except Exception:
            log.exception("architect: failed to publish tool/thinking activity")


__all__ = [
    "ARCHITECT_AGENT_KEY",
    "ARCHITECT_AGENT_REF",
    "AVATAR_PATH",
    "CogBase",
]
