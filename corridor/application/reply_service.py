"""Framework-agnostic reply rendering. Turns a guild's ReplyPreferences plus
message content into a RenderedReply DTO -- the adapter layer is the only
place that touches discord.Embed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain import IconSource, RenderedReply, ReplyField, ReplyMode, ReplyPreferences


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
    fields: tuple[ReplyField, ...] = ()


class ReplyService:
    def __init__(self, icons: IconResolver) -> None:
        self._icons = icons

    async def render(
        self, guild_id: int, preferences: ReplyPreferences, content: ReplyContent
    ) -> RenderedReply:
        if preferences.mode is ReplyMode.TEXT:
            base = content.content or content.description or content.title or ""
            # An embed field has no text-mode equivalent, so it isn't
            # dropped -- it becomes an extra "**name:** value" line instead,
            # after whatever base text there is.
            lines = [base] if base else []
            lines.extend(f"**{field.name}:** {field.value}" for field in content.fields)
            return RenderedReply(
                mode=ReplyMode.TEXT,
                content="\n".join(lines),
                embed_title=None,
                embed_description=None,
                fields=(),
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
            fields=content.fields,
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
