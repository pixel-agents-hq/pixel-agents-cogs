"""Discord-facing adapters: translate discord.py/Red types into the pure
application-layer protocols, and RenderedReply DTOs back into real sends."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import discord
from redbot.core import commands
from redbot.core.bot import Red

from ..domain import REPLY_CATEGORY_COLORS, RenderedReply, ReplyMode


class DiscordMemberRef:
    """Adapts discord.Member to the application layer's MemberRef protocol."""

    def __init__(self, member: discord.Member) -> None:
        self.id = member.id
        self.role_ids: frozenset[int] = frozenset(role.id for role in member.roles)
        self.permission_names: frozenset[str] = frozenset(
            name for name, value in member.guild_permissions if value
        )
        self.is_administrator: bool = member.guild_permissions.administrator


class BotOwnerRegistry:
    """Adapts Red's bot.owner_ids to the application layer's OwnerRegistry protocol."""

    def __init__(self, bot: Red) -> None:
        self._bot = bot

    async def is_owner(self, user_id: int) -> bool:
        return user_id in self._bot.owner_ids


class BotIconResolver:
    """Adapts bot/guild avatar lookups to the application layer's IconResolver protocol."""

    def __init__(self, bot: Red) -> None:
        self._bot = bot

    async def bot_icon_url(self) -> str | None:
        user = self._bot.user
        if user is None:
            return None
        return str(user.display_avatar.url)

    async def guild_icon_url(self, guild_id: int) -> str | None:
        guild = self._bot.get_guild(guild_id)
        if guild is None or guild.icon is None:
            return None
        return str(guild.icon.url)


def build_reply_payload(
    reply: RenderedReply, *, avatar_path: Path | None = None, footer_icon_path: Path | None = None
) -> tuple[dict[str, Any], list[discord.File]]:
    """embed/content kwargs + attachments for one `ctx.send(...)` call --
    shared by `send_rendered_reply` below and floorplan's/pixelagents'
    own interaction-aware `ReplyMixin` dispatch (their own ephemeral/
    followup sends need a different call than plain `ctx.send`, but the
    same embed-building logic -- see docs/reply-identity-design.md
    section 3 on why this was extracted rather than left duplicated in
    three places)."""

    if reply.mode is ReplyMode.TEXT:
        # No embed exists in TEXT mode -- the author-name prefix (if any)
        # was already applied by ReplyService.render; icons have no
        # TEXT-mode equivalent at all.
        return {"content": reply.content}, []

    color = REPLY_CATEGORY_COLORS.get(reply.category) if reply.category is not None else None
    embed = discord.Embed(title=reply.embed_title, description=reply.embed_description, color=color)
    for field in reply.fields:
        embed.add_field(name=field.name, value=field.value, inline=field.inline)

    files: list[discord.File] = []
    author_icon_url: str | None = None
    if reply.author_icon_attachment and avatar_path is not None and avatar_path.exists():
        # Re-read from disk on every call -- deliberate simplicity over
        # premature caching (docs/reply-identity-design.md section 2). A
        # small avatar re-uploaded on every reply that needs it is an
        # accepted cost.
        files.append(discord.File(avatar_path, filename=reply.author_icon_attachment))
        author_icon_url = f"attachment://{reply.author_icon_attachment}"

    footer_icon_url = reply.footer_icon_url
    if reply.footer_icon_attachment and footer_icon_path is not None and footer_icon_path.exists():
        # Prefixed, not the bare filename `author_icon_attachment` above
        # uses -- every cog's avatar is conventionally named "avatar.png"
        # (see ReplyIdentity/FooterOverride's own docstrings), so the
        # calling cog's own author attachment and the consulted agent's
        # footer attachment would otherwise collide: Discord requires
        # unique filenames among one message's attachments.
        footer_filename = f"footer_{reply.footer_icon_attachment}"
        files.append(discord.File(footer_icon_path, filename=footer_filename))
        footer_icon_url = f"attachment://{footer_filename}"

    if reply.author_name:
        # Always set once an identity is bound -- regardless of whether
        # an avatar exists, unlike the former icon-gated behavior.
        embed.set_author(name=reply.author_name, icon_url=author_icon_url)
    if reply.footer_text:
        embed.set_footer(text=reply.footer_text, icon_url=footer_icon_url)
    if reply.show_timestamp:
        embed.timestamp = discord.utils.utcnow()

    return {"embed": embed}, files


async def send_rendered_reply(
    ctx: commands.Context,
    reply: RenderedReply,
    *,
    avatar_path: Path | None = None,
    footer_icon_path: Path | None = None,
) -> discord.Message:
    kwargs, files = build_reply_payload(
        reply, avatar_path=avatar_path, footer_icon_path=footer_icon_path
    )
    return await ctx.send(files=files, **kwargs)
