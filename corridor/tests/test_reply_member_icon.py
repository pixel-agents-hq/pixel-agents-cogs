"""The addressed member's office character in reply embeds, per guild
settings: the author icon (`AuthorIconSource.MEMBER`) and/or the footer
icon (`IconSource.MEMBER`)."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

import discord

from ..adapters.api import MAX_ATTACHMENTS, build_reply_payload
from ..corridor import Corridor
from ..domain import (
    AgentRef,
    AuthorIconSource,
    FooterOverride,
    IconPreference,
    IconSource,
    ReplyMode,
)
from .conftest import FakeBot, FakeContext, FakeGuild, FakeMember

ARCHITECT = AgentRef(discord_user_id=None, guild_id=None, is_bot=True, agent_key="architect")
MEMBER_ID = 42


class TestReplyMemberIcon(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = FakeBot(owner_ids=frozenset({1}))
        self.guild = FakeGuild(guild_id=10)
        self.bot.register_guild(self.guild)
        self.corridor = Corridor(bot=self.bot)
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.files = {"architect": tmp / "aaaa.png", MEMBER_ID: tmp / "mmmm.png"}
        for path in self.files.values():
            path.write_bytes(b"png")
        self.asked: list[AgentRef] = []

        async def resolve(agent: AgentRef) -> Path | None:
            self.asked.append(agent)
            return self.files.get(agent.agent_key or agent.discord_user_id)  # type: ignore[arg-type]

        self.corridor.register_avatar_resolver(resolve, owner="pixelagents")

    def _ctx(self) -> FakeContext:
        member = FakeMember(MEMBER_ID, self.guild)
        member.display_name = "Ada"  # type: ignore[attr-defined]
        return FakeContext(author=member, guild=self.guild)

    async def _send(self, **kwargs: object) -> dict[str, object]:
        ctx = self._ctx()
        sender = self.corridor.reply_sender(owner="Architect", agent=ARCHITECT)
        await sender.send_reply(ctx, title="Hi", **kwargs)  # type: ignore[arg-type]
        return ctx.sent[0]

    async def _footer_member(self) -> None:
        await self.corridor.set_icon_preference(
            self.guild.id, IconPreference(source=IconSource.MEMBER)
        )

    async def test_by_default_the_member_is_not_looked_up(self) -> None:
        sent = await self._send()

        self.assertEqual(self.asked, [ARCHITECT])
        sent["embed"].set_author.assert_called_once_with(  # type: ignore[attr-defined]
            name="Architect", icon_url="attachment://aaaa.png"
        )

    async def test_the_member_can_be_the_author_icon(self) -> None:
        await self.corridor.set_author_icon(self.guild.id, AuthorIconSource.MEMBER)

        sent = await self._send()

        # The name stays the replier's.
        sent["embed"].set_author.assert_called_once_with(  # type: ignore[attr-defined]
            name="Architect", icon_url="attachment://member_mmmm.png"
        )
        self.assertEqual(
            [f.filename for f in sent["files"]],  # type: ignore[attr-defined]
            ["member_mmmm.png"],
        )

    async def test_the_member_can_be_the_footer_icon(self) -> None:
        await self._footer_member()

        sent = await self._send()

        embed = sent["embed"]
        embed.set_author.assert_called_once_with(  # type: ignore[attr-defined]
            name="Architect", icon_url="attachment://aaaa.png"
        )
        footer = embed.set_footer.call_args  # type: ignore[attr-defined]
        self.assertEqual(footer.kwargs["icon_url"], "attachment://member_mmmm.png")

    async def test_both_slots_share_one_attachment(self) -> None:
        await self._footer_member()
        await self.corridor.set_author_icon(self.guild.id, AuthorIconSource.MEMBER)

        sent = await self._send()

        self.assertEqual(
            [f.filename for f in sent["files"]],  # type: ignore[attr-defined]
            ["member_mmmm.png"],
        )

    async def test_a_consulted_agents_footer_still_wins(self) -> None:
        await self._footer_member()
        override = FooterOverride(name="painter", icon_filename="pppp.png")
        painter = self.files["architect"].with_name("pppp.png")
        painter.write_bytes(b"png")

        sent = await self._send(footer_override=override, footer_icon_path=painter)

        footer = sent["embed"].set_footer.call_args  # type: ignore[attr-defined]
        self.assertEqual(
            footer.kwargs, {"text": "painter", "icon_url": "attachment://footer_pppp.png"}
        )

    async def test_a_member_footer_without_text_is_labelled_with_their_name(self) -> None:
        await self._footer_member()

        sent = await self._send()

        footer = sent["embed"].set_footer.call_args  # type: ignore[attr-defined]
        self.assertEqual(footer.kwargs["text"], "Ada")

    async def test_without_a_member_avatar_the_footer_falls_back_to_the_bot(self) -> None:
        await self._footer_member()
        await self.corridor.set_footer_text(self.guild.id, "Pixel Agents HQ")
        await self.corridor.set_author_icon(self.guild.id, AuthorIconSource.MEMBER)
        del self.files[MEMBER_ID]

        sent = await self._send()

        embed = sent["embed"]
        embed.set_author.assert_called_once_with(  # type: ignore[attr-defined]
            name="Architect", icon_url="attachment://aaaa.png"
        )
        footer = embed.set_footer.call_args  # type: ignore[attr-defined]
        self.assertEqual(
            footer.kwargs["icon_url"], await self.corridor._reply_service._icons.bot_icon_url()
        )  # noqa: SLF001

    async def test_text_mode_never_looks_the_member_up(self) -> None:
        await self._footer_member()
        await self.corridor.set_reply_mode(self.guild.id, ReplyMode.TEXT)

        sent = await self._send()

        self.assertEqual(sent["content"], "**Architect:** Hi")
        self.assertNotIn(MEMBER_ID, [a.discord_user_id for a in self.asked])

    async def test_render_reply_carries_the_member_icon_for_reply_mixins(self) -> None:
        """floorplan/pixelagents/cctv call render_reply + build_reply_payload
        themselves."""

        await self._footer_member()

        rendered = await self.corridor.render_reply(self._ctx(), title="Hi")
        _, files = build_reply_payload(rendered)

        self.assertEqual([f.filename for f in files], ["member_mmmm.png"])

    async def test_icons_give_way_to_a_callers_files_past_the_attachment_limit(self) -> None:
        await self._footer_member()
        extra = [
            discord.File(io.BytesIO(b"x"), filename=f"render-{i}.png")
            for i in range(MAX_ATTACHMENTS - 1)
        ]

        sent = await self._send(extra_files=extra)

        names = [f.filename for f in sent["files"]]  # type: ignore[attr-defined]
        self.assertEqual(len(names), MAX_ATTACHMENTS)
        # The author icon stays; the footer's member icon gave way.
        self.assertIn("aaaa.png", names)
        self.assertNotIn("member_mmmm.png", names)


if __name__ == "__main__":
    unittest.main()
