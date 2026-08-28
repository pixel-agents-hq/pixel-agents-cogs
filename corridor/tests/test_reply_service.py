"""ReplyService is fully testable without Red: a plain fake IconResolver
satisfies the protocol, no unittest.mock needed."""

from __future__ import annotations

import unittest

from ..application import ReplyContent, ReplyService
from ..domain import (
    FooterOverride,
    IconPreference,
    IconSource,
    ReplyCategory,
    ReplyField,
    ReplyIdentity,
    ReplyMode,
    ReplyPreferences,
)


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
            1, preferences, ReplyContent(title="Title", description="Body"), prefix=";"
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
            1, preferences, ReplyContent(title="Title", description="Body"), prefix=";"
        )

        self.assertEqual(rendered.embed_title, "Title")
        self.assertEqual(rendered.footer_icon_url, "https://example.com/bot.png")
        self.assertTrue(rendered.show_timestamp)

    async def test_embed_mode_resolves_server_icon_per_guild(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.SERVER),
        )

        rendered = await self.service.render(
            42, preferences, ReplyContent(description="Body"), prefix=";"
        )

        self.assertEqual(rendered.footer_icon_url, "https://example.com/guild/42.png")

    async def test_embed_mode_uses_custom_icon_without_calling_resolver(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.CUSTOM, custom_url="https://example.com/x.png"),
        )

        rendered = await self.service.render(
            1, preferences, ReplyContent(description="Body"), prefix=";"
        )

        self.assertEqual(rendered.footer_icon_url, "https://example.com/x.png")

    async def test_embed_mode_carries_fields_through_unchanged(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )
        fields = (ReplyField("Serving", "yes", False), ReplyField("Clients", "3"))

        rendered = await self.service.render(
            1, preferences, ReplyContent(title="Status", fields=fields), prefix=";"
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
            1, preferences, ReplyContent(description="Status", fields=fields), prefix=";"
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

        rendered = await self.service.render(
            1, preferences, ReplyContent(fields=fields), prefix=";"
        )

        self.assertEqual(rendered.content, "**Serving:** yes")

    async def test_prefix_replaces_p_placeholder_everywhere(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )
        fields = (ReplyField("Hint", "run [p]foo"),)

        rendered = await self.service.render(
            1,
            preferences,
            ReplyContent(title="[p]title", description="See [p]bar", fields=fields),
            prefix=";",
        )

        self.assertEqual(rendered.embed_title, ";title")
        self.assertEqual(rendered.embed_description, "See ;bar")
        self.assertEqual(rendered.fields[0].value, "run ;foo")

    async def test_embed_mode_appends_code_blocks_after_description(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )

        rendered = await self.service.render(
            1,
            preferences,
            ReplyContent(description="Do this:", code=["[p]foo bar"]),
            prefix=";",
        )

        self.assertEqual(rendered.embed_description, "Do this:\n\n```\n;foo bar\n```")

    async def test_text_mode_appends_code_blocks(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.TEXT,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )

        rendered = await self.service.render(
            1,
            preferences,
            ReplyContent(description="Do this:", code=["[p]foo bar"]),
            prefix=";",
        )

        self.assertEqual(rendered.content, "Do this:\n```\n;foo bar\n```")

    async def test_embed_mode_fences_code_field_and_forces_non_inline(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )
        fields = (ReplyField("Fix", "[p]foo bar", True, code=True),)

        rendered = await self.service.render(
            1, preferences, ReplyContent(fields=fields), prefix=";"
        )

        field = rendered.fields[0]
        self.assertEqual(field.value, "```\n;foo bar\n```")
        self.assertFalse(field.inline)

    async def test_text_mode_fences_code_field_on_its_own_line(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.TEXT,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )
        fields = (ReplyField("Fix", "[p]foo bar", code=True),)

        rendered = await self.service.render(
            1, preferences, ReplyContent(fields=fields), prefix=";"
        )

        self.assertEqual(rendered.content, "**Fix:**\n```\n;foo bar\n```")

    async def test_embed_mode_with_no_identity_has_no_author(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )

        rendered = await self.service.render(1, preferences, ReplyContent(), prefix=";")

        self.assertIsNone(rendered.author_name)
        self.assertIsNone(rendered.author_icon_attachment)

    async def test_embed_mode_author_name_always_shows_without_an_avatar(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )

        rendered = await self.service.render(
            1, preferences, ReplyContent(), prefix=";", identity=ReplyIdentity(owner="Architect")
        )

        self.assertEqual(rendered.author_name, "Architect")
        self.assertIsNone(rendered.author_icon_attachment)

    async def test_embed_mode_author_icon_attachment_carried_through(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )
        identity = ReplyIdentity(owner="Architect", avatar_filename="avatar.png")

        rendered = await self.service.render(
            1, preferences, ReplyContent(), prefix=";", identity=identity
        )

        self.assertEqual(rendered.author_name, "Architect")
        self.assertEqual(rendered.author_icon_attachment, "avatar.png")

    async def test_embed_mode_with_no_category_has_no_color(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )

        rendered = await self.service.render(1, preferences, ReplyContent(), prefix=";")

        self.assertIsNone(rendered.category)

    async def test_embed_mode_carries_category_through(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )

        rendered = await self.service.render(
            1, preferences, ReplyContent(), prefix=";", category=ReplyCategory.AGENT
        )

        self.assertEqual(rendered.category, ReplyCategory.AGENT)

    async def test_text_mode_never_carries_a_category(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.TEXT,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )

        rendered = await self.service.render(
            1, preferences, ReplyContent(content="Body"), prefix=";", category=ReplyCategory.ROOM
        )

        self.assertIsNone(rendered.category)

    async def test_embed_mode_footer_override_wins_over_guild_footer(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text="Guild footer",
            icon=IconPreference(source=IconSource.BOT),
        )
        override = FooterOverride(name="architect", icon_url="http://x/architect/avatar.png")

        rendered = await self.service.render(
            1, preferences, ReplyContent(), prefix=";", footer_override=override
        )

        self.assertEqual(rendered.footer_text, "architect")
        self.assertEqual(rendered.footer_icon_url, "http://x/architect/avatar.png")

    async def test_embed_mode_falls_back_to_guild_footer_without_an_override(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.EMBED,
            show_timestamp=False,
            footer_text="Guild footer",
            icon=IconPreference(source=IconSource.BOT),
        )

        rendered = await self.service.render(1, preferences, ReplyContent(), prefix=";")

        self.assertEqual(rendered.footer_text, "Guild footer")
        self.assertEqual(rendered.footer_icon_url, "https://example.com/bot.png")

    async def test_text_mode_prefixes_owner_name(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.TEXT,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )

        rendered = await self.service.render(
            1,
            preferences,
            ReplyContent(description="Body"),
            prefix=";",
            identity=ReplyIdentity(owner="Pico"),
        )

        self.assertEqual(rendered.content, "**Pico:** Body")

    async def test_text_mode_omits_prefix_when_content_is_empty(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.TEXT,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )

        rendered = await self.service.render(
            1, preferences, ReplyContent(), prefix=";", identity=ReplyIdentity(owner="Pico")
        )

        self.assertEqual(rendered.content, "")

    async def test_text_mode_drops_footer_override_entirely(self) -> None:
        preferences = ReplyPreferences(
            mode=ReplyMode.TEXT,
            show_timestamp=False,
            footer_text=None,
            icon=IconPreference(source=IconSource.BOT),
        )
        override = FooterOverride(name="architect", icon_url="http://x/architect/avatar.png")

        rendered = await self.service.render(
            1, preferences, ReplyContent(description="Body"), prefix=";", footer_override=override
        )

        self.assertIsNone(rendered.footer_text)
        self.assertIsNone(rendered.footer_icon_url)
        self.assertEqual(rendered.content, "Body")
