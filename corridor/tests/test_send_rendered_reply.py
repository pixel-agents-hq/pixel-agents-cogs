"""build_reply_payload/send_rendered_reply: the RenderedReply -> real
discord.Embed/attachment translation. discord.Embed/discord.File are
stubbed as MagicMock by corridor.testing.install_stubs -- this exercises
the *calls* made against them (set_author/set_footer/File(...)), not real
Discord wire behavior."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ..adapters.api import build_reply_payload, send_rendered_reply
from ..domain import REPLY_CATEGORY_COLORS, ReplyCategory, ReplyMode
from ..domain.models import RenderedReply
from .conftest import FakeContext, FakeGuild, FakeMember


def _reply(
    *,
    mode: ReplyMode = ReplyMode.EMBED,
    author_name: str | None = None,
    author_icon_attachment: str | None = None,
    footer_text: str | None = None,
    footer_icon_url: str | None = None,
    content: str | None = None,
    category: ReplyCategory | None = None,
) -> RenderedReply:
    return RenderedReply(
        mode=mode,
        content=content,
        embed_title="Title" if mode is ReplyMode.EMBED else None,
        embed_description="Body" if mode is ReplyMode.EMBED else None,
        fields=(),
        footer_text=footer_text,
        footer_icon_url=footer_icon_url,
        show_timestamp=False,
        author_name=author_name,
        author_icon_attachment=author_icon_attachment,
        category=category,
    )


class TestBuildReplyPayload(unittest.TestCase):
    def test_text_mode_ignores_avatar_path(self) -> None:
        kwargs, files = build_reply_payload(
            _reply(mode=ReplyMode.TEXT, content="hi"), avatar_path=Path("/nonexistent")
        )

        self.assertEqual(kwargs, {"content": "hi"})
        self.assertEqual(files, [])

    def test_no_identity_never_calls_set_author(self) -> None:
        kwargs, _files = build_reply_payload(_reply())

        kwargs["embed"].set_author.assert_not_called()

    def test_author_name_always_set_without_an_avatar(self) -> None:
        kwargs, files = build_reply_payload(_reply(author_name="Architect"))

        kwargs["embed"].set_author.assert_called_once_with(name="Architect", icon_url=None)
        self.assertEqual(files, [])

    def test_avatar_attached_and_referenced_when_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            avatar_path = Path(tmp) / "avatar.png"
            avatar_path.write_bytes(b"fake-png-bytes")

            kwargs, files = build_reply_payload(
                _reply(author_name="Architect", author_icon_attachment="avatar.png"),
                avatar_path=avatar_path,
            )

            self.assertEqual(len(files), 1)
            kwargs["embed"].set_author.assert_called_once_with(
                name="Architect", icon_url="attachment://avatar.png"
            )

    def test_no_attachment_when_avatar_path_does_not_exist(self) -> None:
        kwargs, files = build_reply_payload(
            _reply(author_name="Architect", author_icon_attachment="avatar.png"),
            avatar_path=Path("/definitely/does/not/exist/avatar.png"),
        )

        self.assertEqual(files, [])
        kwargs["embed"].set_author.assert_called_once_with(name="Architect", icon_url=None)

    def test_no_attachment_when_avatar_path_is_none(self) -> None:
        kwargs, files = build_reply_payload(
            _reply(author_name="Architect", author_icon_attachment="avatar.png"), avatar_path=None
        )

        self.assertEqual(files, [])
        kwargs["embed"].set_author.assert_called_once_with(name="Architect", icon_url=None)

    def test_footer_set_from_footer_text_and_footer_icon_url(self) -> None:
        kwargs, _files = build_reply_payload(
            _reply(footer_text="architect", footer_icon_url="http://x/architect/avatar.png")
        )

        kwargs["embed"].set_footer.assert_called_once_with(
            text="architect", icon_url="http://x/architect/avatar.png"
        )

    def test_no_footer_call_without_footer_text(self) -> None:
        kwargs, _files = build_reply_payload(_reply())

        kwargs["embed"].set_footer.assert_not_called()

    def test_no_category_sets_no_color(self) -> None:
        kwargs, _files = build_reply_payload(_reply())

        self.assertIsNone(kwargs["embed"].color)

    def test_category_sets_its_mapped_color(self) -> None:
        kwargs, _files = build_reply_payload(_reply(category=ReplyCategory.AGENT))

        self.assertEqual(kwargs["embed"].color, REPLY_CATEGORY_COLORS[ReplyCategory.AGENT])


class TestSendRenderedReply(unittest.IsolatedAsyncioTestCase):
    async def test_embed_sent_with_empty_files_when_no_avatar(self) -> None:
        guild = FakeGuild(guild_id=1)
        ctx = FakeContext(author=FakeMember(2, guild), guild=guild)

        await send_rendered_reply(ctx, _reply(author_name="Architect"))

        self.assertEqual(ctx.sent[0]["files"], [])

    async def test_text_reply_sent_as_plain_content(self) -> None:
        guild = FakeGuild(guild_id=1)
        ctx = FakeContext(author=FakeMember(2, guild), guild=guild)

        await send_rendered_reply(ctx, _reply(mode=ReplyMode.TEXT, content="hi"))

        self.assertEqual(ctx.sent[0]["content"], "hi")


if __name__ == "__main__":
    unittest.main()
