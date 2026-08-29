"""Discord-facing commands. Thin: translate ctx <-> repository/CogBase
calls only.

Every command here is bot-owner-only -- this is bot-wide capability
configuration (which registered agent may use these MCP tools, where
feedback posts, what port the MCP listener binds), not guild content. See
docs/suggestionbox-design.md §2.

Replies go through corridor (this cog's required_cogs dependency) rather
than ctx.send()/hand-rolled role checks, so this cog automatically
respects whatever reply style the guild has already configured for every
other cog -- except `agents`, whose Components V2 panel is sent via a
plain `ctx.send(view=...)`, the same lint-exempt convention `[p]
corridorsettings`/`[p]floorplan settings` already use (Components V2
cannot be mixed with an embed/content, so it structurally cannot honor
ReplyMode).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import discord
from redbot.core import commands

from ..infrastructure import RedSuggestionboxRepository
from .agent_access_panel import AgentAccessView


class CommandsMixin:
    """Requires `self._repository: RedSuggestionboxRepository`,
    `self._corridor`, `self._reply`, `self._restart_mcp`, and
    `self.list_agents` (all provided by CogBase)."""

    _repository: RedSuggestionboxRepository
    _corridor: Any
    _reply: Any
    _restart_mcp: Callable[[], Awaitable[str | None]]
    list_agents: Callable[[], tuple[Any, ...]]

    @commands.hybrid_group(name="suggestionbox")
    @commands.is_owner()
    async def suggestionbox_group(self, ctx: commands.Context) -> None:
        """MCP feedback server for reporting errors/improvements, per-agent gated."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @suggestionbox_group.command(name="channel")
    @commands.is_owner()
    async def channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Set the one Discord channel report_error/suggest_improvement post to."""

        guild = ctx.guild
        assert guild is not None, "suggestionbox channel needs a guild context"
        await self._repository.set_feedback_channel(guild.id, channel.id)
        await self._reply.send_reply(
            ctx, title="Feedback channel set", description=f"Now posting to {channel.mention}."
        )

    @suggestionbox_group.group(name="mcp")
    @commands.is_owner()
    async def mcp_group(self, ctx: commands.Context) -> None:
        """Configure this cog's own MCP listener host/port."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @mcp_group.command(name="host")
    @commands.is_owner()
    async def mcp_host(self, ctx: commands.Context, host: str) -> None:
        """Set the MCP listener's bind host and restart it."""

        await self._repository.set_mcp_host(host)
        error = await self._restart_mcp()
        if error is not None:
            await self._reply.send_reply(
                ctx, title="MCP listener", description=f"Could not bind: {error}"
            )
            return
        await self._reply.send_reply(
            ctx, title="MCP listener", description=f"Now bound to host `{host}`."
        )

    @mcp_group.command(name="port")
    @commands.is_owner()
    async def mcp_port(self, ctx: commands.Context, port: int) -> None:
        """Set the MCP listener's bind port and restart it."""

        if not 1 <= port <= 65535:
            await self._reply.send_reply(
                ctx, title="MCP listener", description="Port must be between 1 and 65535."
            )
            return
        await self._repository.set_mcp_port(port)
        error = await self._restart_mcp()
        if error is not None:
            await self._reply.send_reply(
                ctx, title="MCP listener", description=f"Could not bind: {error}"
            )
            return
        await self._reply.send_reply(
            ctx, title="MCP listener", description=f"Now bound to port `{port}`."
        )

    @suggestionbox_group.command(name="agents")
    @commands.is_owner()
    async def agents(self, ctx: commands.Context) -> None:
        """Open the per-agent MCP access panel."""

        view = await AgentAccessView.create(self, owner_id=ctx.author.id)
        await ctx.send(view=view)
