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
    # Copy-pastable strings (a command, a config value, ...) that should
    # render in their own Discord code block -- giving the client's native
    # copy button -- rather than inline in prose. Each is rendered as its
    # own fenced block, appended after the rest of the reply's text/embed
    # description. Prose that merely *mentions* a command inline should
    # stay out of here and keep its single backticks; a fenced block is
    # always block-level, so promoting an inline mention would force ugly
    # mid-sentence line breaks -- see `ReplyField.code` for the same
    # treatment when the copy-pastable value *is* a whole field's value.
    code: tuple[str, ...] = ()


def _substitute_prefix(text: str | None, prefix: str) -> str | None:
    """Mirror Red's own `[p]` substitution (applied automatically to
    command docstrings) for reply text this codebase builds by hand --
    which Red's substitution never touches."""

    if text is None:
        return None
    return text.replace("[p]", prefix)


def _fence(text: str) -> str:
    return f"```\n{text}\n```"


class ReplyService:
    def __init__(self, icons: IconResolver) -> None:
        self._icons = icons

    async def render(
        self,
        guild_id: int,
        preferences: ReplyPreferences,
        content: ReplyContent,
        *,
        prefix: str,
    ) -> RenderedReply:
        title = _substitute_prefix(content.title, prefix)
        description = _substitute_prefix(content.description, prefix)
        body = _substitute_prefix(content.content, prefix)
        fields = tuple(
            ReplyField(
                field.name, _substitute_prefix(field.value, prefix) or "", field.inline, field.code
            )
            for field in content.fields
        )
        code_blocks = tuple(
            _fence(_substitute_prefix(entry, prefix) or "") for entry in content.code
        )

        if preferences.mode is ReplyMode.TEXT:
            base = body or description or title or ""
            # An embed field has no text-mode equivalent, so it isn't
            # dropped -- it becomes an extra "**name:** value" line instead,
            # after whatever base text there is. A `code` field renders its
            # value as its own fenced block on the following line instead,
            # same reasoning as the message-level `code` entries below.
            lines = [base] if base else []
            lines.extend(code_blocks)
            lines.extend(
                f"**{field.name}:**\n{_fence(field.value)}"
                if field.code
                else f"**{field.name}:** {field.value}"
                for field in fields
            )
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
        embed_description = description or body
        if code_blocks:
            block_text = "\n".join(code_blocks)
            embed_description = (
                f"{embed_description}\n\n{block_text}" if embed_description else block_text
            )
        embed_fields = tuple(
            ReplyField(field.name, _fence(field.value), False, field.code) if field.code else field
            for field in fields
        )
        return RenderedReply(
            mode=ReplyMode.EMBED,
            content=None,
            embed_title=title,
            embed_description=embed_description,
            fields=embed_fields,
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
