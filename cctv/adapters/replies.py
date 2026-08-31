"""Interaction-aware replies rendered by Corridor."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from redbot.core import commands

from .cog_base import CctvBase

if TYPE_CHECKING:
    from corridor.domain import ReplyField


class ReplyMixin(CctvBase):
    async def _reply(
        self,
        ctx: commands.Context,
        content: str | None = None,
        *,
        title: str | None = None,
        fields: Sequence[ReplyField] = (),
        code: Sequence[str] = (),
        **kwargs: Any,
    ) -> None:
        from corridor.adapters.api import build_reply_payload
        from corridor.domain import ReplyCategory, ReplyIdentity

        if "view" not in kwargs:
            rendered = await self._corridor.render_reply(
                ctx,
                title=title or "CCTV",
                description=content,
                fields=fields,
                code=code,
                identity=ReplyIdentity(owner="CCTV"),
                category=ReplyCategory.ROOM,
            )
            payload, files = build_reply_payload(rendered)
            kwargs.update(payload)
            if files:
                kwargs["files"] = files
        elif content is not None:
            kwargs["content"] = content

        if ctx.interaction:
            kwargs["ephemeral"] = True
            if ctx.interaction.response.is_done():
                await ctx.interaction.followup.send(**kwargs)
            else:
                await ctx.interaction.response.send_message(**kwargs)
        else:
            await ctx.send(**kwargs)


__all__ = ["ReplyMixin"]
