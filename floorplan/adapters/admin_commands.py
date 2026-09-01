"""Floorplan's Pixel Index-only command root and status."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from redbot.core import commands

from corridor.domain import ReplyField

from .cog_base import FloorplanBase


class AdminCommandsMixin(FloorplanBase):
    @commands.hybrid_group(name="floorplan", invoke_without_command=True)
    async def floorplan_group(self, ctx: commands.Context) -> None:
        """Browse and load Pixel Index office layouts."""

        send_help: Callable[[], Awaitable[object]] = ctx.send_help
        await send_help()

    @floorplan_group.command(name="status")
    @commands.admin_or_permissions(administrator=True)
    async def cmd_status(self, ctx: commands.Context) -> None:
        """Show floorplan's configured Pixel Index endpoints and API health."""

        bases = await self._catalogue_service.bases()
        result = await self._catalogue_service.health(bases.api)
        health = result.value if result.error is None else result.error.message
        await self._reply(
            ctx,
            title="Floorplan Status",
            fields=(
                ReplyField("Pixel Index API", bases.api),
                ReplyField("Pixel Index Web", bases.web),
                ReplyField("API Health", str(health)),
            ),
        )


__all__ = ["AdminCommandsMixin"]
