"""Discord-facing commands. Thin: translate ctx <-> service calls only.

Unlike every other cog here, replies use plain `ctx.send` rather than
corridor's `send_reply`: switchboard exists to install and load corridor,
so it must work while corridor cannot import at all. That is also why this
package is not in contracts/discord_replies/lint_reply_channel.py.
"""

from __future__ import annotations

from typing import Any

from redbot.core import commands
from redbot.core.utils.chat_formatting import box, pagify

from ..application import SwitchboardService
from . import rendering


class CommandsMixin:
    """Requires `self._service: SwitchboardService` (provided by CogBase)."""

    _service: SwitchboardService

    @commands.group(name="switchboard")
    @commands.is_owner()
    async def switchboard_group(self, ctx: commands.Context) -> None:
        """Install requirements for addpath cogs and load/unload cogs in dependency order.

        Cogs are given as names, `all` (every cog on an addpath or --cog-path
        directory), or a directory name such as `office-cogs`.
        """

    @switchboard_group.command(name="status")
    async def cmd_status(self, ctx: commands.Context, *cogs: str) -> None:
        """Show where cogs come from, whether they are loaded, and missing requirements.

        With no cogs, shows every cog that is not one of Red's own.
        """

        await _send_boxed(ctx, rendering.status_table(await self._service.status(cogs)))

    @switchboard_group.command(name="install")
    async def cmd_install(self, ctx: commands.Context, *cogs: str) -> None:
        """Install the requirements of cogs and of every cog they require.

        Requirements go to the same directory Downloader uses.
        """

        if not cogs:
            await ctx.send_help()
            return
        async with ctx.typing():
            report = await self._service.install(cogs)
        await _send_boxed(ctx, rendering.install_text(report))

    @switchboard_group.command(name="load")
    async def cmd_load(self, ctx: commands.Context, *cogs: str) -> None:
        """Load cogs and every cog they require, dependencies first.

        Installs missing requirements first. Loaded cogs become managed: if
        autorepair is on, switchboard loads them again after a restart.
        """

        if not cogs:
            await ctx.send_help()
            return
        async with ctx.typing():
            report = await self._service.load(cogs)
        await _send_boxed(ctx, rendering.load_text(report))

    @switchboard_group.command(name="unload")
    async def cmd_unload(self, ctx: commands.Context, *cogs: str) -> None:
        """Unload cogs and every loaded cog that requires them, dependents first.

        Unloaded cogs stop being managed.
        """

        if not cogs:
            await ctx.send_help()
            return
        report = await self._service.unload(cogs)
        await _send_boxed(ctx, rendering.unload_text(report))

    @switchboard_group.command(name="reload")
    async def cmd_reload(self, ctx: commands.Context, *cogs: str) -> None:
        """Unload cogs (dependents first), then load them again (dependencies first)."""

        if not cogs:
            await ctx.send_help()
            return
        async with ctx.typing():
            unloaded, loaded = await self._service.reload(cogs)
        await _send_boxed(
            ctx, rendering.unload_text(unloaded) + "\n\n" + rendering.load_text(loaded)
        )

    @switchboard_group.command(name="repair")
    async def cmd_repair(self, ctx: commands.Context) -> None:
        """Load every managed cog that is not loaded, installing requirements first."""

        async with ctx.typing():
            report = await self._service.repair()
        if report is None:
            await ctx.send("Autorepair is off. Turn it on with `switchboard autorepair true`.")
            return
        await _send_boxed(ctx, rendering.load_text(report))

    @switchboard_group.command(name="autorepair")
    async def cmd_autorepair(self, ctx: commands.Context, enabled: bool | None = None) -> None:
        """Show or set whether managed cogs are repaired once the bot is ready."""

        if enabled is not None:
            await self._service.set_autorepair(enabled)
        state = "on" if await self._service.autorepair() else "off"
        await ctx.send(f"Autorepair is {state}.")

    @switchboard_group.command(name="managed")
    async def cmd_managed(self, ctx: commands.Context) -> None:
        """List the cogs switchboard keeps loaded."""

        managed = await self._service.managed()
        await ctx.send(
            "Managed cogs: " + ", ".join(f"`{name}`" for name in managed)
            if managed
            else "No managed cogs. `switchboard load <cogs>` adds them."
        )


async def _send_boxed(ctx: Any, text: str) -> None:
    for page in pagify(text, shorten_by=12):
        await ctx.send(box(page))
