"""Discord-facing commands. Thin: translate ctx <-> repository calls only.

Replies go through corridor (this cog's required_cogs dependency), never a
raw ctx.send(), so this cog respects whatever reply style a guild has
configured. `[p]architect ws ...`/`maxtoolcalls`/`prompt ...` are bot-owner
scope -- architect's office WebSocket server and webview are process-scoped,
not per-guild, so unlike pico there is no `[p]architect enabled` toggle
(see docs/architect-design.md section 6). architect's former `[p]architect
a2a ...` group is gone -- its A2A listener now lives on corridor's own
shared one, configured via `[p]corridor a2a ...`
(see docs/agent-directory-design.md).
"""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from corridor.domain import ReplyField

from ..infrastructure import RedArchitectRepository

_MASKED_KEY = "•" * 8


class CommandsMixin:
    """Requires `self._repository: RedArchitectRepository`, `self._corridor`,
    and `self._websocket_server` (all provided by CogBase). Unlike
    corridor's shared A2A listener, the office WebSocket server is not
    live-restarted on a host/port change -- same "reload the cog to
    rebind" convention floorplan's own `[p]floorplan wsport` already
    uses, since rebinding a socket server out from under already-connected
    clients is riskier than an explicit reload."""

    _repository: RedArchitectRepository
    _corridor: Any
    _websocket_server: Any

    @commands.hybrid_group(name="architect", invoke_without_command=True)
    async def architect_group(self, ctx: commands.Context) -> None:
        """Manage architect's tool-calling and A2A listener settings."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @architect_group.group(name="ws", invoke_without_command=True)
    @commands.is_owner()
    async def ws_group(self, ctx: commands.Context) -> None:
        """Configure architect's own office WebSocket server. Bot owner only."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @ws_group.command(name="host")
    @commands.is_owner()
    async def ws_host(self, ctx: commands.Context, host: str) -> None:
        """Set the office WebSocket server's bind host. Reload the cog to rebind."""

        await self._repository.set_ws_host(host)
        await self._corridor.send_reply(
            ctx,
            description=(
                f"WebSocket host set to `{host}`. Reload the cog to rebind, and update your "
                "reverse-proxy route to match."
            ),
        )

    @ws_group.command(name="port")
    @commands.is_owner()
    async def ws_port(self, ctx: commands.Context, port: int) -> None:
        """Set the office WebSocket server's bind port. Reload the cog to rebind."""

        try:
            await self._repository.set_ws_port(port)
        except ValueError as exc:
            await self._corridor.send_reply(ctx, description=str(exc))
            return
        await self._corridor.send_reply(
            ctx,
            description=(
                f"WebSocket port set to `{port}`. Reload the cog to rebind, and update your "
                "reverse-proxy route to match."
            ),
        )

    @architect_group.command(name="maxtoolcalls")
    @commands.is_owner()
    async def maxtoolcalls(self, ctx: commands.Context, count: int) -> None:
        """Set the max tool calls architect may make per A2A turn."""

        try:
            await self._repository.set_max_tool_calls(count)
        except ValueError as exc:
            await self._corridor.send_reply(ctx, description=str(exc))
            return
        await self._corridor.send_reply(
            ctx, description=f"Max tool calls per turn set to `{count}`."
        )

    @architect_group.command(name="debuglogging")
    @commands.is_owner()
    async def debug_logging(self, ctx: commands.Context, enabled: bool) -> None:
        """Enable or disable verbose per-tool-call logging (tool name,
        arguments, and result/error for every call the LLM makes this
        turn), logged at INFO under the `red.architect` logger. Off by
        default -- turn on only while diagnosing a tool-calling issue."""

        await self._repository.set_debug_logging(enabled)
        await self._corridor.send_reply(
            ctx, description=f"Debug logging {'enabled' if enabled else 'disabled'}."
        )

    @architect_group.group(name="prompt", invoke_without_command=True)
    @commands.is_owner()
    async def prompt_group(self, ctx: commands.Context) -> None:
        """Manage architect's system prompt. Bot owner only."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @prompt_group.command(name="set")
    @commands.is_owner()
    async def prompt_set(self, ctx: commands.Context, *, text: str) -> None:
        """Set architect's system prompt."""

        await self._repository.set_system_prompt(text)
        await self._corridor.send_reply(ctx, description="System prompt updated.")

    @prompt_group.command(name="reset")
    @commands.is_owner()
    async def prompt_reset(self, ctx: commands.Context) -> None:
        """Reset architect's system prompt to the default."""

        await self._repository.reset_system_prompt()
        await self._corridor.send_reply(ctx, description="System prompt reset to default.")

    @prompt_group.command(name="show")
    @commands.is_owner()
    async def prompt_show(self, ctx: commands.Context) -> None:
        """Show architect's current system prompt."""

        settings = await self._repository.global_settings()
        await self._corridor.send_reply(
            ctx, title="System Prompt", description=settings.system_prompt
        )

    @architect_group.command(name="status")
    async def status(self, ctx: commands.Context) -> None:
        """Show architect's current settings."""

        settings = await self._repository.global_settings()
        llm_settings: Any = await self._corridor.llm_settings()
        await self._sync_webview_assets()  # type: ignore[attr-defined]
        layout = await self._repository.layout()
        fields = [
            ReplyField("LLM Endpoint", llm_settings.llm_base_url, False),
            ReplyField("LLM Model", llm_settings.llm_model or "*(not set)*"),
            ReplyField("LLM Key", _MASKED_KEY if llm_settings.llm_api_key else "*(not set)*"),
            ReplyField("Max Tool Calls", str(settings.max_tool_calls)),
            ReplyField("Debug Logging", "on" if settings.debug_logging else "off"),
            ReplyField(
                "A2A Registration",
                "✅ registered with corridor's shared listener"
                if "architect" in {agent.agent_key for agent in self._corridor.list_agents()}
                else "⚠️ not registered",
                False,
            ),
            ReplyField(
                "Office WebSocket",
                f"{settings.ws_host}:{settings.ws_port} "
                f"({'running' if self._websocket_server.running else 'not running'})",
                False,
            ),
            ReplyField("Webview", self._webview_assets_status(), False),  # type: ignore[attr-defined]
            ReplyField(
                "Layout",
                "✅ seeded (own copy, independent of floorplan)" if layout else "⚠️ not seeded yet",
                False,
            ),
        ]
        await self._corridor.send_reply(ctx, title="Architect Status", fields=fields)


__all__ = ["CommandsMixin"]
