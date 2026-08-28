"""A per-cog bound reply sender -- obtained once via `CogBase.reply_sender`,
reused at every one of that cog's own `send_reply`/`render_reply` call
sites, so `owner`/`avatar_path` never repeats as an argument at any of
them. See docs/reply-identity-design.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from redbot.core import commands

from ..domain import FooterOverride, RenderedReply, ReplyCategory, ReplyField, ReplyIdentity
from .api import send_rendered_reply

if TYPE_CHECKING:
    from .cog_base import CogBase


class ReplySender:
    """Bound once per cog, via `CogBase.reply_sender(owner=..., avatar_path=...)`
    at that cog's own `cog_load` -- forwards to `CogBase`'s existing
    `render_reply`/`send_reply` logic rather than duplicating it; adds
    nothing beyond carrying this cog's own `ReplyIdentity` through every
    call. A thin, explicit forwarding wrapper -- never a `__getattr__`
    blanket passthrough, so its surface stays intentional and typed.

    `avatar_path`, when given, should be the *conventional* path
    (`<cog_package>/assets/avatar.png`) regardless of whether that file
    currently exists on disk -- existence is checked fresh on every send
    (see `build_reply_payload`), so dropping a real image there later
    lights up icons everywhere with zero code change. Passing `None`
    outright (rather than a not-yet-existing conventional path) is only
    appropriate for a cog that will never want author icons at all."""

    def __init__(
        self,
        cog_base: CogBase,
        *,
        owner: str,
        avatar_path: Path | None = None,
        category: ReplyCategory | None = None,
    ) -> None:
        self._cog_base = cog_base
        self._avatar_path = avatar_path
        self._category = category
        self._identity = ReplyIdentity(
            owner=owner,
            avatar_filename=avatar_path.name if avatar_path is not None else None,
        )

    async def render_reply(
        self,
        ctx: commands.Context,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: Sequence[ReplyField] = (),
        code: Sequence[str] = (),
    ) -> RenderedReply:
        return await self._cog_base.render_reply(
            ctx,
            title=title,
            description=description,
            content=content,
            fields=fields,
            code=code,
            identity=self._identity,
            category=self._category,
        )

    async def send_reply(
        self,
        ctx: commands.Context,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: Sequence[ReplyField] = (),
        code: Sequence[str] = (),
        footer_override: FooterOverride | None = None,
    ) -> discord.Message:
        rendered = await self._cog_base.render_reply(
            ctx,
            title=title,
            description=description,
            content=content,
            fields=fields,
            code=code,
            identity=self._identity,
            footer_override=footer_override,
            category=self._category,
        )
        return await send_rendered_reply(ctx, rendered, avatar_path=self._avatar_path)

    async def publish_event(self, event: object) -> None:
        """Forwarded, not duplicated -- `ReplyTool` needs both this and
        `send_reply` from one object it's handed; everything else a
        caller might want from corridor stays reached through the plain
        `corridor` reference passed alongside this one, never guessed at
        via a blanket passthrough here."""

        await self._cog_base.publish_event(event)


__all__ = ["ReplySender"]
