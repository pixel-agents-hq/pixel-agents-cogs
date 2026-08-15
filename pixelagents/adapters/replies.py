"""Discord response helpers shared by command adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from redbot.core import commands

from .cog_base import PixelAgentsBase


class ReplyMixin(PixelAgentsBase):
    async def _reply(self, ctx: commands.Context, content: Any = None, **kwargs: Any) -> None:
        if ctx.interaction:
            kwargs["ephemeral"] = True
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(content, **kwargs)
            else:
                await ctx.interaction.followup.send(content, **kwargs)
        else:
            send: Callable[..., Awaitable[object]] = ctx.send
            await send(content, **kwargs)

    async def _send_public(self, ctx: commands.Context, content: Any = None, **kwargs: Any) -> None:
        if ctx.interaction:
            kwargs["ephemeral"] = False
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(content, **kwargs)
            else:
                await ctx.interaction.followup.send(content, **kwargs)
        else:
            send: Callable[..., Awaitable[object]] = ctx.send
            await send(content, **kwargs)
