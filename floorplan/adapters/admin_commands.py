"""The `[p]floorplan` command group root.

Every command that used to live here (status/settings/wsport/
toolcleardelay/richpresence/messages/enable/disable/includebots/sync/
despawnall) moved to `[p]cctv` (docs/cctv-design.md) -- this mixin now
only defines the parent group `catalogue_commands.py`'s `index`/`layout`
subgroups nest under.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from redbot.core import commands

from .cog_base import PixelAgentsBase


class AdminCommandsMixin(PixelAgentsBase):
    """Keep the stable `[p]floorplan` root as a framework adapter."""

    @commands.hybrid_group(name="floorplan", invoke_without_command=True)
    async def floorplan_group(self, ctx: commands.Context) -> None:
        """Browse and load shared office layouts from Pixel Index."""

        send_help: Callable[[], Awaitable[object]] = ctx.send_help
        await send_help()


__all__ = ["AdminCommandsMixin"]
