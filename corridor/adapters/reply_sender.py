"""A per-cog bound reply sender -- obtained once via `CogBase.reply_sender`,
reused at every one of that cog's own `send_reply`/`render_reply` call
sites, so `owner`/`avatar_path` never repeats as an argument at any of
them. See docs/reply-identity-design.md.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

import discord
from redbot.core import commands

from ..domain import (
    AgentRef,
    FooterOverride,
    RenderedReply,
    ReplyCategory,
    ReplyField,
    ReplyIdentity,
)
from .api import send_rendered_reply, send_rendered_reply_to_channel

if TYPE_CHECKING:
    from .cog_base import CogBase

# Who is replying, for a per-send avatar (`CogBase.avatar_for`): an
# `AgentRef`, or a callable for one only known later (the bot's own account
# before the bot is ready).
ReplyAgent: TypeAlias = AgentRef | Callable[[], AgentRef | None]


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
    appropriate for a cog that will never want author icons at all.

    `agent`, given in place of `avatar_path`, names who is replying: each
    send asks `CogBase.avatar_for(agent)` for its avatar (pixelagents
    answers with that agent's office character), and the reply carries no
    icon while there's none. An agent never has a bundled image."""

    def __init__(
        self,
        cog_base: CogBase,
        *,
        owner: str,
        avatar_path: Path | None = None,
        category: ReplyCategory | None = None,
        agent: ReplyAgent | None = None,
    ) -> None:
        if agent is not None and avatar_path is not None:
            raise ValueError("an agent's avatar is its office character, not a bundled image")
        self._cog_base = cog_base
        self._owner = owner
        self._avatar_path = avatar_path
        self._category = category
        self._agent = agent
        self._identity = ReplyIdentity(
            owner=owner,
            avatar_filename=avatar_path.name if avatar_path is not None else None,
        )

    async def _avatar(self) -> tuple[ReplyIdentity, Path | None]:
        """This send's identity and author icon: the agent's avatar (its
        office character) for an agent, else the bundled `avatar_path`."""

        if self._agent is None:
            return self._identity, self._avatar_path
        agent = self._agent() if callable(self._agent) else self._agent
        resolved = await self._cog_base.avatar_for(agent) if agent is not None else None
        if resolved is None:
            return self._identity, None
        return ReplyIdentity(owner=self._owner, avatar_filename=resolved.name), resolved

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
        identity, _ = await self._avatar()
        return await self._cog_base.render_reply(
            ctx,
            title=title,
            description=description,
            content=content,
            fields=fields,
            code=code,
            identity=identity,
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
        footer_icon_path: Path | None = None,
        extra_files: Sequence[discord.File] = (),
    ) -> discord.Message:
        identity, avatar_path = await self._avatar()
        rendered = await self._cog_base.render_reply(
            ctx,
            title=title,
            description=description,
            content=content,
            fields=fields,
            code=code,
            identity=identity,
            footer_override=footer_override,
            category=self._category,
        )
        return await send_rendered_reply(
            ctx,
            rendered,
            avatar_path=avatar_path,
            footer_icon_path=footer_icon_path,
            extra_files=extra_files,
        )

    async def render_channel_reply(
        self,
        guild_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: Sequence[ReplyField] = (),
        code: Sequence[str] = (),
    ) -> RenderedReply:
        identity, _ = await self._avatar()
        return await self._cog_base.render_channel_reply(
            guild_id,
            title=title,
            description=description,
            content=content,
            fields=fields,
            code=code,
            identity=identity,
            category=self._category,
        )

    async def send_channel_reply(
        self,
        channel: discord.abc.Messageable,
        guild_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        content: str | None = None,
        fields: Sequence[ReplyField] = (),
        code: Sequence[str] = (),
        footer_override: FooterOverride | None = None,
        footer_icon_path: Path | None = None,
        extra_files: Sequence[discord.File] = (),
    ) -> discord.Message:
        identity, avatar_path = await self._avatar()
        rendered = await self._cog_base.render_channel_reply(
            guild_id,
            title=title,
            description=description,
            content=content,
            fields=fields,
            code=code,
            identity=identity,
            footer_override=footer_override,
            category=self._category,
        )
        return await send_rendered_reply_to_channel(
            channel,
            rendered,
            avatar_path=avatar_path,
            footer_icon_path=footer_icon_path,
            extra_files=extra_files,
        )

    async def publish_event(self, event: object) -> None:
        """Forwarded, not duplicated -- `ReplyTool` needs both this and
        `send_reply` from one object it's handed; everything else a
        caller might want from corridor stays reached through the plain
        `corridor` reference passed alongside this one, never guessed at
        via a blanket passthrough here."""

        await self._cog_base.publish_event(event)


__all__ = ["ReplyAgent", "ReplySender"]
