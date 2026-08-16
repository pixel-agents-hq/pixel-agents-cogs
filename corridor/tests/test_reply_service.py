"""ReplyService is fully testable without Red: a plain fake IconResolver
satisfies the protocol, no unittest.mock needed."""

from __future__ import annotations

import unittest

from ..application import ReplyContent, ReplyService
from ..domain import IconPreference, IconSource, ReplyMode, ReplyPreferences


class FakeIconResolver:
    async def bot_icon_url(self) -> str | None:
        return "https://example.com/bot.png"

    async def guild_icon_url(self, guild_id: int) -> str | None:
        return f"https://example.com/guild/{guild_id}.png"


class TestReplyService(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.service = ReplyService(FakeIconResolver())

    async def test_text_mode_ignores_embed_fields(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.TEXT,
            show_timestamp=True,
            footer_text="ignored",
            icon=IconPreference(source=IconSource.BOT),
        )

        rendered = await self.service.render(
            1, preferences, ReplyContent(title="Title", description="Body")
        )

        self.assertEqual(rendered.mode, ReplyMode.TEXT)
        self.assertEqual(rendered.content, "Body")
        self.assertIsNone(rendered.embed_title)

    async def test_embed_mode_resolves_bot_icon(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=True,
            footer_text="Footer",
            icon=IconPreference(source=IconSource.BOT),
        )

        rendered = await self.service.render(
            1, preferences, ReplyContent(title="Title", description="Body")
        )

        self.assertEqual(rendered.embed_title, "Title")
        self.assertEqual(rendered.icon_url, "https://example.com/bot.png")
        self.assertTrue(rendered.show_timestamp)

    async def test_embed_mode_resolves_server_icon_per_guild(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.SERVER),
        )

        rendered = await self.service.render(42, preferences, ReplyContent(description="Body"))

        self.assertEqual(rendered.icon_url, "https://example.com/guild/42.png")

    async def test_embed_mode_uses_custom_icon_without_calling_resolver(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.CUSTOM, custom_url="https://example.com/x.png"),
        )

        rendered = await self.service.render(1, preferences, ReplyContent(description="Body"))

        self.assertEqual(rendered.icon_url, "https://example.com/x.png")
