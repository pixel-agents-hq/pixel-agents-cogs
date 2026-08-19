"""Discord-facing commands. Thin: translate ctx <-> service calls only.

Bot-owner only (`@commands.is_owner()`) throughout, not corridor's per-guild
`require_permission` tiers (Keyholder, Moderator, ...) or pixelagents'
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

from ..application import NodeService
from ..infrastructure import NodeInstallError


class CommandsMixin:
    """Requires `self._service: NodeService` and `self._corridor`
    (both provided by CogBase)."""

    _service: NodeService
    _corridor: Any

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
        await self._corridor.send_reply(
            ctx, title="toolbox", description=f"Installing Node.js{target}…"
        )
        try:
            installation = await self._service.install(version)
        except NodeInstallError as exc:
            await self._corridor.send_reply(ctx, title="toolbox", description=f"⚠️ {exc}")
            return
        await self._corridor.send_reply(
            ctx, title="toolbox", description=f"✅ Installed Node.js {installation.version}."
        )

    @node_group.command(name="uninstall")
    @commands.guild_only()
    @commands.is_owner()
    async def node_uninstall(self, ctx: commands.Context) -> None:
        """Remove the Node.js this cog installed from the bot host."""

        installation = await self._service.uninstall()
        if installation is None:
            await self._corridor.send_reply(
                ctx, title="toolbox", description="Node.js is not installed."
            )
            return
        await self._corridor.send_reply(
            ctx, title="toolbox", description=f"✅ Uninstalled Node.js {installation.version}."
        )

    @node_group.command(name="version", aliases=["status"])
    @commands.guild_only()
    @commands.is_owner()
    async def node_version(self, ctx: commands.Context) -> None:
        """Show the Node.js version this cog has installed, if any."""

        status = await self._service.status()
        if not status.installed:
            await self._corridor.send_reply(
                ctx, title="toolbox", description="Node.js is not installed."
            )
            return
        await self._corridor.send_reply(
            ctx,
            title="toolbox",
            description=f"Node.js {status.version} (`{status.install_dir}`)",
        )
