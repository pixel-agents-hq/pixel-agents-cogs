"""Discord response helpers shared by command adapters.

Every text/title/description/fields reply renders through Corridor's shared
`ReplyMode` (`self._corridor.render_reply`) -- see toolbox/adapters/commands.py
for the same rule applied to a cog that doesn't need interaction-aware
dispatch. floorplan does, for hybrid slash-command ephemeral responses and
deferred followups, which corridor's own `send_reply` doesn't support -- so
rather than duplicate corridor's dispatch, this calls its lower-level
`render_reply` (title/description/fields in, a `RenderedReply` DTO out,
nothing sent) and keeps doing its own ctx/interaction dispatch on top of
that. `fields` (`corridor.domain.ReplyField`, discord.Embed.add_field-shaped)
covers a multi-field status-style reply -- e.g. `cmd_status` -- without
falling back to a hand-built discord.Embed that bypasses ReplyMode.

A caller that already supplies a Discord Components V2 `view=` bypasses
rendering entirely and is sent as-is: Discord rejects mixing Components V2
with plain content or an embed, so `ReplyMode` structurally cannot apply to
it -- see e.g. `cmd_settings`, `cmd_layout_search`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from redbot.core import commands

if TYPE_CHECKING:
    # ReplyField is never constructed here, only referenced in annotations
    # (which `from __future__ import annotations` never evaluates at
    # runtime) -- a real top-level import isn't just unnecessary, it's
    # actively unsafe: Red's `_cleanup_and_refresh_modules` re-execs this
    # module directly, unconditionally, before floorplan's own setup() gets
    # a chance to run `ensure_corridor_loaded`, so a hard corridor import
    # here would crash any reload attempted at a moment corridor isn't
    # currently loaded (this happened in production for pixelagents' own
    # replies.py -- same shape of module, same risk).
    from corridor.domain import ReplyField

from .cog_base import PixelAgentsBase

_REPLY_TITLE = "Pixel Agents"

# Conventional bundled-avatar path + identity for this mixin's own
# render_reply calls -- see docs/reply-identity-design.md. ReplyIdentity
# itself is only constructed lazily, inside _render_reply_payload below,
# for the same reload-safety reason ReplyField stays under TYPE_CHECKING.
AVATAR_PATH = Path(__file__).resolve().parent.parent / "assets" / "avatar.png"


class ReplyMixin(PixelAgentsBase):
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
        await self._dispatch_reply(
            ctx, ephemeral=True, content=content, title=title, fields=fields, code=code, **kwargs
        )

    async def _send_public(
        self,
        ctx: commands.Context,
        content: str | None = None,
        *,
        title: str | None = None,
        fields: Sequence[ReplyField] = (),
        code: Sequence[str] = (),
        **kwargs: Any,
    ) -> None:
        await self._dispatch_reply(
            ctx, ephemeral=False, content=content, title=title, fields=fields, code=code, **kwargs
        )

    async def _dispatch_reply(
        self,
        ctx: commands.Context,
        *,
        ephemeral: bool,
        content: str | None = None,
        title: str | None = None,
        fields: Sequence[ReplyField] = (),
        code: Sequence[str] = (),
        **kwargs: Any,
    ) -> None:
        if "view" in kwargs:
            if content is not None:
                kwargs["content"] = content
        else:
            kwargs.update(
                await self._render_reply_payload(
                    ctx, title=title, description=content, fields=fields, code=code
                )
            )

        if ctx.interaction:
            kwargs["ephemeral"] = ephemeral
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
        code: Sequence[str] = (),
    ) -> dict[str, Any]:
        # Deferred, not a top-level import -- same reload-safety reasoning
        # this module's own docstring/TYPE_CHECKING block gives for
        # ReplyField above: a hard corridor import here would crash a
        # reload attempted at a moment corridor isn't currently loaded.
        from corridor.adapters.api import build_reply_payload
        from corridor.domain import ReplyCategory, ReplyIdentity

        rendered = await self._corridor.render_reply(
            ctx,
            title=title or _REPLY_TITLE,
            description=description,
            fields=fields,
            code=code,
            identity=ReplyIdentity(owner="Floorplan", avatar_filename=AVATAR_PATH.name),
            category=ReplyCategory.ROOM,
        )
        kwargs, files = build_reply_payload(rendered, avatar_path=AVATAR_PATH)
        if files:
            kwargs["files"] = files
        return kwargs
