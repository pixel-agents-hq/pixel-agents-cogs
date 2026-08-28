"""ReplySender end-to-end through a real Corridor instance -- exercises
CogBase.reply_sender()/render_reply()/send_reply() together, the same
"only the discord/redbot stubs" scope test_cog_api.py already uses."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ..corridor import Corridor
from ..domain import REPLY_CATEGORY_COLORS, FooterOverride, ReplyCategory, ReplyMode
from .conftest import FakeBot, FakeContext, FakeGuild, FakeMember


class TestReplySender(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = FakeBot(owner_ids=frozenset({1}))
        self.guild = FakeGuild(guild_id=10)
        self.bot.register_guild(self.guild)
        self.corridor = Corridor(bot=self.bot)

    async def test_render_reply_carries_the_bound_identity(self) -> None:
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)
        sender = self.corridor.reply_sender(owner="Architect")

        rendered = await sender.render_reply(ctx, title="Hi")

        self.assertEqual(rendered.author_name, "Architect")
        self.assertIsNone(rendered.author_icon_attachment)

    async def test_send_reply_sets_the_author_name_with_no_avatar_configured(self) -> None:
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)
        sender = self.corridor.reply_sender(owner="Architect")

        await sender.send_reply(ctx, title="Hi")

        embed = ctx.sent[0]["embed"]
        embed.set_author.assert_called_once_with(name="Architect", icon_url=None)

    async def test_send_reply_attaches_the_avatar_when_the_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            avatar_path = Path(tmp) / "avatar.png"
            avatar_path.write_bytes(b"fake-png-bytes")
            member = FakeMember(2, self.guild)
            ctx = FakeContext(author=member, guild=self.guild)
            sender = self.corridor.reply_sender(owner="Architect", avatar_path=avatar_path)

            await sender.send_reply(ctx, title="Hi")

            embed = ctx.sent[0]["embed"]
            embed.set_author.assert_called_once_with(
                name="Architect", icon_url="attachment://avatar.png"
            )
            self.assertEqual(len(ctx.sent[0]["files"]), 1)

    async def test_send_reply_omits_the_avatar_when_the_configured_file_is_missing(self) -> None:
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)
        sender = self.corridor.reply_sender(
            owner="Architect", avatar_path=Path("/does/not/exist/avatar.png")
        )

        await sender.send_reply(ctx, title="Hi")

        embed = ctx.sent[0]["embed"]
        embed.set_author.assert_called_once_with(name="Architect", icon_url=None)
        self.assertEqual(ctx.sent[0]["files"], [])

    async def test_send_reply_footer_override_name_reaches_the_embed(self) -> None:
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)
        sender = self.corridor.reply_sender(owner="Pico")
        override = FooterOverride(name="architect", icon_filename="avatar.png")

        await sender.send_reply(
            ctx,
            description="asking",
            footer_override=override,
            footer_icon_path=Path("/does/not/exist/avatar.png"),
        )

        embed = ctx.sent[0]["embed"]
        # Missing file -- name still shows, icon stays absent (same
        # convention as the author's own avatar).
        embed.set_footer.assert_called_once_with(text="architect", icon_url=None)
        self.assertEqual(ctx.sent[0]["files"], [])

    async def test_send_reply_attaches_the_footer_icon_when_the_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            footer_icon_path = Path(tmp) / "avatar.png"
            footer_icon_path.write_bytes(b"fake-png-bytes")
            member = FakeMember(2, self.guild)
            ctx = FakeContext(author=member, guild=self.guild)
            sender = self.corridor.reply_sender(owner="Pico")
            override = FooterOverride(name="architect", icon_filename="avatar.png")

            await sender.send_reply(
                ctx,
                description="asking",
                footer_override=override,
                footer_icon_path=footer_icon_path,
            )

            embed = ctx.sent[0]["embed"]
            embed.set_footer.assert_called_once_with(
                text="architect", icon_url="attachment://footer_avatar.png"
            )
            self.assertEqual(len(ctx.sent[0]["files"]), 1)

    async def test_send_reply_footer_and_author_icon_attachments_never_collide(self) -> None:
        """Both are conventionally named "avatar.png" -- corridor's own
        avatar and the consulted agent's -- so the footer attachment must
        get a distinct filename or Discord would only keep one of the two
        same-named files."""

        with tempfile.TemporaryDirectory() as tmp:
            avatar_path = Path(tmp) / "avatar.png"
            avatar_path.write_bytes(b"pico-avatar-bytes")
            footer_icon_path = Path(tmp) / "footer" / "avatar.png"
            footer_icon_path.parent.mkdir()
            footer_icon_path.write_bytes(b"architect-avatar-bytes")
            member = FakeMember(2, self.guild)
            ctx = FakeContext(author=member, guild=self.guild)
            sender = self.corridor.reply_sender(owner="Pico", avatar_path=avatar_path)
            override = FooterOverride(name="architect", icon_filename="avatar.png")

            await sender.send_reply(
                ctx,
                description="asking",
                footer_override=override,
                footer_icon_path=footer_icon_path,
            )

            embed = ctx.sent[0]["embed"]
            embed.set_author.assert_called_once_with(
                name="Pico", icon_url="attachment://avatar.png"
            )
            embed.set_footer.assert_called_once_with(
                text="architect", icon_url="attachment://footer_avatar.png"
            )
            self.assertEqual(len(ctx.sent[0]["files"]), 2)

    async def test_send_reply_carries_the_bound_category_to_the_embed_color(self) -> None:
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)
        sender = self.corridor.reply_sender(owner="Pico", category=ReplyCategory.AGENT)

        await sender.send_reply(ctx, title="Hi")

        embed = ctx.sent[0]["embed"]
        self.assertEqual(embed.color, REPLY_CATEGORY_COLORS[ReplyCategory.AGENT])

    async def test_send_reply_with_no_bound_category_leaves_the_embed_uncolored(self) -> None:
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)
        sender = self.corridor.reply_sender(owner="Architect")

        await sender.send_reply(ctx, title="Hi")

        embed = ctx.sent[0]["embed"]
        self.assertIsNone(embed.color)

    async def test_send_reply_text_mode_prefixes_the_owner_name(self) -> None:
        await self.corridor.set_reply_mode(self.guild.id, ReplyMode.TEXT)
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)
        sender = self.corridor.reply_sender(owner="Pico")

        await sender.send_reply(ctx, description="hello")

        self.assertEqual(ctx.sent[0]["content"], "**Pico:** hello")

    async def test_publish_event_is_forwarded_to_corridor(self) -> None:
        published: list[object] = []

        async def _record(event: object) -> None:
            published.append(event)

        self.corridor.publish_event = _record  # type: ignore[method-assign]
        sender = self.corridor.reply_sender(owner="Pico")

        await sender.publish_event("an-event")

        self.assertEqual(published, ["an-event"])

    async def test_two_senders_stay_independent(self) -> None:
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)
        architect_sender = self.corridor.reply_sender(owner="Architect")
        pico_sender = self.corridor.reply_sender(owner="Pico")

        await architect_sender.send_reply(ctx, title="A")
        await pico_sender.send_reply(ctx, title="B")

        self.assertEqual(ctx.sent[0]["embed"].set_author.call_args.kwargs["name"], "Architect")
        self.assertEqual(ctx.sent[1]["embed"].set_author.call_args.kwargs["name"], "Pico")


if __name__ == "__main__":
    unittest.main()
