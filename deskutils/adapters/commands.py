"""Discord-facing commands. Thin: translate ctx <-> service calls only.

Replies go through corridor (this cog's required_cogs dependency) rather
than ctx.send(), so this cog automatically respects whatever reply style
the guild has already configured for every other cog.
"""

from __future__ import annotations

from typing import Annotated, Any

import discord
from redbot.core import commands

from corridor.domain import EMPLOYEE_KEY, ReplyField, ToolDescription, llm_tool

from ..application import TextService, TimeService, UnknownTimeZoneError

_MAX_QUOTED_TEXT_LENGTH = 1_750


class _QuoteResolutionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def _resolve_quoted_message(
    ctx: commands.Context, message_link: str | None
) -> discord.Message:
    if message_link is not None:
        try:
            target = await commands.MessageConverter().convert(ctx, message_link)
        except Exception as exc:
            raise _QuoteResolutionError(
                "message_not_found",
                "I couldn't find that Discord message. Check the link and try again.",
            ) from exc
    else:
        reference = getattr(ctx.message, "reference", None)
        if reference is None or getattr(reference, "message_id", None) is None:
            raise _QuoteResolutionError(
                "message_required",
                "Reply to a message or provide a Discord message link to quote.",
            )
        resolved = getattr(reference, "resolved", None)
        if resolved is not None and hasattr(resolved, "author"):
            target = resolved
        else:
            channel_id = getattr(reference, "channel_id", None) or ctx.channel.id
            locator = f"{channel_id}-{reference.message_id}"
            try:
                target = await commands.MessageConverter().convert(ctx, locator)
            except Exception as exc:
                raise _QuoteResolutionError(
                    "message_not_found",
                    "I couldn't find the replied-to message. It may have been deleted.",
                ) from exc

    if ctx.guild is None or target.guild is None or target.guild.id != ctx.guild.id:
        raise _QuoteResolutionError(
            "message_not_accessible",
            "The quoted message must be in this server.",
        )
    permissions = target.channel.permissions_for(ctx.author)
    if not permissions.view_channel or not permissions.read_message_history:
        raise _QuoteResolutionError(
            "message_not_accessible",
            "You don't have permission to read that message's channel.",
        )
    return target


def _quoted_text(content: str) -> str:
    escaped = discord.utils.escape_mentions(content)
    if len(escaped) > _MAX_QUOTED_TEXT_LENGTH:
        escaped = f"{escaped[: _MAX_QUOTED_TEXT_LENGTH - 1]}…"
    return f">>> {escaped}"


class CommandsMixin:
    """Requires application services and `self._corridor`
    (both provided by CogBase)."""

    _service: TimeService
    _text_service: TextService
    _corridor: Any
    _reply: Any

    @commands.hybrid_group(name="deskutils")
    async def deskutils_group(self, ctx: commands.Context) -> None:
        """Use stateless time, text, and message utilities."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @deskutils_group.command(name="count")
    @llm_tool()
    async def count_command(self, ctx: commands.Context, *, text: str) -> dict[str, object]:
        """Count all characters and whitespace-delimited words in text."""

        if not isinstance(text, str):
            message = "Text to count must be a string."
            await self._reply.send_reply(ctx, title="Text count", description=message)
            return {"status": "error", "error": "invalid_text", "message": message}

        statistics = self._text_service.count(text)
        await self._reply.send_reply(
            ctx,
            title="Text count",
            fields=[
                ReplyField("Characters", str(statistics.characters)),
                ReplyField("Words", str(statistics.words)),
            ],
        )
        return {
            "status": "ok",
            "characters": statistics.characters,
            "words": statistics.words,
        }

    @deskutils_group.command(name="quote")
    @commands.guild_only()
    @llm_tool()
    async def quote_command(
        self, ctx: commands.Context, message_link: str | None = None
    ) -> dict[str, object]:
        """Quote a replied-to Discord message or one identified by a message link."""

        if message_link is not None and not isinstance(message_link, str):
            message = "The message link must be a string."
            await self._reply.send_reply(ctx, title="Quote", description=message)
            return {"status": "error", "error": "invalid_message", "message": message}

        try:
            target = await _resolve_quoted_message(ctx, message_link)
        except _QuoteResolutionError as exc:
            await self._reply.send_reply(ctx, title="Quote", description=exc.message)
            return {"status": "error", "error": exc.code, "message": exc.message}

        content = target.content
        if not content or not content.strip():
            message = "That message has no text to quote."
            await self._reply.send_reply(ctx, title="Quote", description=message)
            return {"status": "error", "error": "empty_message", "message": message}

        author_name = target.author.display_name
        jump_url = target.jump_url
        await self._reply.send_reply(
            ctx,
            title="Quoted message",
            description=_quoted_text(content),
            fields=[
                ReplyField("Author", discord.utils.escape_mentions(author_name), inline=False),
                ReplyField("Source", f"[Jump to message]({jump_url})", inline=False),
            ],
        )
        return {
            "status": "ok",
            "message_id": target.id,
            "channel_id": target.channel.id,
            "author_id": target.author.id,
            "author_name": author_name,
            "content": content,
            "jump_url": jump_url,
        }

    @deskutils_group.command(name="time")
    @llm_tool(
        name="deskutils_time",
        description=(
            "Get the current date and time. Optionally pass an IANA timezone name "
            "(e.g. 'America/New_York') to also get it localized to that zone."
        ),
        required_group=EMPLOYEE_KEY,
    )
    async def time_command(
        self,
        ctx: commands.Context,
        timezone: Annotated[
            str | None,
            ToolDescription("An IANA time zone name, e.g. 'America/New_York' or 'Europe/London'."),
        ] = None,
    ) -> dict[str, object]:
        """Show the current time.

        Always includes Discord's native timestamp markup, which each
        viewer's own client renders in their own local time and locale
        automatically, plus an explicit UTC timestamp. Pass an IANA
        `timezone` (e.g. `America/New_York`) to also show it explicitly
        localized to that zone.
        """

        if not await self._corridor.require_permission(ctx, EMPLOYEE_KEY):
            return {
                "status": "error",
                "error": "permission_denied",
                "message": "The invoking member does not have permission to use this tool.",
            }

        snapshot = self._service.now()
        epoch = snapshot.epoch_seconds
        discord_timestamp = f"<t:{epoch}:F> (<t:{epoch}:R>)"
        utc = snapshot.utc.strftime("%Y-%m-%d %H:%M:%S %Z")
        fields = [
            ReplyField(
                "Discord (auto-localized per viewer)",
                discord_timestamp,
                inline=False,
            ),
            ReplyField("UTC", utc, inline=False),
        ]
        result: dict[str, object] = {
            "status": "ok",
            "epoch_seconds": epoch,
            "utc": utc,
            "discord_timestamp": discord_timestamp,
        }

        if timezone is not None:
            try:
                zone = self._service.resolve_zone(timezone)
            except UnknownTimeZoneError:
                warning = (
                    f"⚠️ Unknown time zone `{timezone}`. Use an IANA name, e.g. "
                    "`America/New_York` or `Europe/London`."
                )
                await self._reply.send_reply(
                    ctx,
                    title="deskutils",
                    description=warning,
                )
                return {
                    "status": "error",
                    "error": "unknown_timezone",
                    "timezone": timezone,
                    "message": warning,
                }
            localized = snapshot.utc.astimezone(zone)
            localized_text = localized.strftime("%Y-%m-%d %H:%M:%S %Z")
            fields.append(ReplyField(timezone, localized_text, inline=False))
            result["timezone"] = timezone
            result["localized"] = localized_text

        await self._reply.send_reply(ctx, title="Current time", fields=fields)
        return result
