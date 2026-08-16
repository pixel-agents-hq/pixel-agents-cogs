"""Discord-facing adapters: translate discord.py/Red types into the pure
application-layer protocols, and RenderedReply DTOs back into real sends."""

from __future__ import annotations

import discord
from redbot.core import commands
from redbot.core.bot import Red

from ..domain import RenderedReply, ReplyMode


class DiscordMemberRef:
    """Adapts discord.Member to the application layer's MemberRef protocol."""

    def __init__(self, member: discord.Member) -> None:
        self.id = member.id
        self.role_ids: frozenset[int] = frozenset(role.id for role in member.roles)


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


async def send_rendered_reply(ctx: commands.Context, reply: RenderedReply) -> discord.Message:
    if reply.mode is ReplyMode.TEXT:
        return await ctx.send(content=reply.content)

    embed = discord.Embed(title=reply.embed_title, description=reply.embed_description)
    if reply.icon_url:
        embed.set_author(name=reply.embed_title or "", icon_url=reply.icon_url)
    if reply.footer_text:
        embed.set_footer(text=reply.footer_text, icon_url=reply.icon_url)
    if reply.show_timestamp:
        embed.timestamp = discord.utils.utcnow()
    return await ctx.send(embed=embed)
