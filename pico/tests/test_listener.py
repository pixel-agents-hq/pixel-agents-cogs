"""`_message_text`: plain content plus a text rendering of embeds, so
embed-only messages (corridor's own EMBED reply mode, or other bots'
embeds) don't show up as empty history entries.

Uses bare `SimpleNamespace` stand-ins for `discord.Message`/`discord.Embed`
rather than the real types -- `_message_text` only reads `.content` and
`.embeds` (and each embed's `.title`/`.description`/`.fields`), and the
project-wide test stub (`corridor.testing.install_stubs`) replaces
`discord.Embed` with a bare `MagicMock` class anyway, whose instances don't
carry constructor kwargs as attributes."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from ..adapters.listener import _message_text


def _field(name: str, value: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, value=value)


def _embed(
    title: str | None = None,
    description: str | None = None,
    fields: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(title=title, description=description, fields=fields or [])


def _message(content: str = "", embeds: list[SimpleNamespace] | None = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, embeds=embeds or [])


class TestMessageText(unittest.TestCase):
    def test_plain_content_passes_through(self) -> None:
        self.assertEqual(_message_text(_message("hello there")), "hello there")

    def test_embed_only_message_is_not_empty(self) -> None:
        embed = _embed(title="Title", description="Description")
        message = _message(content="", embeds=[embed])

        self.assertEqual(_message_text(message), "Title\nDescription")

    def test_embed_fields_are_rendered(self) -> None:
        embed = _embed(title="Status", fields=[_field("Enabled", "true")])
        message = _message(embeds=[embed])

        self.assertEqual(_message_text(message), "Status\n**Enabled:** true")

    def test_content_and_embed_are_both_included(self) -> None:
        embed = _embed(description="extra detail")
        message = _message(content="here's a reply", embeds=[embed])

        self.assertEqual(_message_text(message), "here's a reply\nextra detail")

    def test_no_content_and_no_embeds_is_empty(self) -> None:
        self.assertEqual(_message_text(_message()), "")

    def test_embed_with_no_title_or_description_falls_back_to_fields_only(self) -> None:
        embed = _embed(fields=[_field("Key", "Value")])
        message = _message(embeds=[embed])

        self.assertEqual(_message_text(message), "**Key:** Value")

    def test_multiple_embeds_are_all_included(self) -> None:
        message = _message(embeds=[_embed(title="First"), _embed(title="Second")])

        self.assertEqual(_message_text(message), "First\nSecond")
