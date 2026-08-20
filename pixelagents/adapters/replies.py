"""Discord response helpers shared by command adapters.

Every text/title/description/fields reply renders through Corridor's shared
`ReplyMode` (`self._corridor.render_reply`) -- see toolbox/adapters/commands.py
for the same rule applied to a cog that doesn't need interaction-aware
dispatch. pixelagents does, for `[p]pixelagents webview rebuild`'s deferred
followup, which corridor's own `send_reply` doesn't support -- so rather
than duplicate corridor's dispatch, this calls its lower-level
`render_reply` (title/description/fields in, a `RenderedReply` DTO out,
nothing sent) and keeps doing its own ctx/interaction dispatch on top of
that.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import discord
from redbot.core import commands

from corridor.domain import ReplyField

from .cog_base import PixelAgentsBase

_REPLY_TITLE = "Pixel Agents"


class ReplyMixin(PixelAgentsBase):
    async def _reply(
        self,
        ctx: commands.Context,
        content: str | None = None,
        *,
        title: str | None = None,
        fields: Sequence[ReplyField] = (),
        **kwargs: Any,
    ) -> None:
        if "view" in kwargs:
            if content is not None:
                kwargs["content"] = content
        else:
            kwargs.update(
                await self._render_reply_payload(
                    ctx, title=title, description=content, fields=fields
                )
            )

        if ctx.interaction:
            kwargs["ephemeral"] = True
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(**kwargs)
            else:
                await ctx.interaction.followup.send(**kwargs)
        else:
            await ctx.send(**kwargs)

    async def _render_reply_payload(
        self,
        ctx: commands.Context,
        *,
        title: str | None,
        description: str | None,
        fields: Sequence[ReplyField],
    ) -> dict[str, Any]:
        assert ctx.guild is not None, "pixelagents replies need a guild context"
        rendered = await self._corridor.render_reply(
            ctx.guild.id, title=title or _REPLY_TITLE, description=description, fields=fields
        )
        if rendered.mode == "text":
            return {"content": rendered.content}

        embed = discord.Embed(title=rendered.embed_title, description=rendered.embed_description)
        for field in rendered.fields:
            embed.add_field(name=field.name, value=field.value, inline=field.inline)
        if rendered.icon_url:
            embed.set_author(name=rendered.embed_title or "", icon_url=rendered.icon_url)
        if rendered.footer_text:
            embed.set_footer(text=rendered.footer_text, icon_url=rendered.icon_url)
        if rendered.show_timestamp:
            embed.timestamp = discord.utils.utcnow()
        return {"embed": embed}
