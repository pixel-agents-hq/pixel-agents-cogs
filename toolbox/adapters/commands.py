"""Discord-facing commands. Thin: translate ctx <-> service calls only.

Bot-owner only (`@commands.is_owner()`) throughout, not corridor's per-guild
`require_permission` tiers (Keyholder, Moderator, ...) or floorplan's
`admin_or_permissions(administrator=True)` pattern: installing/uninstalling
Node.js on the bot host affects every guild the bot serves, not just the
guild the command was run from, so a guild-scoped permission -- however
it's granted -- is the wrong tier for it regardless. Replies still go
through corridor (this cog's required_cogs dependency) so this cog respects
whatever reply style every other cog on the guild has already configured.
"""

from __future__ import annotations

from typing import Any

from redbot.core import commands

from ..application import NodeService, ToolSelectionService, ToolVisibilityService
from ..infrastructure import NodeInstallError
from .tool_candidates import list_candidate_commands
from .tool_panel import ToolGuildOverrideView, ToolSelectionView


class CommandsMixin:
    """Requires `self._service: NodeService`, `self._corridor`,
    `self._tool_selection_service`, and `self._tool_visibility_service`
    (all provided by CogBase)."""

    _service: NodeService
    _corridor: Any
    _reply: Any
    _tool_selection_service: ToolSelectionService
    _tool_visibility_service: ToolVisibilityService

    @commands.hybrid_group(name="toolbox")
    @commands.guild_only()
    @commands.is_owner()
    async def toolbox_group(self, ctx: commands.Context) -> None:
        """Install and manage development tooling on the bot host."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @toolbox_group.group(name="node", invoke_without_command=True)
    @commands.guild_only()
    @commands.is_owner()
    async def node_group(self, ctx: commands.Context) -> None:
        """Manage the Node.js runtime installed on the bot host."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @node_group.command(name="install")
    @commands.guild_only()
    @commands.is_owner()
    async def node_install(self, ctx: commands.Context, version: str | None = None) -> None:
        """Install Node.js (and its bundled npm) onto the bot host.

        Defaults to the latest Node.js 22.x LTS release. Pass an explicit
        `version` (e.g. `20.17.0`) to install a different one -- re-running
        this with a different version switches the active install.
        """

        target = f" {version}" if version else " the default LTS version"
        await self._reply.send_reply(
            ctx, title="toolbox", description=f"Installing Node.js{target}…"
        )
        try:
            installation = await self._service.install(version)
        except NodeInstallError as exc:
            await self._reply.send_reply(ctx, title="toolbox", description=f"⚠️ {exc}")
            return
        await self._reply.send_reply(
            ctx, title="toolbox", description=f"✅ Installed Node.js {installation.version}."
        )

    @node_group.command(name="uninstall")
    @commands.guild_only()
    @commands.is_owner()
    async def node_uninstall(self, ctx: commands.Context) -> None:
        """Remove the Node.js this cog installed from the bot host."""

        installation = await self._service.uninstall()
        if installation is None:
            await self._reply.send_reply(
                ctx, title="toolbox", description="Node.js is not installed."
            )
            return
        await self._reply.send_reply(
            ctx, title="toolbox", description=f"✅ Uninstalled Node.js {installation.version}."
        )

    @node_group.command(name="version", aliases=["status"])
    @commands.guild_only()
    @commands.is_owner()
    async def node_version(self, ctx: commands.Context) -> None:
        """Show the Node.js version this cog has installed, if any."""

        status = await self._service.status()
        if not status.installed:
            await self._reply.send_reply(
                ctx, title="toolbox", description="Node.js is not installed."
            )
            return
        await self._reply.send_reply(
            ctx,
            title="toolbox",
            description=f"Node.js {status.version} (`{status.install_dir}`)",
        )

    @toolbox_group.group(name="tools", invoke_without_command=True)
    @commands.guild_only()
    @commands.is_owner()
    async def tools_group(self, ctx: commands.Context, search: str | None = None) -> None:
        """Choose which Discord commands are exposed to the LLM as tools.

        Every command listed in `[p]help` you can run is a candidate --
        select one to turn it into an LLM tool, or toggle whether an
        already-tool-eligible command is enabled by default. Pass `search`
        to only list candidates whose name contains it. Use
        `[p]toolbox tools guild` to override visibility for this server
        only.
        """

        if ctx.invoked_subcommand is not None:
            return
        selected = await self._tool_selection_service.list_selected()
        candidates = await list_candidate_commands(ctx.bot.walk_commands(), ctx, selected, search)
        enabled_defaults = await self._tool_visibility_service.all_defaults()
        await ctx.send(view=ToolSelectionView(candidates, 0, ctx.author.id, enabled_defaults))

    @tools_group.command(name="guild")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def tools_guild(self, ctx: commands.Context) -> None:
        """Override LLM tool visibility for this server only.

        Lists every currently registered tool -- from any cog, not just
        ones selected here -- and lets you enable/disable each one for
        this guild specifically, on top of the bot owner's global default.
        """

        assert ctx.guild is not None
        tool_names = sorted(tool.name for tool in self._corridor.list_tools())
        defaults = await self._tool_visibility_service.all_defaults()
        overrides = await self._tool_visibility_service.all_overrides(ctx.guild.id)
        await ctx.send(
            view=ToolGuildOverrideView(
                tool_names, 0, ctx.guild.id, ctx.author.id, defaults, overrides
            )
        )
