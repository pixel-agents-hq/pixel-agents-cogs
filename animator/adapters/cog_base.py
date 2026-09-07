"""Dependency composition and lifecycle for the Animator Cog.

Animator registers on corridor's A2A listener and otherwise owns no domain
state of its own -- unlike architect/painter, it never touches pixelagents
or a shared office layout at all. Its capabilities live entirely in
pixel-art-mcp, reached through corridor's `AgentToolServerRegistry` (see
`_mcp_tools` below and README.md's `[p]telephonepole` setup), plus the one
native tool that turns a finished render job into real Discord attachments
(`tools/deliver_assets_tool.py`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
from redbot.core.bot import Red

from corridor.domain import AgentRef, AgentReplied, RegisteredAgent, ReplyCategory

from ..application import ToolLoopService
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure import AnimatorAgentExecutor, RedAnimatorRepository, build_agent_card
from ..infrastructure.corridor_llm import CorridorLLMClient
from ..tools.agent_tool_server import AgentToolServerTool
from ..tools.base import ToolSpec
from ..tools.deliver_assets_tool import DeliverPixelAgentsAssetsTool

log = logging.getLogger("red.animator")

ANIMATOR_AGENT_KEY = "animator"

# animator's own fixed identity on corridor's event bus -- A2A-reachable,
# not a Discord bot login, and isn't scoped to one guild, same shape
# architect's/painter's own AGENT_REF constants use.
ANIMATOR_AGENT_REF = AgentRef(
    discord_user_id=None, guild_id=None, is_bot=True, agent_key=ANIMATOR_AGENT_KEY
)

# Conventional path for animator's own bundled avatar image -- passed to
# both corridor.reply_sender() and RegisteredAgent.avatar_path regardless
# of whether a real file exists here yet; existence is checked fresh on
# every send. See docs/reply-identity-design.md.
AVATAR_PATH = Path(__file__).resolve().parent.parent / "assets" / "avatar.png"

# A render job's tool loop may make several sequential MCP round trips
# (create_project, execute_blender_python, render_preview, render_sprites,
# repeated get_job polls, then deliver_pixel_agents_assets' own artifact
# downloads) -- generous, same reasoning pico's ArchitectClient gives its
# own A2A request timeout for a consulted agent's whole bounded loop.
_HTTP_TIMEOUT_SECONDS = 120.0


async def _mcp_tools(corridor: Any, *, read_timeout_seconds: float | None = None) -> list[ToolSpec]:
    """Every MCP tool `[p]telephonepole` (or any future agent-tool-server
    provider) currently makes available to animator -- same shape
    architect's/painter's own `_mcp_tools` uses. Fetched fresh every turn,
    not cached, so a bot owner flipping a per-agent toggle takes effect on
    animator's very next turn. See docs/suggestionbox-design.md §6 and
    docs/telephonepole-design.md.

    `read_timeout_seconds` is passed straight through to every wrapped
    tool (`AgentToolServerTool`'s own `read_timeout_seconds`, see that
    class's docstring) -- animator's `[p]animator readtimeout`, read fresh
    each turn alongside the tool list itself so a change takes effect
    immediately too."""

    tools: list[ToolSpec] = []
    for tool in await corridor.list_agent_tools_for(ANIMATOR_AGENT_KEY):
        try:
            tools.append(AgentToolServerTool(tool, read_timeout_seconds=read_timeout_seconds))
        except Exception:
            log.warning(
                "animator: could not adapt MCP tool %r, skipping",
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
        self._repository = RedAnimatorRepository.create(self)
        self.config = self._repository.config
        self._corridor: Any = None
        self._reply: Any = None
        # Same lazy-lookup shape as architect's/painter's own CogBase --
        # corridor isn't resolved until cog_load().
        llm = CorridorLLMClient(lambda: self._corridor)
        self._tool_loop_service = ToolLoopService(llm)
        self._http_client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS)
        self._tools: list[ToolSpec] = [
            DeliverPixelAgentsAssetsTool(
                # A plain closure over `self`, read at call time -- same
                # lazy-lookup shape `llm_settings=lambda: self._corridor.
                # llm_settings()` below already relies on: `self._corridor`
                # is still `None` here in `__init__`, and only becomes real
                # once `cog_load()` runs, well before this is ever called.
                get_registered_tools=lambda: self._corridor.list_agent_tools_for(
                    ANIMATOR_AGENT_KEY
                ),
                http_client=self._http_client,
            )
        ]
        self._executor = AnimatorAgentExecutor(
            tool_loop=self._tool_loop_service,
            tools=self._tools,
            settings=self._repository.global_settings,
            llm_settings=lambda: self._corridor.llm_settings(),
            publish_activity=self._publish_activity,
            mcp_tools=self._mcp_tools,
        )

    async def _mcp_tools(self) -> list[ToolSpec]:
        """Bound method form of the module-level `_mcp_tools`, so
        `AnimatorAgentExecutor`'s `mcp_tools` callback can read animator's
        *current* `read_timeout_seconds` each turn instead of whatever was
        configured at `__init__` time."""

        settings = await self._repository.global_settings()
        return await _mcp_tools(self._corridor, read_timeout_seconds=settings.read_timeout_seconds)

    async def cog_load(self) -> None:
        """`required_cogs` in `info.json` is only a Downloader install
        hint -- Red does not auto-load a dependency at runtime just
        because it's declared there, so `ensure_corridor_loaded()` pulls
        corridor back in if it was unloaded independently."""

        self._corridor = await ensure_corridor_loaded(self.bot)
        # So unloading corridor cascades to unload this cog too, instead of
        # leaving it running with a stale corridor reference.
        self._corridor.register_dependent("animator")
        self._reply = self._corridor.reply_sender(
            owner="Animator", avatar_path=AVATAR_PATH, category=ReplyCategory.AGENT
        )
        await self._register_with_corridor()

    async def cog_unload(self) -> None:
        if self._corridor is not None:
            await self._corridor.unregister_agent_owner("Animator")
            self._corridor.unregister_dependent("animator")
        await self._http_client.aclose()

    async def _register_with_corridor(self) -> None:
        """Hands corridor animator's AgentCard + AgentExecutor so it can be
        mounted on corridor's own shared A2A listener -- same shape
        architect's/painter's own `_register_with_corridor` uses. Must
        never raise: corridor's own `register_agent` already never raises
        for a bind failure, but a stale/removed corridor reference
        mid-reload shouldn't fail animator's own load either."""

        card = build_agent_card(tools=self._tools)
        try:
            await self._corridor.register_agent(
                RegisteredAgent(
                    agent_key=ANIMATOR_AGENT_KEY,
                    card=card,
                    executor=self._executor,
                    avatar_path=AVATAR_PATH,
                ),
                # Matches AgentDirectoryService.register's own documented
                # convention (the cog's class name) -- see painter's own
                # equivalent comment.
                owner="Animator",
            )
        except Exception:
            log.exception("animator: could not register with corridor's agent directory")

    async def _publish_activity(self, summary: str) -> None:
        """Reports one tool-use or "thinking" step from animator's own
        tool loop as an AgentReplied -- same shape architect's/painter's
        own `_publish_activity` uses. A publish failure must never fail
        the tool loop itself."""

        try:
            await self._corridor.publish_event(
                AgentReplied(agent=ANIMATOR_AGENT_REF, summary=summary)
            )
        except Exception:
            log.exception("animator: failed to publish tool/thinking activity")


__all__ = ["ANIMATOR_AGENT_KEY", "ANIMATOR_AGENT_REF", "AVATAR_PATH", "CogBase"]
