"""The only tests needing the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_permission_service.py / test_reply_service.py)."""

from __future__ import annotations

import unittest

from ..corridor import Corridor
from ..domain import RegisteredTool, ReplyField, ReplyMode, llm_tool
from .conftest import FakeBot, FakeContext, FakeGuild, FakeMember


async def _tool_handler(ctx: object, raw_input: object) -> dict[str, object]:
    return {}


def _tool(name: str, *, required_group: str | None = None) -> RegisteredTool:
    return RegisteredTool(
        name=name,
        description="A tool.",
        parameters={"type": "object", "properties": {}},
        handler=_tool_handler,
        required_group=required_group,
    )


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

    async def test_send_reply_substitutes_p_with_ctx_clean_prefix(self) -> None:
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild, clean_prefix=";")

        await self.corridor.send_reply(ctx, title="Hi", description="Run [p]foo")

        embed = ctx.sent[0]["embed"]
        self.assertEqual(embed.description, "Run ;foo")

    async def test_send_reply_code_renders_a_fenced_block(self) -> None:
        await self.corridor.set_reply_mode(self.guild.id, ReplyMode.TEXT)
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild, clean_prefix=";")

        await self.corridor.send_reply(ctx, description="Do this:", code=["[p]foo"])

        self.assertEqual(ctx.sent[0]["content"], "Do this:\n```\n;foo\n```")

    async def test_render_reply_resolves_prefix_from_ctx_without_a_prefix_argument(self) -> None:
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild, clean_prefix=";")

        rendered = await self.corridor.render_reply(ctx, description="Run [p]foo")

        self.assertEqual(rendered.embed_description, "Run ;foo")

    async def test_default_prefix_uses_the_bots_first_valid_prefix(self) -> None:
        self.bot = FakeBot(owner_ids=frozenset({1}), valid_prefixes=("!", "?"))
        self.corridor = Corridor(bot=self.bot)

        self.assertEqual(await self.corridor.default_prefix(), "!")

    async def test_substitute_default_prefix_replaces_p_with_no_ctx_needed(self) -> None:
        self.bot = FakeBot(owner_ids=frozenset({1}), valid_prefixes=("!",))
        self.corridor = Corridor(bot=self.bot)

        message = await self.corridor.substitute_default_prefix("Run [p]foo")

        self.assertEqual(message, "Run !foo")

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

    async def test_register_tool_and_list_tools_roundtrip(self) -> None:
        tool = _tool("a")

        self.corridor.register_tool(tool, owner="A")

        self.assertEqual(self.corridor.list_tools(), (tool,))

    async def test_unregister_tool_owner_removes_only_that_owners_tools(self) -> None:
        self.corridor.register_tool(_tool("a"), owner="A")
        self.corridor.register_tool(_tool("b"), owner="B")

        self.corridor.unregister_tool_owner("A")

        self.assertEqual({tool.name for tool in self.corridor.list_tools()}, {"b"})

    async def test_list_tools_for_includes_an_ungated_tool_for_any_member(self) -> None:
        self.corridor.register_tool(_tool("a"), owner="A")
        member = FakeMember(2, self.guild)

        allowed = await self.corridor.list_tools_for(member)

        self.assertEqual({tool.name for tool in allowed}, {"a"})

    async def test_list_tools_for_includes_an_employee_gated_tool_for_any_member(self) -> None:
        self.corridor.register_tool(_tool("a", required_group="employee"), owner="A")
        member = FakeMember(2, self.guild)

        allowed = await self.corridor.list_tools_for(member)

        self.assertEqual({tool.name for tool in allowed}, {"a"})

    async def test_list_tools_for_excludes_a_tool_the_member_does_not_satisfy(self) -> None:
        # "building_manager" seeds with no roles/permissions assigned, so a
        # plain member fails it by default (see test_require_permission_
        # denies_by_default above).
        self.corridor.register_tool(_tool("a", required_group="building_manager"), owner="A")
        member = FakeMember(2, self.guild)

        allowed = await self.corridor.list_tools_for(member)

        self.assertEqual(allowed, ())

    async def test_list_tools_for_includes_a_gated_tool_once_the_member_satisfies_it(self) -> None:
        await self.corridor.set_group_role_ids(self.guild.id, "building_manager", frozenset({500}))
        self.corridor.register_tool(_tool("a", required_group="building_manager"), owner="A")
        member = FakeMember(2, self.guild, role_ids=(500,))

        allowed = await self.corridor.list_tools_for(member)

        self.assertEqual({tool.name for tool in allowed}, {"a"})

    async def test_register_llm_tools_scans_a_cog_and_registers_its_decorated_commands(
        self,
    ) -> None:
        class _StubCommand:
            def __init__(self, callback: object) -> None:
                self.callback = callback

        @llm_tool(name="a_tool", description="Does a thing.", required_group="employee")
        async def command(cog: object, ctx: object) -> None:
            return None

        fake_cog = type("FakeCog", (), {})()
        fake_cog.some_command = _StubCommand(command)  # type: ignore[attr-defined]

        self.corridor.register_llm_tools(fake_cog, owner="SomeCog")

        self.assertEqual({tool.name for tool in self.corridor.list_tools()}, {"a_tool"})
        member = FakeMember(2, self.guild)
        allowed = await self.corridor.list_tools_for(member)
        self.assertEqual({tool.name for tool in allowed}, {"a_tool"})
