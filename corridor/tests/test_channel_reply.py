"""render_channel_reply/send_channel_reply -- render_reply/send_reply's
ctx-less twins (see docs/suggestionbox-design.md §5), against a real
Corridor instance, same scope test_reply_sender.py already uses."""

from __future__ import annotations

import unittest

from ..corridor import Corridor
from ..domain import ReplyMode
from .conftest import FakeBot, FakeChannel, FakeGuild


class TestChannelReply(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = FakeBot(owner_ids=frozenset({1}))
        self.guild = FakeGuild(guild_id=10)
        self.bot.register_guild(self.guild)
        self.corridor = Corridor(bot=self.bot)

    async def test_render_channel_reply_carries_the_bound_identity(self) -> None:
        sender = self.corridor.reply_sender(owner="SuggestionBox")

        rendered = await sender.render_channel_reply(self.guild.id, title="An error")

        self.assertEqual(rendered.author_name, "SuggestionBox")

    async def test_send_channel_reply_sends_to_the_given_channel_not_a_ctx(self) -> None:
        channel = FakeChannel()
        sender = self.corridor.reply_sender(owner="SuggestionBox")

        await sender.send_channel_reply(channel, self.guild.id, title="An error")

        self.assertEqual(len(channel.sent), 1)
        embed = channel.sent[0]["embed"]
        embed.set_author.assert_called_once_with(name="SuggestionBox", icon_url=None)

    async def test_send_channel_reply_respects_the_guilds_reply_mode(self) -> None:
        await self.corridor.set_reply_mode(self.guild.id, ReplyMode.TEXT)
        channel = FakeChannel()
        sender = self.corridor.reply_sender(owner="SuggestionBox")

        await sender.send_channel_reply(channel, self.guild.id, description="hello")

        self.assertEqual(channel.sent[0]["content"], "**SuggestionBox:** hello")

    async def test_literal_prefix_placeholder_is_substituted_from_the_bots_default(self) -> None:
        channel = FakeChannel()
        sender = self.corridor.reply_sender(owner="SuggestionBox")

        await sender.send_channel_reply(
            channel, self.guild.id, description="try [p]suggestionbox channel"
        )

        embed = channel.sent[0]["embed"]
        self.assertIn(";suggestionbox channel", embed.description)

    async def test_two_channel_replies_to_different_guilds_use_each_guilds_own_reply_mode(
        self,
    ) -> None:
        other_guild = FakeGuild(guild_id=20)
        self.bot.register_guild(other_guild)
        await self.corridor.set_reply_mode(other_guild.id, ReplyMode.TEXT)
        channel = FakeChannel()
        sender = self.corridor.reply_sender(owner="SuggestionBox")

        await sender.send_channel_reply(channel, self.guild.id, description="embed guild")
        await sender.send_channel_reply(channel, other_guild.id, description="text guild")

        self.assertIsNotNone(channel.sent[0]["embed"])
        self.assertEqual(channel.sent[1]["content"], "**SuggestionBox:** text guild")


if __name__ == "__main__":
    unittest.main()
