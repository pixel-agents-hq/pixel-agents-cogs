"""Dependency composition and lifecycle for the Suggestionbox Cog."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from redbot.core.bot import Red

from corridor.domain import RegisteredMcpServer, ReplyField

from ..application import FeedbackService
from ..dependency_loader import ensure_corridor_loaded
from ..infrastructure import McpListener, RedSuggestionboxRepository, build_mcp_server

log = logging.getLogger("red.suggestionbox")

# Conventional path for this cog's own bundled avatar image -- passed to
# corridor.reply_sender() regardless of whether a real file exists here
# yet; existence is checked fresh on every send, so dropping a real image
# at this exact path later needs no code change. See
# docs/reply-identity-design.md.
AVATAR_PATH = Path(__file__).resolve().parent.parent / "assets" / "avatar.png"


class CogBase:
    """Wire services once and own resources spanning the Cog lifetime."""

    bot: Red
    config: Any

    def __init__(self, bot: Red) -> None:
        self.bot = bot
        self._repository = RedSuggestionboxRepository.create(self)
        self.config = self._repository.config
        self._service = FeedbackService(self._repository, post=self._post_feedback)
        self._mcp_listener = McpListener(logger=log)
        self._mcp_base_url: str | None = None
        self._corridor: Any = None
        self._reply: Any = None

    async def cog_load(self) -> None:
        """required_cogs in info.json is only a Downloader install hint --
        Red does not auto-load a dependency at runtime just because it's
        declared there, so ensure_corridor_loaded() pulls corridor back in
        if it was unloaded independently. Also starts this cog's own MCP
        listener and registers it with corridor's AgentToolServerRegistry
        -- see docs/suggestionbox-design.md §3/§4."""

        self._corridor = await ensure_corridor_loaded(self.bot)
        # So unloading corridor cascades to unload this cog too, instead of
        # leaving it running with a stale corridor reference.
        self._corridor.register_dependent("suggestionbox")
        # Bound once, reused at every reply call site (self._reply.
        # send_channel_reply(...)) instead of repeating this cog's owner
        # name as an argument everywhere -- see docs/reply-identity-design.md.
        self._reply = self._corridor.reply_sender(owner="Suggestionbox", avatar_path=AVATAR_PATH)
        error = await self._restart_mcp()
        if error is not None:
            await self._notify_owners_mcp_failed(error)

    async def cog_unload(self) -> None:
        await self._mcp_listener.stop()
        if self._corridor is not None:
            if self._mcp_base_url is not None:
                self._corridor.unregister_mcp_server(self._mcp_base_url)
                self._mcp_base_url = None
            self._corridor.unregister_dependent("suggestionbox")

    async def _restart_mcp(self) -> str | None:
        """(Re)builds this cog's `FastMCP` app from its current settings,
        (re)binds `McpListener` to them, and (re)registers with corridor
        under the resulting `base_url` -- called from `cog_load` and
        whenever `[p]suggestionbox mcp host/port` changes. Never raises;
        returns an error string on a bind/registration failure, same
        never-raise convention `corridor`'s own `_start_a2a_server` uses.
        """

        host, port = await self._repository.mcp_listener()
        mcp = build_mcp_server(self._service, host=host, port=port)
        error = await self._mcp_listener.start(mcp, host=host, port=port)
        if error is not None:
            return error

        new_base_url = f"http://{host}:{port}/mcp"
        if self._mcp_base_url is not None and self._mcp_base_url != new_base_url:
            self._corridor.unregister_mcp_server(self._mcp_base_url)
        registration_error: str | None = await self._corridor.register_mcp_server(
            RegisteredMcpServer(
                owner="Suggestionbox",
                base_url=new_base_url,
                agent_allowed=self._repository.is_agent_enabled,
            ),
            owner="Suggestionbox",
        )
        self._mcp_base_url = new_base_url
        return registration_error

    async def _notify_owners_mcp_failed(self, error: str) -> None:
        """Best-effort DM -- must never raise: a missing/unreachable owner
        DM is not a reason to fail this cog's own load."""

        message = (
            f"⚠️ suggestionbox's MCP listener failed to start ({error}). "
            "suggestionbox is still loaded and its Discord commands work, but "
            "no MCP client -- external, or a registered A2A agent's own tool "
            "loop -- can reach report_error/suggest_improvement until this is "
            "fixed. Try [p]suggestionbox mcp host/port once the issue is "
            "resolved."
        )
        try:
            await self.bot.send_to_owners(message)
        except Exception:
            log.exception("suggestionbox: could not notify owners about the MCP listener failure")

    async def _post_feedback(
        self,
        guild_id: int,
        channel_id: int,
        title: str,
        description: str,
        fields: Sequence[tuple[str, str]],
    ) -> bool:
        """`FeedbackService`'s `Poster` implementation -- the only place in
        this cog that imports `corridor.domain.ReplyField` or calls
        `corridor.send_channel_reply`, matching `FeedbackService`'s own
        "corridor-agnostic business logic, corridor-aware adapter" split.
        `channel_id` has no live Discord ctx to resolve it from (an MCP
        tool call has none at all -- see docs/suggestionbox-design.md §5),
        so it's resolved directly off `self.bot`."""

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            return False
        await self._reply.send_channel_reply(
            channel,
            guild_id,
            title=title,
            description=description,
            fields=[ReplyField(name=name, value=value) for name, value in fields],
        )
        return True

    async def set_mcp_host(self, value: str) -> str | None:
        await self._repository.set_mcp_host(value)
        return await self._restart_mcp()

    async def set_mcp_port(self, value: int) -> str | None:
        await self._repository.set_mcp_port(value)
        return await self._restart_mcp()

    def list_agents(self) -> tuple[Any, ...]:
        """Every agent currently registered in corridor's AgentDirectoryService
        -- the candidate rows for the Components V2 access panel
        (adapters/agent_access_panel.py). Empty (not an error) if corridor
        has no registered agents, or this cog hasn't finished cog_load yet.

        Plain repository CRUD (feedback channel, per-agent enabled state)
        is read/written straight off `self._repository` by commands.py/
        agent_access_panel.py instead of a thin CogBase wrapper per field
        -- matching architect's own CommandsMixin -> `self._repository.<x>`
        precedent."""

        if self._corridor is None:
            return ()
        return tuple(self._corridor.list_agents())
