"""corridor's own commands. Configuration is also reachable by mounting
build_shared_settings_container() inside another cog's settings panel --
this is the standalone entry point for guild admins who just want corridor's
panel directly.

The `[p]corridor llm ...` group configures the one LLM connection shared by
every LLM-backed dependent (pico, architect) -- moved here from pico's
former `[p]pico llm ...` group, see docs/architect-design.md. The
`[p]corridor a2a ...` group configures corridor's one shared A2A listener,
used by every registered agent -- moved here from architect's former
`[p]architect a2a ...` group, see docs/agent-directory-design.md. These replies
go through `self.send_reply`/`self.render_reply` directly (corridor is its
own renderer, so there's no `self._corridor` to reach through -- see
contracts/discord_replies/lint_reply_channel.py's `_CORRIDOR_REPLY_CALLS`)."""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from ..domain import ReplyCategory, ReplyField
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
        await self.send_reply(  # type: ignore[attr-defined]
            ctx, description=f"LLM endpoint set to `{url}`.", category=ReplyCategory.ROOM
        )

    @llm_group.command(name="key")
    @commands.is_owner()
    async def llm_key(self, ctx: commands.Context, key: str) -> None:
        """Set the LiteLLM virtual key. Deletes your invoking message immediately."""

        await self.set_llm_api_key(key)  # type: ignore[attr-defined]
        try:
            await ctx.message.delete()
        except Exception:  # best-effort: missing perms/already-deleted must not block the update
            pass
        await self.send_reply(  # type: ignore[attr-defined]
            ctx, description="LLM virtual key updated.", category=ReplyCategory.ROOM
        )

    @llm_group.command(name="model")
    @commands.is_owner()
    async def llm_model(self, ctx: commands.Context, model: str) -> None:
        """Set the model name passed to the LLM endpoint."""

        await self.set_llm_model(model)  # type: ignore[attr-defined]
        await self.send_reply(  # type: ignore[attr-defined]
            ctx, description=f"LLM model set to `{model}`.", category=ReplyCategory.ROOM
        )

    @corridor_group.group(name="a2a", invoke_without_command=True)
    @commands.is_owner()
    async def a2a_group(self, ctx: commands.Context) -> None:
        """Configure corridor's one shared A2A listener, used by every
        registered agent (architect, and any future agent). Bot owner
        only. See docs/agent-directory-design.md."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @a2a_group.command(name="host")
    @commands.is_owner()
    async def a2a_host(self, ctx: commands.Context, host: str) -> None:
        """Set the A2A listener's bind host and restart it, re-mounting
        every already-registered agent."""

        error = await self.set_a2a_host(host)  # type: ignore[attr-defined]
        if error is not None:
            await self.send_reply(  # type: ignore[attr-defined]
                ctx,
                description=f"A2A listener host set to `{host}`, but it failed to start: {error}",
                category=ReplyCategory.ROOM,
            )
            return
        await self.send_reply(  # type: ignore[attr-defined]
            ctx, description=f"A2A listener host set to `{host}`.", category=ReplyCategory.ROOM
        )

    @a2a_group.command(name="port")
    @commands.is_owner()
    async def a2a_port(self, ctx: commands.Context, port: int) -> None:
        """Set the A2A listener's bind port and restart it, re-mounting
        every already-registered agent."""

        try:
            error = await self.set_a2a_port(port)  # type: ignore[attr-defined]
        except ValueError as exc:
            await self.send_reply(  # type: ignore[attr-defined]
                ctx, description=str(exc), category=ReplyCategory.ROOM
            )
            return
        if error is not None:
            await self.send_reply(  # type: ignore[attr-defined]
                ctx,
                description=f"A2A listener port set to `{port}`, but it failed to start: {error}",
                category=ReplyCategory.ROOM,
            )
            return
        await self.send_reply(  # type: ignore[attr-defined]
            ctx, description=f"A2A listener port set to `{port}`.", category=ReplyCategory.ROOM
        )

    @corridor_group.command(name="status")
    async def corridor_status(self, ctx: commands.Context) -> None:
        """Show the shared LLM connection and A2A listener's current settings."""

        settings: Any = await self.llm_settings()  # type: ignore[attr-defined]
        a2a: Any = await self.a2a_settings()  # type: ignore[attr-defined]
        agents = self.list_agents()  # type: ignore[attr-defined]
        fields = [
            ReplyField("LLM Endpoint", settings.llm_base_url, False),
            ReplyField("LLM Model", settings.llm_model or "*(not set)*"),
            ReplyField("LLM Key", _MASKED_KEY if settings.llm_api_key else "*(not set)*"),
            ReplyField(
                "A2A Listener",
                f"{a2a.a2a_host}:{a2a.a2a_port} "
                f"({'running' if self._a2a_server.running else 'not running'})",  # type: ignore[attr-defined]
                False,
            ),
            ReplyField(
                "Registered Agents",
                ", ".join(agent.agent_key for agent in agents) or "*(none)*",
                False,
            ),
        ]
        await self.send_reply(  # type: ignore[attr-defined]
            ctx, title="Corridor LLM Status", fields=fields, category=ReplyCategory.ROOM
        )


__all__ = ["CommandsMixin"]
