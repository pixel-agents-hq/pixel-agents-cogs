"""Framework-agnostic reply rendering. Turns a guild's ReplyPreferences plus
message content into a RenderedReply DTO -- the adapter layer is the only
place that touches discord.Embed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain import IconSource, RenderedReply, ReplyMode, ReplyPreferences


class IconResolver(Protocol):
    """The only side-effecting dependency: resolving "bot" / "server" icons
    to a URL. Everything else here is pure."""

    async def bot_icon_url(self) -> str | None: ...

    async def guild_icon_url(self, guild_id: int) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ReplyContent:
    title: str | None = None
    description: str | None = None
    content: str | None = None


class ReplyService:
    def __init__(self, icons: IconResolver) -> None:
        self._icons = icons

    async def render(
        self, guild_id: int, preferences: ReplyPreferences, content: ReplyContent
    ) -> RenderedReply:
        if preferences.mode is ReplyMode.TEXT:
            text = content.content or content.description or content.title or ""
            return RenderedReply(
                mode=ReplyMode.TEXT,
                content=text,
                embed_title=None,
                embed_description=None,
                footer_text=None,
                show_timestamp=False,
                icon_url=None,
            )

        icon_url = await self._resolve_icon(guild_id, preferences)
        return RenderedReply(
            mode=ReplyMode.EMBED,
            content=None,
            embed_title=content.title,
            embed_description=content.description or content.content,
            footer_text=preferences.footer_text,
            show_timestamp=preferences.show_timestamp,
            icon_url=icon_url,
        )

    async def _resolve_icon(self, guild_id: int, preferences: ReplyPreferences) -> str | None:
        icon = preferences.icon
        if icon.source is IconSource.CUSTOM:
            return icon.custom_url
        if icon.source is IconSource.BOT:
            return await self._icons.bot_icon_url()
        return await self._icons.guild_icon_url(guild_id)
