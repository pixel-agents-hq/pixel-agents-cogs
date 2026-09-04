"""The only tests needing the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_permission_service.py / test_reply_service.py)."""

from __future__ import annotations

import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import AgentCapabilities, AgentCard, AgentInterface
from a2a.utils import TransportProtocol

from ..corridor import Corridor
from ..domain import (
    AgentPresenceChanged,
    RegisteredAgent,
    RegisteredTool,
    ReplyField,
    ReplyMode,
    llm_tool,
)
from ..infrastructure import LiteLLMClient
from .conftest import FakeBot, FakeContext, FakeGuild, FakeMember


async def _tool_handler(ctx: object, raw_input: object) -> dict[str, object]:
    return {}


class _DummyExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def _agent(
    agent_key: str,
    *,
    avatar_path: Path | None = None,
    required_permission_group: str | None = None,
) -> RegisteredAgent:
    card = AgentCard(
        name=agent_key,
        description="A test agent.",
        version="0.1.0",
        supported_interfaces=[
            AgentInterface(
                url="http://placeholder/", protocol_binding=TransportProtocol.JSONRPC.value
            )
        ],
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[],
    )
    return RegisteredAgent(
        agent_key=agent_key,
        card=card,
        executor=_DummyExecutor(),
        avatar_path=avatar_path,
        required_permission_group=required_permission_group,
    )


def _recorder(sink: list) -> object:
    async def handler(event: object) -> None:
        sink.append(event)

    return handler


def _tool(
    name: str,
    *,
    required_group: str | None = None,
    availability_check: Callable[[object], Awaitable[bool]] | None = None,
) -> RegisteredTool:
    return RegisteredTool(
        name=name,
        description="A tool.",
        parameters={"type": "object", "properties": {}},
        handler=_tool_handler,
        required_group=required_group,
        availability_check=availability_check,
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

    async def test_llm_settings_defaults_to_unconfigured_key(self) -> None:
        settings = await self.corridor.llm_settings()

        self.assertIsNone(settings.llm_api_key)
        self.assertFalse(settings.ready)

    async def test_set_llm_settings_persist_and_become_ready(self) -> None:
        await self.corridor.set_llm_base_url("https://example.test/")
        await self.corridor.set_llm_api_key("sk-secret")
        await self.corridor.set_llm_model("gpt-test")

        settings = await self.corridor.llm_settings()

        self.assertEqual(settings.llm_base_url, "https://example.test/")
        self.assertEqual(settings.llm_api_key, "sk-secret")
        self.assertEqual(settings.llm_model, "gpt-test")
        self.assertTrue(settings.ready)

    async def test_llm_client_returns_the_same_shared_instance(self) -> None:
        client = self.corridor.llm_client()

        self.assertIsInstance(client, LiteLLMClient)
        self.assertIs(self.corridor.llm_client(), client)

    async def test_cog_unload_closes_the_shared_llm_client(self) -> None:
        client = self.corridor.llm_client()

        await self.corridor.cog_unload()  # must not raise even though never started

        self.assertFalse(client.running)

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

    async def test_unregister_tool_removes_only_that_tool(self) -> None:
        self.corridor.register_tool(_tool("a"), owner="A")
        self.corridor.register_tool(_tool("b"), owner="A")

        self.corridor.unregister_tool("a")

        self.assertEqual({tool.name for tool in self.corridor.list_tools()}, {"b"})

    async def test_unregister_tool_on_an_unregistered_name_is_a_noop(self) -> None:
        self.corridor.register_tool(_tool("a"), owner="A")

        self.corridor.unregister_tool("never registered")

        self.assertEqual({tool.name for tool in self.corridor.list_tools()}, {"a"})

    async def test_list_tools_for_includes_an_ungated_tool_for_any_member(self) -> None:
        self.corridor.register_tool(_tool("a"), owner="A")
        member = FakeMember(2, self.guild)

        allowed = await self.corridor.list_tools_for(FakeContext(author=member, guild=self.guild))

        self.assertEqual({tool.name for tool in allowed}, {"a"})

    async def test_list_tools_for_includes_an_employee_gated_tool_for_any_member(self) -> None:
        self.corridor.register_tool(_tool("a", required_group="employee"), owner="A")
        member = FakeMember(2, self.guild)

        allowed = await self.corridor.list_tools_for(FakeContext(author=member, guild=self.guild))

        self.assertEqual({tool.name for tool in allowed}, {"a"})

    async def test_list_tools_for_excludes_a_tool_the_member_does_not_satisfy(self) -> None:
        # "building_manager" seeds with no roles/permissions assigned, so a
        # plain member fails it by default (see test_require_permission_
        # denies_by_default above).
        self.corridor.register_tool(_tool("a", required_group="building_manager"), owner="A")
        member = FakeMember(2, self.guild)

        allowed = await self.corridor.list_tools_for(FakeContext(author=member, guild=self.guild))

        self.assertEqual(allowed, ())

    async def test_list_tools_for_includes_a_gated_tool_once_the_member_satisfies_it(self) -> None:
        await self.corridor.set_group_role_ids(self.guild.id, "building_manager", frozenset({500}))
        self.corridor.register_tool(_tool("a", required_group="building_manager"), owner="A")
        member = FakeMember(2, self.guild, role_ids=(500,))

        allowed = await self.corridor.list_tools_for(FakeContext(author=member, guild=self.guild))

        self.assertEqual({tool.name for tool in allowed}, {"a"})

    async def test_list_tools_for_uses_the_full_context_for_an_availability_check(self) -> None:
        received: list[object] = []

        async def availability_check(ctx: object) -> bool:
            received.append(ctx)
            return True

        self.corridor.register_tool(_tool("a", availability_check=availability_check), owner="A")
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)

        allowed = await self.corridor.list_tools_for(ctx)

        self.assertEqual({tool.name for tool in allowed}, {"a"})
        self.assertEqual(received, [ctx])

    async def test_list_tools_for_omits_false_or_broken_availability_checks(self) -> None:
        async def denied(ctx: object) -> bool:
            return False

        async def broken(ctx: object) -> bool:
            raise RuntimeError("broken check")

        self.corridor.register_tool(_tool("denied", availability_check=denied), owner="A")
        self.corridor.register_tool(_tool("broken", availability_check=broken), owner="A")
        member = FakeMember(2, self.guild)

        allowed = await self.corridor.list_tools_for(FakeContext(author=member, guild=self.guild))

        self.assertEqual(allowed, ())

    async def test_list_tools_for_is_unaffected_when_no_visibility_filter_is_installed(
        self,
    ) -> None:
        self.corridor.register_tool(_tool("a"), owner="A")
        member = FakeMember(2, self.guild)

        allowed = await self.corridor.list_tools_for(FakeContext(author=member, guild=self.guild))

        self.assertEqual({tool.name for tool in allowed}, {"a"})

    async def test_visibility_filter_can_omit_a_tool_that_passes_every_other_check(self) -> None:
        async def hide_it(ctx: object, tool: RegisteredTool) -> bool:
            return tool.name != "a"

        self.corridor.register_tool(_tool("a"), owner="A")
        self.corridor.register_tool(_tool("b"), owner="A")
        self.corridor.register_tool_visibility_filter(hide_it, owner="Toolbox")
        member = FakeMember(2, self.guild)

        allowed = await self.corridor.list_tools_for(FakeContext(author=member, guild=self.guild))

        self.assertEqual({tool.name for tool in allowed}, {"b"})

    async def test_visibility_filter_receives_the_full_context_and_the_tool(self) -> None:
        received: list[object] = []

        async def record(ctx: object, tool: RegisteredTool) -> bool:
            received.append((ctx, tool.name))
            return True

        self.corridor.register_tool(_tool("a"), owner="A")
        self.corridor.register_tool_visibility_filter(record, owner="Toolbox")
        member = FakeMember(2, self.guild)
        ctx = FakeContext(author=member, guild=self.guild)

        allowed = await self.corridor.list_tools_for(ctx)

        self.assertEqual({tool.name for tool in allowed}, {"a"})
        self.assertEqual(received, [(ctx, "a")])

    async def test_a_broken_visibility_filter_omits_only_that_tool(self) -> None:
        async def broken(ctx: object, tool: RegisteredTool) -> bool:
            raise RuntimeError("broken filter")

        self.corridor.register_tool(_tool("a"), owner="A")
        self.corridor.register_tool_visibility_filter(broken, owner="Toolbox")
        member = FakeMember(2, self.guild)

        allowed = await self.corridor.list_tools_for(FakeContext(author=member, guild=self.guild))

        self.assertEqual(allowed, ())

    async def test_a_tool_must_pass_every_installed_visibility_filter(self) -> None:
        async def allow_a(ctx: object, tool: RegisteredTool) -> bool:
            return tool.name == "a"

        async def allow_b(ctx: object, tool: RegisteredTool) -> bool:
            return tool.name == "b"

        self.corridor.register_tool(_tool("a"), owner="A")
        self.corridor.register_tool(_tool("b"), owner="A")
        self.corridor.register_tool_visibility_filter(allow_a, owner="FilterOne")
        self.corridor.register_tool_visibility_filter(allow_b, owner="FilterTwo")
        member = FakeMember(2, self.guild)

        allowed = await self.corridor.list_tools_for(FakeContext(author=member, guild=self.guild))

        self.assertEqual(allowed, ())

    async def test_re_registering_a_visibility_filter_under_the_same_owner_replaces_it(
        self,
    ) -> None:
        async def hide_everything(ctx: object, tool: RegisteredTool) -> bool:
            return False

        async def allow_everything(ctx: object, tool: RegisteredTool) -> bool:
            return True

        self.corridor.register_tool(_tool("a"), owner="A")
        self.corridor.register_tool_visibility_filter(hide_everything, owner="Toolbox")
        self.corridor.register_tool_visibility_filter(allow_everything, owner="Toolbox")
        member = FakeMember(2, self.guild)

        allowed = await self.corridor.list_tools_for(FakeContext(author=member, guild=self.guild))

        self.assertEqual({tool.name for tool in allowed}, {"a"})

    async def test_unregister_visibility_filter_owner_removes_only_that_owners_filter(
        self,
    ) -> None:
        async def hide_a(ctx: object, tool: RegisteredTool) -> bool:
            return tool.name != "a"

        self.corridor.register_tool(_tool("a"), owner="A")
        self.corridor.register_tool_visibility_filter(hide_a, owner="Toolbox")

        self.corridor.unregister_visibility_filter_owner("Toolbox")

        member = FakeMember(2, self.guild)
        allowed = await self.corridor.list_tools_for(FakeContext(author=member, guild=self.guild))
        self.assertEqual({tool.name for tool in allowed}, {"a"})

    async def test_on_cog_remove_cleans_up_that_cogs_visibility_filter(self) -> None:
        async def hide_a(ctx: object, tool: RegisteredTool) -> bool:
            return tool.name != "a"

        self.corridor.register_tool(_tool("a"), owner="A")
        self.corridor.register_tool_visibility_filter(hide_a, owner="Toolbox")

        fake_cog = type("FakeCog", (), {"qualified_name": "Toolbox"})()
        await self.corridor.on_cog_remove(fake_cog)  # type: ignore[arg-type]

        member = FakeMember(2, self.guild)
        allowed = await self.corridor.list_tools_for(FakeContext(author=member, guild=self.guild))
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
        allowed = await self.corridor.list_tools_for(FakeContext(author=member, guild=self.guild))
        self.assertEqual({tool.name for tool in allowed}, {"a_tool"})

    async def test_register_agent_and_list_agents_roundtrip(self) -> None:
        await self.corridor.register_agent(_agent("architect"), owner="Architect")

        agents = self.corridor.list_agents()

        self.assertEqual([agent.agent_key for agent in agents], ["architect"])

    async def test_register_agent_rewrites_the_cards_url_to_corridors_own_listener(self) -> None:
        await self.corridor.set_a2a_host("127.0.0.1")

        await self.corridor.register_agent(_agent("architect"), owner="Architect")
        await self.corridor.cog_unload()  # stop the real listener set_a2a_host started

        agents = self.corridor.list_agents()
        settings = await self.corridor.a2a_settings()
        expected = f"http://{settings.a2a_host}:{settings.a2a_port}/architect/"
        self.assertEqual(agents[0].card.supported_interfaces[0].url, expected)

    async def test_register_agent_sets_icon_url_when_avatar_path_given(self) -> None:
        await self.corridor.set_a2a_host("127.0.0.1")

        await self.corridor.register_agent(
            _agent("architect", avatar_path=Path("/some/avatar.png")), owner="Architect"
        )
        await self.corridor.cog_unload()

        agents = self.corridor.list_agents()
        settings = await self.corridor.a2a_settings()
        expected = f"http://{settings.a2a_host}:{settings.a2a_port}/architect/avatar.png"
        self.assertEqual(agents[0].card.icon_url, expected)

    async def test_register_agent_leaves_icon_url_unset_without_an_avatar_path(self) -> None:
        await self.corridor.register_agent(_agent("architect"), owner="Architect")

        agents = self.corridor.list_agents()
        self.assertEqual(agents[0].card.icon_url, "")

    async def test_register_agent_forwards_required_permission_group(self) -> None:
        await self.corridor.register_agent(
            _agent("recruiter", required_permission_group="keyholder"), owner="Bootcamp"
        )

        agents = self.corridor.list_agents()
        self.assertEqual(agents[0].required_permission_group, "keyholder")

    async def test_register_agent_leaves_required_permission_group_unset_by_default(self) -> None:
        await self.corridor.register_agent(_agent("architect"), owner="Architect")

        agents = self.corridor.list_agents()
        self.assertIsNone(agents[0].required_permission_group)

    async def test_unregister_agent_owner_removes_only_that_owners_agents(self) -> None:
        await self.corridor.register_agent(_agent("architect"), owner="Architect")
        await self.corridor.register_agent(_agent("agent-n"), owner="AgentN")

        await self.corridor.unregister_agent_owner("Architect")

        self.assertEqual([agent.agent_key for agent in self.corridor.list_agents()], ["agent-n"])

    async def test_unregister_agent_removes_by_key_regardless_of_owner(self) -> None:
        await self.corridor.register_agent(_agent("architect"), owner="Architect")

        await self.corridor.unregister_agent("architect")

        self.assertEqual(self.corridor.list_agents(), ())

    async def test_register_agent_publishes_online_presence(self) -> None:
        received: list[object] = []
        self.corridor.subscribe_event(AgentPresenceChanged, _recorder(received), owner="Test")

        await self.corridor.register_agent(_agent("architect"), owner="Architect")

        self.assertEqual(len(received), 1)
        event = received[0]
        assert isinstance(event, AgentPresenceChanged)
        self.assertEqual(event.status, "online")
        self.assertEqual(event.display_name, "architect")
        self.assertEqual(event.agent.agent_key, "architect")
        self.assertTrue(event.agent.is_bot)
        self.assertIsNone(event.agent.discord_user_id)
        self.assertIsNone(event.agent.guild_id)

    async def test_unregister_agent_owner_publishes_offline_presence_for_each_removed_agent(
        self,
    ) -> None:
        await self.corridor.register_agent(_agent("architect"), owner="Architect")
        await self.corridor.register_agent(_agent("agent-n"), owner="Architect")
        received: list[object] = []
        self.corridor.subscribe_event(AgentPresenceChanged, _recorder(received), owner="Test")

        await self.corridor.unregister_agent_owner("Architect")

        offline_keys = {
            event.agent.agent_key
            for event in received
            if isinstance(event, AgentPresenceChanged) and event.status == "offline"
        }
        self.assertEqual(offline_keys, {"architect", "agent-n"})

    async def test_unregister_agent_publishes_offline_presence(self) -> None:
        await self.corridor.register_agent(_agent("architect"), owner="Architect")
        received: list[object] = []
        self.corridor.subscribe_event(AgentPresenceChanged, _recorder(received), owner="Test")

        await self.corridor.unregister_agent("architect")

        self.assertEqual(len(received), 1)
        event = received[0]
        assert isinstance(event, AgentPresenceChanged)
        self.assertEqual(event.status, "offline")
        self.assertEqual(event.agent.agent_key, "architect")

    async def test_unregister_agent_for_unknown_key_publishes_nothing(self) -> None:
        received: list[object] = []
        self.corridor.subscribe_event(AgentPresenceChanged, _recorder(received), owner="Test")

        await self.corridor.unregister_agent("nobody")

        self.assertEqual(received, [])

    async def test_cog_load_starts_the_shared_a2a_listener(self) -> None:
        await self.corridor.set_a2a_port(8960)
        await self.corridor.cog_unload()  # stop the listener set_a2a_port already started

        await self.corridor.cog_load()
        self.addAsyncCleanup(self.corridor.cog_unload)

        self.assertTrue(self.corridor._a2a_server.running)
