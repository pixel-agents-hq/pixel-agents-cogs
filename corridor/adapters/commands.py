"""corridor's own commands. Configuration is also reachable by mounting
build_shared_settings_container() inside another cog's settings panel --
this is the standalone entry point for guild admins who just want corridor's
panel directly.

The `[p]corridor llm ...` group configures the one LLM connection shared by
every LLM-backed dependent (pico, architect) -- moved here from pico's
former `[p]pico llm ...` group, see docs/architect-design.md. These replies
go through `self.send_reply`/`self.render_reply` directly (corridor is its
own renderer, so there's no `self._corridor` to reach through -- see
contracts/discord_replies/lint_reply_channel.py's `_CORRIDOR_REPLY_CALLS`)."""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from ..domain import ReplyField
from .settings_ui import SharedSettingsView

_MASKED_KEY = "•" * 8


class CommandsMixin:
    """Requires `self.guild_settings`, `self.llm_settings`, `self.send_reply`,
    `self.set_llm_base_url`/`set_llm_api_key`/`set_llm_model` (all provided
    by CogBase)."""

    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.hybrid_command(name="corridorsettings")
    async def corridor_settings(self, ctx: commands.Context) -> None:
        """Configure shared reply style and permission role tiers."""

        assert ctx.guild is not None
        settings = await self.guild_settings(ctx.guild.id)  # type: ignore[attr-defined]
        await ctx.send(view=SharedSettingsView(settings))

    @commands.hybrid_group(name="corridor", invoke_without_command=True)
    async def corridor_group(self, ctx: commands.Context) -> None:
        """Manage corridor's shared LLM connection."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @corridor_group.group(name="llm", invoke_without_command=True)
    @commands.is_owner()
    async def llm_group(self, ctx: commands.Context) -> None:
        """Configure the shared LLM connection pico and architect use. Bot owner only."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @llm_group.command(name="endpoint")
    @commands.is_owner()
    async def llm_endpoint(self, ctx: commands.Context, url: str) -> None:
        """Set the LiteLLM proxy base URL."""

        await self.set_llm_base_url(url)  # type: ignore[attr-defined]
        await self.send_reply(ctx, description=f"LLM endpoint set to `{url}`.")  # type: ignore[attr-defined]

    @llm_group.command(name="key")
    @commands.is_owner()
    async def llm_key(self, ctx: commands.Context, key: str) -> None:
        """Set the LiteLLM virtual key. Deletes your invoking message immediately."""

        await self.set_llm_api_key(key)  # type: ignore[attr-defined]
        try:
            await ctx.message.delete()
        except Exception:  # best-effort: missing perms/already-deleted must not block the update
            pass
        await self.send_reply(ctx, description="LLM virtual key updated.")  # type: ignore[attr-defined]

    @llm_group.command(name="model")
    @commands.is_owner()
    async def llm_model(self, ctx: commands.Context, model: str) -> None:
        """Set the model name passed to the LLM endpoint."""

        await self.set_llm_model(model)  # type: ignore[attr-defined]
        await self.send_reply(ctx, description=f"LLM model set to `{model}`.")  # type: ignore[attr-defined]

    @corridor_group.command(name="status")
    async def corridor_status(self, ctx: commands.Context) -> None:
        """Show the shared LLM connection's current settings."""

        settings: Any = await self.llm_settings()  # type: ignore[attr-defined]
        fields = [
            ReplyField("LLM Endpoint", settings.llm_base_url, False),
            ReplyField("LLM Model", settings.llm_model or "*(not set)*"),
            ReplyField("LLM Key", _MASKED_KEY if settings.llm_api_key else "*(not set)*"),
        ]
        await self.send_reply(ctx, title="Corridor LLM Status", fields=fields)  # type: ignore[attr-defined]


__all__ = ["CommandsMixin"]
