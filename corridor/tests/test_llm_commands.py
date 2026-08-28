"""`[p]corridor llm ...` -- moved here from pico's former `[p]pico llm ...`
group, see docs/architect-design.md. Owner-gated vs unrestricted commands
are asserted by introspecting the `__is_owner__` tag the shared redbot stub
(`corridor/testing.py`) attaches -- Red's real check machinery isn't
exercised here, only which decorator each command carries."""

from __future__ import annotations

import unittest

from ..corridor import Corridor
from ..domain import REPLY_CATEGORY_COLORS, ReplyCategory
from .conftest import FakeBot, FakeContext, FakeGuild, FakeMember


def _descriptions(ctx: FakeContext) -> list[str | None]:
    """`send_reply(..., description=...)` renders into the embed's
    `.description` under the default ReplyMode.EMBED, not `content`."""

    return [
        entry["content"] if entry.get("embed") is None else entry["embed"].description
        for entry in ctx.sent
    ]


class TestLLMCommandsAreOwnerGated(unittest.TestCase):
    def setUp(self) -> None:
        self.corridor = Corridor(bot=FakeBot())

    def test_llm_group_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.corridor.llm_group.callback, "__is_owner__", False))

    def test_llm_endpoint_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.corridor.llm_endpoint.callback, "__is_owner__", False))

    def test_llm_key_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.corridor.llm_key.callback, "__is_owner__", False))

    def test_llm_model_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.corridor.llm_model.callback, "__is_owner__", False))

    def test_status_is_not_owner_gated(self) -> None:
        self.assertFalse(getattr(self.corridor.corridor_status.callback, "__is_owner__", False))


class TestLLMCommands(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = FakeBot(owner_ids=frozenset({1}))
        self.guild = FakeGuild(guild_id=10)
        self.bot.register_guild(self.guild)
        self.corridor = Corridor(bot=self.bot)
        self.member = FakeMember(1, self.guild)
        self.ctx = FakeContext(author=self.member, guild=self.guild)

    async def test_llm_endpoint_updates_and_replies(self) -> None:
        await self.corridor.llm_endpoint.callback(self.corridor, self.ctx, "https://example.test/")

        settings = await self.corridor.llm_settings()
        self.assertEqual(settings.llm_base_url, "https://example.test/")
        self.assertEqual(
            _descriptions(self.ctx)[-1], "LLM endpoint set to `https://example.test/`."
        )

    async def test_llm_key_updates_and_deletes_the_invoking_message(self) -> None:
        await self.corridor.llm_key.callback(self.corridor, self.ctx, "sk-secret")

        settings = await self.corridor.llm_settings()
        self.assertEqual(settings.llm_api_key, "sk-secret")
        self.assertTrue(self.ctx.message.deleted)
        self.assertEqual(_descriptions(self.ctx)[-1], "LLM virtual key updated.")
        sent_text = "".join(d or "" for d in _descriptions(self.ctx))
        self.assertNotIn("sk-secret", sent_text)

    async def test_llm_model_updates_and_replies(self) -> None:
        await self.corridor.llm_model.callback(self.corridor, self.ctx, "gpt-test")

        settings = await self.corridor.llm_settings()
        self.assertEqual(settings.llm_model, "gpt-test")

    async def test_llm_endpoint_reply_is_colored_room(self) -> None:
        """corridor binds its own ReplySender in CogBase.__init__ (owner
        "Corridor", category=ReplyCategory.ROOM) the same way every
        dependent cog binds its own -- see commands.py's module docstring
        and docs/embed-colors.md -- so its own replies pick up both the
        author name and the shared Room color with nothing repeated at
        each of its own send_reply call sites."""

        await self.corridor.llm_endpoint.callback(self.corridor, self.ctx, "https://example.test/")

        embed = self.ctx.sent[-1]["embed"]
        self.assertEqual(embed.color, REPLY_CATEGORY_COLORS[ReplyCategory.ROOM])
        embed.set_author.assert_called_once_with(name="Corridor", icon_url=None)

    async def test_status_masks_the_key_when_set(self) -> None:
        await self.corridor.llm_key.callback(self.corridor, self.ctx, "sk-super-secret")

        await self.corridor.corridor_status.callback(self.corridor, self.ctx)

        embed = self.ctx.sent[-1]["embed"]
        key_field = next(f for f in embed.add_field.call_args_list if f.kwargs["name"] == "LLM Key")
        self.assertNotIn("sk-super-secret", key_field.kwargs["value"])

    async def test_status_shows_unset_placeholder_when_no_key(self) -> None:
        await self.corridor.corridor_status.callback(self.corridor, self.ctx)

        embed = self.ctx.sent[-1]["embed"]
        key_field = next(f for f in embed.add_field.call_args_list if f.kwargs["name"] == "LLM Key")
        self.assertEqual(key_field.kwargs["value"], "*(not set)*")
