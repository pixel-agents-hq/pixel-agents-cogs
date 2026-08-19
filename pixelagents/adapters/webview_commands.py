"""Commands for overriding which Pixel Agents commit the webview builds from.

Split out of `admin_commands.py` to keep that file under the adapter size
limit `test_architecture.py` enforces -- see `catalogue_commands.py` for the
same pattern with the `index`/`layout` groups.
"""

from __future__ import annotations

from discord import app_commands
from redbot.core import commands

from ..infrastructure.webview_build import pinned_commit
from .admin_commands import AdminCommandsMixin
from .cog_base import PixelAgentsBase


class WebviewCommandsMixin(PixelAgentsBase):
    """Read-for-anyone, write-for-owner-only webview commit override commands."""

    @AdminCommandsMixin.cmd_webview.command(name="commit")
    async def cmd_webview_commit(self, ctx: commands.Context) -> None:
        """Show which Pixel Agents commit the webview builds from."""

        default = pinned_commit()
        override = await self._settings_service.webview_commit_override()
        if override:
            await self._reply(
                ctx,
                f"Building from custom commit `{override}` "
                f"(default pin is `{default}`). "
                "Use `[p]pixelagents webview resetcommit` to revert.",
            )
        else:
            await self._reply(ctx, f"Building from the default pinned commit `{default}`.")

    @AdminCommandsMixin.cmd_webview.command(name="setcommit")
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
            clean = await self._settings_service.set_webview_commit_override(commit)
        except ValueError as exc:
            await self._reply(ctx, str(exc))
            return
        await self._reply(
            ctx,
            f"Webview builds will use commit `{clean}`. "
            "Run `[p]pixelagents webview rebuild` to build it now.",
        )

    @AdminCommandsMixin.cmd_webview.command(name="resetcommit")
    @commands.is_owner()
    async def cmd_webview_resetcommit(self, ctx: commands.Context) -> None:
        """Revert webview builds to the default pinned Pixel Agents commit."""

        await self._settings_service.reset_webview_commit_override()
        await self._reply(
            ctx,
            f"Webview builds will use the default pinned commit `{pinned_commit()}`. "
            "Run `[p]pixelagents webview rebuild` to build it now.",
        )
