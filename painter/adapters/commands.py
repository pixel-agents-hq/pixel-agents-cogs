"""Discord-facing commands. Thin: translate ctx <-> repository calls only.

Replies go through corridor (this cog's required-cogs dependency), never a
raw ctx.send(), so this cog respects the configured reply style. All settings
are bot-owner scoped: painter is A2A-only and process-scoped. Browser hosting
belongs to CCTV; painter changes the editor aggregate through pixelagents.
"""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from corridor.domain import OfficeStateKind, ReplyField

from ..infrastructure import RedPainterRepository

_MASKED_KEY = "•" * 8


class CommandsMixin:
    """Requires the repositories and cross-cog references from CogBase."""

    _repository: RedPainterRepository
    _corridor: Any
    _pixelagents: Any
    _reply: Any

    @commands.hybrid_group(name="painter", invoke_without_command=True)
    async def painter_group(self, ctx: commands.Context) -> None:
        """Manage painter's tool-calling settings."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @painter_group.command(name="maxtoolcalls")
    @commands.is_owner()
    async def maxtoolcalls(self, ctx: commands.Context, count: int) -> None:
        """Set the max tool calls painter may make per A2A turn."""

        try:
            await self._repository.set_max_tool_calls(count)
        except ValueError as exc:
            await self._reply.send_reply(ctx, description=str(exc))
            return
        await self._reply.send_reply(ctx, description=f"Max tool calls per turn set to `{count}`.")

    @painter_group.command(name="debuglogging")
    @commands.is_owner()
    async def debug_logging(self, ctx: commands.Context, enabled: bool) -> None:
        """Enable or disable verbose per-tool-call logging (tool name,
        arguments, and result/error for every call the LLM makes this
        turn), logged at INFO under the `red.painter` logger. Off by
        default -- turn on only while diagnosing a tool-calling issue."""

        await self._repository.set_debug_logging(enabled)
        await self._reply.send_reply(
            ctx, description=f"Debug logging {'enabled' if enabled else 'disabled'}."
        )

    @painter_group.group(name="prompt", invoke_without_command=True)
    @commands.is_owner()
    async def prompt_group(self, ctx: commands.Context) -> None:
        """Manage painter's system prompt. Bot owner only."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @prompt_group.command(name="set")
    @commands.is_owner()
    async def prompt_set(self, ctx: commands.Context, *, text: str) -> None:
        """Set painter's system prompt."""

        await self._repository.set_system_prompt(text)
        await self._reply.send_reply(ctx, description="System prompt updated.")

    @prompt_group.command(name="reset")
    @commands.is_owner()
    async def prompt_reset(self, ctx: commands.Context) -> None:
        """Reset painter's system prompt to the default."""

        await self._repository.reset_system_prompt()
        await self._reply.send_reply(ctx, description="System prompt reset to default.")

    @prompt_group.command(name="show")
    @commands.is_owner()
    async def prompt_show(self, ctx: commands.Context) -> None:
        """Show painter's current system prompt."""

        settings = await self._repository.global_settings()
        await self._reply.send_reply(ctx, title="System Prompt", description=settings.system_prompt)

    @painter_group.command(name="status")
    async def status(self, ctx: commands.Context) -> None:
        """Show painter's current settings."""

        settings = await self._repository.global_settings()
        llm_settings: Any = await self._corridor.llm_settings()
        try:
            editor_state = await self._pixelagents.office_state(OfficeStateKind.EDITOR)
            editor_status = f"✅ revision {editor_state.revision}"
        except (RuntimeError, TypeError, ValueError) as exc:
            editor_status = f"⚠️ unavailable: {exc}"
        fields = [
            ReplyField("LLM Endpoint", llm_settings.llm_base_url, False),
            ReplyField("LLM Model", llm_settings.llm_model or "*(not set)*"),
            ReplyField("LLM Key", _MASKED_KEY if llm_settings.llm_api_key else "*(not set)*"),
            ReplyField("Max Tool Calls", str(settings.max_tool_calls)),
            ReplyField("Debug Logging", "on" if settings.debug_logging else "off"),
            ReplyField(
                "A2A Registration",
                "✅ registered with corridor's shared listener"
                if "painter" in {agent.agent_key for agent in self._corridor.list_agents()}
                else "⚠️ not registered",
                False,
            ),
            ReplyField(
                "Editor Aggregate",
                editor_status,
                False,
            ),
        ]
        await self._reply.send_reply(ctx, title="Painter Status", fields=fields)


__all__ = ["CommandsMixin"]
