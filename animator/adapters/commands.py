"""Discord-facing commands. Thin: translate ctx <-> repository calls only.

Replies go through corridor (this cog's required-cogs dependency), never a
raw ctx.send(), so this cog respects the configured reply style. All
settings are bot-owner scoped: animator is A2A-only and process-scoped, with
no per-guild state of its own.
"""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from corridor.domain import ReplyField

from ..infrastructure import RedAnimatorRepository
from .validation import parse_read_timeout, parse_request_timeout

_MASKED_KEY = "•" * 8


class CommandsMixin:
    """Requires the repository and cross-cog references from CogBase."""

    _repository: RedAnimatorRepository
    _corridor: Any
    _reply: Any

    @commands.hybrid_group(name="animator", invoke_without_command=True)
    async def animator_group(self, ctx: commands.Context) -> None:
        """Manage animator's tool-calling settings."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @animator_group.command(name="maxtoolcalls")
    @commands.is_owner()
    async def maxtoolcalls(self, ctx: commands.Context, count: int) -> None:
        """Set the max tool calls animator may make per A2A turn."""

        try:
            await self._repository.set_max_tool_calls(count)
        except ValueError as exc:
            await self._reply.send_reply(ctx, description=str(exc))
            return
        await self._reply.send_reply(ctx, description=f"Max tool calls per turn set to `{count}`.")

    @animator_group.command(name="debuglogging")
    @commands.is_owner()
    async def debug_logging(self, ctx: commands.Context, enabled: bool) -> None:
        """Enable or disable verbose per-tool-call logging (tool name,
        arguments, and result/error for every call the LLM makes this
        turn), logged at INFO under the `red.animator` logger. Off by
        default -- turn on only while diagnosing a tool-calling issue."""

        await self._repository.set_debug_logging(enabled)
        await self._reply.send_reply(
            ctx, description=f"Debug logging {'enabled' if enabled else 'disabled'}."
        )

    @animator_group.command(name="requesttimeout")
    @commands.is_owner()
    async def requesttimeout(self, ctx: commands.Context, value: str) -> None:
        """Override the LLM request timeout (in seconds) for animator's
        tool loop, or reset it to corridor's own default with `default`.

        pixel-art-mcp's tool calls (Blender scripting, rendering) can take
        far longer than a typical chat completion, so corridor's shared
        default timeout may need raising here specifically."""

        timeout_value, error = parse_request_timeout(value)
        if error is not None:
            await self._reply.send_reply(ctx, description=error)
            return
        await self._repository.set_request_timeout(timeout_value)
        display = "default" if timeout_value is None else f"{timeout_value:g}s"
        await self._reply.send_reply(ctx, description=f"Request timeout is now {display}.")

    @animator_group.command(name="readtimeout")
    @commands.is_owner()
    async def readtimeout(self, ctx: commands.Context, value: str) -> None:
        """Override the MCP tool-call read timeout (in seconds) for
        animator's bridged pixel-art-mcp tools, or reset it to
        McpClientPool's own default (wait indefinitely) with `default`.

        This is used as the timeout for pixel-art-mcp's wait_for_job tool
        specifically -- raise it if wait_for_job calls are timing out
        before the render they're waiting on actually finishes."""

        timeout_value, error = parse_read_timeout(value)
        if error is not None:
            await self._reply.send_reply(ctx, description=error)
            return
        await self._repository.set_read_timeout(timeout_value)
        display = "default (wait indefinitely)" if timeout_value is None else f"{timeout_value:g}s"
        await self._reply.send_reply(ctx, description=f"Read timeout is now {display}.")

    @animator_group.group(name="prompt", invoke_without_command=True)
    @commands.is_owner()
    async def prompt_group(self, ctx: commands.Context) -> None:
        """Manage animator's system prompt. Bot owner only."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @prompt_group.command(name="set")
    @commands.is_owner()
    async def prompt_set(self, ctx: commands.Context, *, text: str) -> None:
        """Set animator's system prompt."""

        await self._repository.set_system_prompt(text)
        await self._reply.send_reply(ctx, description="System prompt updated.")

    @prompt_group.command(name="reset")
    @commands.is_owner()
    async def prompt_reset(self, ctx: commands.Context) -> None:
        """Reset animator's system prompt to the default."""

        await self._repository.reset_system_prompt()
        await self._reply.send_reply(ctx, description="System prompt reset to default.")

    @prompt_group.command(name="show")
    @commands.is_owner()
    async def prompt_show(self, ctx: commands.Context) -> None:
        """Show animator's current system prompt."""

        settings = await self._repository.global_settings()
        await self._reply.send_reply(ctx, title="System Prompt", description=settings.system_prompt)

    @animator_group.command(name="status")
    async def status(self, ctx: commands.Context) -> None:
        """Show animator's current settings."""

        settings = await self._repository.global_settings()
        llm_settings: Any = await self._corridor.llm_settings()
        mcp_tool_count = len(await self._corridor.list_agent_tools_for("animator"))
        fields = [
            ReplyField("LLM Endpoint", llm_settings.llm_base_url, False),
            ReplyField("LLM Model", llm_settings.llm_model or "*(not set)*"),
            ReplyField("LLM Key", _MASKED_KEY if llm_settings.llm_api_key else "*(not set)*"),
            ReplyField("Max Tool Calls", str(settings.max_tool_calls)),
            ReplyField(
                "Request Timeout",
                "default (corridor's shared setting)"
                if settings.request_timeout_seconds is None
                else f"{settings.request_timeout_seconds:g}s",
                False,
            ),
            ReplyField(
                "Read Timeout",
                "default (wait indefinitely)"
                if settings.read_timeout_seconds is None
                else f"{settings.read_timeout_seconds:g}s",
                False,
            ),
            ReplyField("Debug Logging", "on" if settings.debug_logging else "off"),
            ReplyField(
                "A2A Registration",
                "✅ registered with corridor's shared listener"
                if "animator" in {agent.agent_key for agent in self._corridor.list_agents()}
                else "⚠️ not registered",
                False,
            ),
            ReplyField(
                "pixel-art-mcp tools",
                f"{mcp_tool_count} available"
                if mcp_tool_count
                else "⚠️ none -- see [p]telephonepole add/agents",
                False,
            ),
        ]
        await self._reply.send_reply(ctx, title="Animator Status", fields=fields)


__all__ = ["CommandsMixin"]
