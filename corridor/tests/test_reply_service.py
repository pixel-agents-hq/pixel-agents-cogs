"""ReplyService is fully testable without Red: a plain fake IconResolver
satisfies the protocol, no unittest.mock needed."""

from __future__ import annotations

import unittest

from ..application import ReplyContent, ReplyService
from ..domain import IconPreference, IconSource, ReplyField, ReplyMode, ReplyPreferences


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

    async def test_embed_mode_carries_fields_through_unchanged(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )
        fields = (ReplyField("Serving", "yes", False), ReplyField("Clients", "3"))

        rendered = await self.service.render(
            1, preferences, ReplyContent(title="Status", fields=fields)
        )

        self.assertEqual(rendered.fields, fields)

    async def test_text_mode_flattens_fields_to_lines(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.TEXT,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )
        fields = (ReplyField("Serving", "yes"), ReplyField("Clients", "3"))

        rendered = await self.service.render(
            1, preferences, ReplyContent(description="Status", fields=fields)
        )

        self.assertEqual(rendered.content, "Status\n**Serving:** yes\n**Clients:** 3")
        self.assertEqual(rendered.fields, ())

    async def test_text_mode_with_only_fields_omits_leading_blank_line(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.TEXT,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )
        fields = (ReplyField("Serving", "yes"),)

        rendered = await self.service.render(1, preferences, ReplyContent(fields=fields))

        self.assertEqual(rendered.content, "**Serving:** yes")
