"""Discord-facing commands. Thin: translate ctx <-> repository calls only.

Replies go through corridor (this cog's required_cogs dependency), never a
raw ctx.send(), so this cog respects whatever reply style a guild has
configured. `maxtoolcalls`/`prompt ...` (turn-budget/behavior settings) are
bot-owner scope; `enabled` is per-guild admin scope -- mirrors floorplan's
convention that only the connection itself, not turning the feature on for
a given server, is owner-gated (see `floorplan/adapters/admin_commands.py`'s
`[p]floorplan enable`). The LLM *connection* itself (`llm ...`) moved to
`[p]corridor llm ...` -- see docs/architect-design.md.
"""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from corridor.domain import ReplyField

from ..infrastructure import RedPicoRepository

_MASKED_KEY = "•" * 8


class CommandsMixin:
    """Requires `self._repository: RedPicoRepository` and `self._corridor`
    (both provided by CogBase)."""

    _repository: RedPicoRepository
    _corridor: Any

    @commands.hybrid_group(name="pico", invoke_without_command=True)
    async def pico_group(self, ctx: commands.Context) -> None:
        """Manage Pico's evaluation gate and tool-calling settings."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @pico_group.command(name="maxtoolcalls")
    @commands.is_owner()
    async def maxtoolcalls(self, ctx: commands.Context, count: int) -> None:
        """Set the max tool calls Pico may make per turn."""

        try:
            await self._repository.set_max_tool_calls(count)
        except ValueError as exc:
            await self._corridor.send_reply(ctx, description=str(exc))
            return
        await self._corridor.send_reply(
            ctx, description=f"Max tool calls per turn set to `{count}`."
        )

    @pico_group.group(name="prompt", invoke_without_command=True)
    @commands.is_owner()
    async def prompt_group(self, ctx: commands.Context) -> None:
        """Manage Pico's system prompt. Bot owner only."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @prompt_group.command(name="set")
    @commands.is_owner()
    async def prompt_set(self, ctx: commands.Context, *, text: str) -> None:
        """Set Pico's system prompt."""

        await self._repository.set_system_prompt(text)
        await self._corridor.send_reply(ctx, description="System prompt updated.")

    @prompt_group.command(name="reset")
    @commands.is_owner()
    async def prompt_reset(self, ctx: commands.Context) -> None:
        """Reset Pico's system prompt to the default."""

        await self._repository.reset_system_prompt()
        await self._corridor.send_reply(ctx, description="System prompt reset to default.")

    @prompt_group.command(name="show")
    @commands.is_owner()
    async def prompt_show(self, ctx: commands.Context) -> None:
        """Show Pico's current system prompt."""

        settings = await self._repository.global_settings()
        await self._corridor.send_reply(
            ctx, title="System Prompt", description=settings.system_prompt
        )

    @pico_group.command(name="architecturl")
    @commands.is_owner()
    async def architect_url(self, ctx: commands.Context, url: str) -> None:
        """Set architect's A2A base URL, enabling the consult_architect tool."""

        await self._repository.set_architect_url(url)
        await self._corridor.send_reply(ctx, description=f"Architect URL set to `{url}`.")

    @pico_group.command(name="enabled")
    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    async def enabled(self, ctx: commands.Context, value: bool) -> None:
        """Enable or disable Pico for this server. Admin only."""

        guild = ctx.guild
        assert guild is not None, "guild_only() guarantees this"
        await self._repository.set_guild_enabled(guild.id, value)
        await self._corridor.send_reply(
            ctx, description=f"Pico enabled set to `{value}` for this server."
        )

    @pico_group.command(name="status")
    async def status(self, ctx: commands.Context) -> None:
        """Show Pico's current settings."""

        settings = await self._repository.global_settings()
        llm_settings: Any = await self._corridor.llm_settings()
        fields = [
            ReplyField("LLM Endpoint", llm_settings.llm_base_url, False),
            ReplyField("LLM Model", llm_settings.llm_model or "*(not set)*"),
            ReplyField("LLM Key", _MASKED_KEY if llm_settings.llm_api_key else "*(not set)*"),
            ReplyField("Max Tool Calls", str(settings.max_tool_calls)),
            ReplyField("Architect URL", settings.architect_url or "*(not set)*", False),
        ]
        if ctx.guild is not None:
            guild_settings = await self._repository.guild_settings(ctx.guild.id)
            fields.append(ReplyField("Enabled (this server)", str(guild_settings.enabled)))
        await self._corridor.send_reply(ctx, title="Pico Status", fields=fields)


__all__ = ["CommandsMixin"]
