"""The only tests needing the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_permission_service.py / test_reply_service.py)."""

from __future__ import annotations

import unittest

from ..corridor import Corridor
from ..domain import ReplyField, ReplyMode
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

    async def test_send_reply_embed_carries_fields(self) -> None:
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)

        await self.corridor.send_reply(
            ctx,
            title="Status",
            fields=[ReplyField("Serving", "yes", False), ReplyField("Clients", "3")],
        )

        embed = ctx.sent[0]["embed"]
        self.assertEqual(
            [call.kwargs for call in embed.add_field.call_args_list],
            [
                {"name": "Serving", "value": "yes", "inline": False},
                {"name": "Clients", "value": "3", "inline": True},
            ],
        )

    async def test_send_reply_text_mode_flattens_fields(self) -> None:
        await self.corridor.set_reply_mode(self.guild.id, ReplyMode.TEXT)
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)

        await self.corridor.send_reply(ctx, title="Status", fields=[ReplyField("Serving", "yes")])

        self.assertEqual(ctx.sent[0]["content"], "Status\n**Serving:** yes")

    async def test_require_permission_denies_by_default(self) -> None:
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)

        allowed = await self.corridor.require_permission(ctx, "building_manager")

        self.assertFalse(allowed)
        self.assertEqual(ctx.sent[0]["content"], "You don't have permission to do that.")

    async def test_require_permission_allows_after_role_granted(self) -> None:
        await self.corridor.set_group_role_ids(self.guild.id, "building_manager", frozenset({500}))
        member = FakeMember(2, self.guild, role_ids=(500,))
        ctx = FakeContext(author=member, guild=self.guild)

        allowed = await self.corridor.require_permission(ctx, "building_manager")

        self.assertTrue(allowed)
        self.assertEqual(ctx.sent, [])

    async def test_require_permission_allows_after_discord_permission_granted(self) -> None:
        await self.corridor.set_group_permissions(
            self.guild.id, "building_manager", frozenset({"kick_members"})
        )
        member = FakeMember(2, self.guild, permission_names=frozenset({"kick_members"}))
        ctx = FakeContext(author=member, guild=self.guild)

        allowed = await self.corridor.require_permission(ctx, "building_manager")

        self.assertTrue(allowed)
        self.assertEqual(ctx.sent, [])

    async def test_require_permission_denies_without_matching_role_or_permission(self) -> None:
        await self.corridor.set_group_role_ids(self.guild.id, "building_manager", frozenset({500}))
        await self.corridor.set_group_permissions(
            self.guild.id, "building_manager", frozenset({"kick_members"})
        )
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)

        allowed = await self.corridor.require_permission(ctx, "building_manager")

        self.assertFalse(allowed)

    async def test_bot_owner_bypasses_permission_checks(self) -> None:
        owner = FakeMember(1, self.guild)
        ctx = FakeContext(author=owner, guild=self.guild)

        allowed = await self.corridor.require_permission(ctx, "keyholder")

        self.assertTrue(allowed)

    async def test_guild_administrator_bypasses_permission_checks(self) -> None:
        admin = FakeMember(2, self.guild, is_administrator=True)
        ctx = FakeContext(author=admin, guild=self.guild)

        allowed = await self.corridor.require_permission(ctx, "keyholder")

        self.assertTrue(allowed)

    async def test_add_rename_and_remove_permission_group(self) -> None:
        await self.corridor.add_permission_group(self.guild.id, "hr", "HR")
        groups = await self.corridor.list_permission_groups(self.guild.id)
        self.assertIn("hr", {group.key for group in groups})

        await self.corridor.set_group_label(self.guild.id, "hr", "Human Resources")
        member = FakeMember(3, self.guild, role_ids=())
        ctx = FakeContext(author=member, guild=self.guild)
        self.assertFalse(await self.corridor.require_permission(ctx, "hr"))

        await self.corridor.remove_permission_group(self.guild.id, "hr")
        groups = await self.corridor.list_permission_groups(self.guild.id)
        self.assertNotIn("hr", {group.key for group in groups})
