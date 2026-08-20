"""Pixel Agents command surface: vendoring and building the webview.

Bot-owner only throughout (`@commands.is_owner()`), not a per-guild
permission tier: mirrors toolbox's reasoning exactly (see
toolbox/adapters/commands.py) -- the webview build is one shared artifact on
the bot host that every guild's floorplan instance reads, not per-guild
data, so a guild-scoped permission would be the wrong tier for it regardless
of how it's granted. `guild_only()` stays on every command anyway, purely so
`ctx.guild` is available for corridor's guild-scoped reply rendering (see
`.replies.ReplyMixin`), the same reason toolbox keeps it despite also being
bot-owner-gated.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from discord import app_commands
from redbot.core import commands

from ..infrastructure.webview_build import DEFAULT_BASE_PATH, pinned_commit
from .cog_base import PixelAgentsBase


class CommandsMixin(PixelAgentsBase):
    """The whole Pixel Agents command surface: build the webview, pin its commit."""

    @commands.hybrid_group(name="pixelagents", invoke_without_command=True)
    @commands.guild_only()
    @commands.is_owner()
    async def pixelagents_group(self, ctx: commands.Context) -> None:
        """Manage the vendored Pixel Agents webview build."""

        send_help: Callable[[], Awaitable[object]] = ctx.send_help
        await send_help()

    @pixelagents_group.group(name="webview", invoke_without_command=True)
    @commands.guild_only()
    @commands.is_owner()
    async def cmd_webview(self, ctx: commands.Context) -> None:
        """Manage the built Pixel Agents webview assets."""

        send_help: Callable[[], Awaitable[object]] = ctx.send_help
        await send_help()

    @cmd_webview.command(name="rebuild")
    @commands.guild_only()
    @commands.is_owner()
    async def cmd_webview_rebuild(self, ctx: commands.Context) -> None:
        """Re-clone and rebuild the webview from the pinned Pixel Agents commit.

        Runs the same routine `cog_load` runs automatically -- useful after
        installing a missing build tool, or to pick up a pin bump without a
        full cog reload.
        """

        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)
        await self._reply(ctx, "Rebuilding the Pixel Agents webview…")
        await self._reply(ctx, await self._rebuild_webview(force=True))

    @cmd_webview.command(name="commit")
    @commands.guild_only()
    @commands.is_owner()
    async def cmd_webview_commit(self, ctx: commands.Context) -> None:
        """Show which Pixel Agents commit the webview builds from."""

        default = pinned_commit()
        override = await self._settings_repository.webview_commit_override()
        if override:
            await self._reply(
                ctx,
                f"Building from custom commit `{override}` "
                f"(default pin is `{default}`). "
                "Use `[p]pixelagents webview resetcommit` to revert.",
            )
        else:
            await self._reply(ctx, f"Building from the default pinned commit `{default}`.")

    @cmd_webview.command(name="setcommit")
    @commands.guild_only()
    @commands.is_owner()
    @app_commands.describe(
        commit="A Pixel Agents commit hash, or a link to it "
        "(e.g. https://github.com/pixel-agents-hq/pixel-agents/tree/<hash>)"
    )
    async def cmd_webview_setcommit(self, ctx: commands.Context, commit: str) -> None:
        """Pin webview builds to a specific Pixel Agents commit.

        `commit` is a commit hash from
        https://github.com/pixel-agents-hq/pixel-agents, or a direct link to
        it (e.g. https://github.com/pixel-agents-hq/pixel-agents/tree/<hash>).
        """

        try:
            clean = await self._settings_repository.set_webview_commit_override(commit)
        except ValueError as exc:
            await self._reply(ctx, str(exc))
            return
        await self._reply(
            ctx,
            f"Webview builds will use commit `{clean}`. "
            "Run `[p]pixelagents webview rebuild` to build it now.",
        )

    @cmd_webview.command(name="resetcommit")
    @commands.guild_only()
    @commands.is_owner()
    async def cmd_webview_resetcommit(self, ctx: commands.Context) -> None:
        """Revert webview builds to the default pinned Pixel Agents commit."""

        await self._settings_repository.reset_webview_commit_override()
        await self._reply(
            ctx,
            f"Webview builds will use the default pinned commit `{pinned_commit()}`. "
            "Run `[p]pixelagents webview rebuild` to build it now.",
        )

    @cmd_webview.command(name="basepath")
    @commands.guild_only()
    @commands.is_owner()
    async def cmd_webview_basepath(self, ctx: commands.Context) -> None:
        """Show which Dashboard route the webview builds asset URLs for.

        pixelagents only builds the bundle; whichever cog registers this
        path as its own Red Dashboard third-party route is the one that
        actually has to serve it (floorplan by default) -- see Architecture.md.
        """

        override = await self._settings_repository.webview_base_path()
        if override:
            await self._reply(
                ctx,
                f"Building for `{override}` "
                f"(default is `{DEFAULT_BASE_PATH}`). "
                "Use `[p]pixelagents webview resetbasepath` to revert.",
            )
        else:
            await self._reply(ctx, f"Building for the default path `{DEFAULT_BASE_PATH}`.")

    @cmd_webview.command(name="setbasepath")
    @commands.guild_only()
    @commands.is_owner()
    @app_commands.describe(
        base_path="The serving cog's Dashboard route, e.g. /third-party/<cog>/static/"
    )
    async def cmd_webview_setbasepath(self, ctx: commands.Context, base_path: str) -> None:
        """Point webview builds at a different cog's Dashboard route.

        Only needed if something other than floorplan is serving the built
        bundle -- an absolute path with a leading and trailing slash, e.g.
        `/third-party/<cog>/static/`.
        """

        try:
            clean = await self._settings_repository.set_webview_base_path(base_path)
        except ValueError as exc:
            await self._reply(ctx, str(exc))
            return
        await self._reply(
            ctx,
            f"Webview builds will target `{clean}`. "
            "Run `[p]pixelagents webview rebuild` to build it now.",
        )

    @cmd_webview.command(name="resetbasepath")
    @commands.guild_only()
    @commands.is_owner()
    async def cmd_webview_resetbasepath(self, ctx: commands.Context) -> None:
        """Revert webview builds to the default (floorplan's) Dashboard route."""

        await self._settings_repository.reset_webview_base_path()
        await self._reply(
            ctx,
            f"Webview builds will target the default path `{DEFAULT_BASE_PATH}`. "
            "Run `[p]pixelagents webview rebuild` to build it now.",
        )
