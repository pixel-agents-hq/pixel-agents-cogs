"""The only tests needing the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_permission_service.py / test_reply_service.py)."""

from __future__ import annotations

import unittest

from ..corridor import Corridor
from ..domain import PermissionGroup, ReplyMode
from .conftest import FakeBot, FakeContext, FakeGuild, FakeMember


class TestCorridorApi(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = FakeBot(owner_ids=frozenset({1}))
        self.guild = FakeGuild(guild_id=10)
        self.bot.register_guild(self.guild)
        self.corridor = Corridor(bot=self.bot)

    async def test_send_reply_defaults_to_embed(self) -> None:
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)

        await self.corridor.send_reply(ctx, title="Hi", description="Body")

        self.assertEqual(len(ctx.sent), 1)
        self.assertIsNotNone(ctx.sent[0]["embed"])
        self.assertIsNone(ctx.sent[0]["content"])

    async def test_send_reply_respects_text_mode(self) -> None:
        await self.corridor.set_reply_mode(self.guild.id, ReplyMode.TEXT)
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)

        await self.corridor.send_reply(ctx, description="Body")

        self.assertEqual(ctx.sent[0]["content"], "Body")
        self.assertIsNone(ctx.sent[0]["embed"])

    async def test_require_permission_denies_by_default(self) -> None:
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)

        allowed = await self.corridor.require_permission(ctx, PermissionGroup.MODERATOR)

        self.assertFalse(allowed)
        self.assertEqual(ctx.sent[0]["content"], "You don't have permission to do that.")

    async def test_require_permission_allows_after_role_granted(self) -> None:
        await self.corridor.set_moderator_role_ids(self.guild.id, frozenset({500}))
        member = FakeMember(2, self.guild, role_ids=(500,))
        ctx = FakeContext(author=member, guild=self.guild)

        allowed = await self.corridor.require_permission(ctx, PermissionGroup.MODERATOR)

        self.assertTrue(allowed)
        self.assertEqual(ctx.sent, [])

    async def test_bot_owner_bypasses_permission_checks(self) -> None:
        owner = FakeMember(1, self.guild)
        ctx = FakeContext(author=owner, guild=self.guild)

        allowed = await self.corridor.require_permission(ctx, PermissionGroup.PRIVILEGED)

        self.assertTrue(allowed)
