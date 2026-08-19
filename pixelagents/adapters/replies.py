"""Discord response helpers shared by command adapters.

Every plain-text/title+description reply renders through Corridor's shared
`ReplyMode` (`self._corridor.render_reply`) -- see toolbox/adapters/commands.py
for the same rule applied to a cog that doesn't need interaction-aware
dispatch. pixelagents does, for hybrid slash-command ephemeral responses and
deferred followups, which corridor's own `send_reply` doesn't support -- so
rather than duplicate corridor's dispatch, this calls its lower-level
`render_reply` (title/description in, a `RenderedReply` DTO out, nothing
sent) and keeps doing its own ctx/interaction dispatch on top of that.

A caller that already supplies a Discord Components V2 `view=` (and no
`content=`/`embed=`) bypasses rendering entirely and is sent as-is: Discord
rejects mixing Components V2 with plain content or an embed, so `ReplyMode`
structurally cannot apply to it -- see e.g. `cmd_settings`, `cmd_layout_search`.
"""

from __future__ import annotations

from typing import Any

import discord
from redbot.core import commands

from .cog_base import PixelAgentsBase

_REPLY_TITLE = "Pixel Agents"


class ReplyMixin(PixelAgentsBase):
    async def _reply(
        self,
        ctx: commands.Context,
        content: str | None = None,
        *,
        title: str | None = None,
        **kwargs: Any,
    ) -> None:
        await self._dispatch_reply(ctx, ephemeral=True, content=content, title=title, **kwargs)

    async def _send_public(
        self,
        ctx: commands.Context,
        content: str | None = None,
        *,
        title: str | None = None,
        **kwargs: Any,
    ) -> None:
        await self._dispatch_reply(ctx, ephemeral=False, content=content, title=title, **kwargs)

    async def _dispatch_reply(
        self,
        ctx: commands.Context,
        *,
        ephemeral: bool,
        content: str | None = None,
        title: str | None = None,
        **kwargs: Any,
    ) -> None:
        if content is not None and "view" not in kwargs and "embed" not in kwargs:
            kwargs.update(await self._render_reply_payload(ctx, title=title, description=content))
        elif content is not None:
            kwargs["content"] = content

        if ctx.interaction:
            kwargs["ephemeral"] = ephemeral
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(**kwargs)
            else:
                await ctx.interaction.followup.send(**kwargs)
        else:
            await ctx.send(**kwargs)

    async def _render_reply_payload(
        self, ctx: commands.Context, *, title: str | None, description: str
    ) -> dict[str, Any]:
        assert ctx.guild is not None, "pixelagents replies need a guild context"
        rendered = await self._corridor.render_reply(
            ctx.guild.id, title=title or _REPLY_TITLE, description=description
        )
        if rendered.mode == "text":
            return {"content": rendered.content}

        embed = discord.Embed(title=rendered.embed_title, description=rendered.embed_description)
        if rendered.icon_url:
            embed.set_author(name=rendered.embed_title or "", icon_url=rendered.icon_url)
        if rendered.footer_text:
            embed.set_footer(text=rendered.footer_text, icon_url=rendered.icon_url)
        if rendered.show_timestamp:
            embed.timestamp = discord.utils.utcnow()
        return {"embed": embed}
