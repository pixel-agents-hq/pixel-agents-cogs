"""Discord-facing commands. Thin: translate ctx <-> repository/service calls
only.

Every command here is bot-owner-only -- this is bot-wide capability
configuration (which third-party MCP server exists, which registered agent
may use it), not guild content, same rationale
`suggestionbox/adapters/commands.py` documents for its own MCP
configuration commands.

Replies go through corridor (this cog's `required_cogs` dependency) rather
than `ctx.send()`/hand-rolled role checks -- except `agents`, whose
Components V2 panel is sent via a plain `ctx.send(view=...)`, the same
lint-exempt convention `suggestionbox`'s own `agents` command uses
(Components V2 cannot be mixed with an embed/content, so it structurally
cannot honor `ReplyMode`).
"""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from ..application import TelephonepoleService
from ..infrastructure import RedTelephonepoleRepository
from .agent_access_panel import AgentAccessView


class CommandsMixin:
    """Requires `self._repository: RedTelephonepoleRepository`,
    `self._service: TelephonepoleService | None`, `self._corridor`,
    `self._reply`, and `self.list_agents` (all provided by CogBase)."""

    _repository: RedTelephonepoleRepository
    _service: TelephonepoleService | None
    _corridor: Any
    _reply: Any

    @commands.hybrid_group(name="telephonepole")
    @commands.is_owner()
    async def telephonepole_group(self, ctx: commands.Context) -> None:
        """Dynamically registers third-party MCP servers for registered A2A agents."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @telephonepole_group.command(name="add")
    @commands.is_owner()
    async def add(self, ctx: commands.Context, name: str, base_url: str) -> None:
        """Register a third-party MCP server's Streamable HTTP endpoint under `name`."""

        assert self._service is not None, "telephonepole: cog_load has not completed yet"
        error = await self._service.add_server(name, base_url)
        if error is not None:
            await self._reply.send_reply(
                ctx, title="Add MCP server", description=f"Could not register `{name}`: {error}"
            )
            return
        await self._reply.send_reply(
            ctx,
            title="Add MCP server",
            description=(
                f"Registered `{name}` at `{base_url}`. Grant a registered agent access with "
                f"`[p]telephonepole agents {name}`."
            ),
        )

    @telephonepole_group.command(name="remove")
    @commands.is_owner()
    async def remove(self, ctx: commands.Context, name: str) -> None:
        """Unregister a previously-added third-party MCP server."""

        assert self._service is not None, "telephonepole: cog_load has not completed yet"
        error = await self._service.remove_server(name)
        if error is not None:
            await self._reply.send_reply(ctx, title="Remove MCP server", description=error)
            return
        await self._reply.send_reply(
            ctx, title="Remove MCP server", description=f"Removed `{name}`."
        )

    @telephonepole_group.command(name="list")
    @commands.is_owner()
    async def list_servers(self, ctx: commands.Context) -> None:
        """List every registered third-party MCP server."""

        servers = await self._repository.list_servers()
        if not servers:
            await self._reply.send_reply(
                ctx,
                title="MCP servers",
                description="No third-party MCP servers are registered yet.",
            )
            return
        description = "\n".join(f"`{server.name}` -> `{server.base_url}`" for server in servers)
        await self._reply.send_reply(ctx, title="MCP servers", description=description)

    @telephonepole_group.command(name="agents")
    @commands.is_owner()
    async def agents(self, ctx: commands.Context, name: str) -> None:
        """Open the per-agent access panel for one registered MCP server."""

        server = await self._repository.get_server(name)
        if server is None:
            await self._reply.send_reply(
                ctx, title="MCP servers", description=f"No server named `{name}` is registered."
            )
            return
        view = await AgentAccessView.create(self, server_name=name, owner_id=ctx.author.id)
        await ctx.send(view=view)
