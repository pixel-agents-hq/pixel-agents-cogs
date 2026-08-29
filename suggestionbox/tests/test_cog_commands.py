"""The only tests that need the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_domain_feedback.py / test_feedback_service.py /
test_mcp_server.py).

`cog_load()` here binds a real MCP listener (see `adapters/cog_base.py`'s
`_restart_mcp`) -- every test that calls it pairs the bind with
`addAsyncCleanup(self.cog.cog_unload)` so the port is always released
before the next test binds it again, the same real-bind-then-real-release
discipline `corridor/tests/test_a2a_server.py` already establishes for the
same class of listener.
"""

from __future__ import annotations

import socket
import unittest

from redbot.core.errors import CogLoadError

from .. import setup
from ..suggestionbox import Suggestionbox
from .conftest import FakeBot, FakeChannel, FakeContext, FakeCorridor


class TestCommandsAreOwnerGated(unittest.TestCase):
    """A `@commands.is_owner()` decorator on a parent hybrid_group does NOT
    propagate to its subcommands in discord.py -- every other cog in this
    repo (toolbox, architect, ...) decorates each subcommand individually
    for exactly this reason. Regression guard: an earlier revision of
    commands.py only decorated `suggestionbox_group` itself, leaving
    channel/mcp host/mcp port/agents runnable by anyone despite this cog's
    own "every command here is bot-owner-only" design."""

    def setUp(self) -> None:
        self.cog = Suggestionbox(bot=FakeBot())

    def test_suggestionbox_group_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.suggestionbox_group.callback, "__is_owner__", False))

    def test_channel_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.channel.callback, "__is_owner__", False))

    def test_mcp_group_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.mcp_group.callback, "__is_owner__", False))

    def test_mcp_host_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.mcp_host.callback, "__is_owner__", False))

    def test_mcp_port_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.mcp_port.callback, "__is_owner__", False))

    def test_agents_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.agents.callback, "__is_owner__", False))


class TestChannelCommand(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot(FakeCorridor())
        self.cog = Suggestionbox(bot=self.bot)
        await self.cog.cog_load()
        self.addAsyncCleanup(self.cog.cog_unload)
        self.ctx = FakeContext()

    async def test_channel_sets_the_feedback_channel_and_replies(self) -> None:
        channel = FakeChannel(channel_id=555)

        await self.cog.channel.callback(self.cog, self.ctx, channel)

        self.assertEqual(await self.cog._repository.feedback_channel(), (self.ctx.guild.id, 555))
        self.assertIn("<#555>", str(self.bot.corridor.replies[-1]["description"]))


class TestMcpCommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot(FakeCorridor())
        self.cog = Suggestionbox(bot=self.bot)
        await self.cog.cog_load()
        self.addAsyncCleanup(self.cog.cog_unload)
        self.ctx = FakeContext()

    async def test_mcp_port_rejects_an_out_of_range_port(self) -> None:
        await self.cog.mcp_port.callback(self.cog, self.ctx, 0)

        self.assertIn("between 1 and 65535", str(self.bot.corridor.replies[-1]["description"]))
        self.assertEqual((await self.cog._repository.mcp_listener())[1], 8934)

    async def test_mcp_port_restarts_the_listener_on_the_new_port(self) -> None:
        await self.cog.mcp_port.callback(self.cog, self.ctx, 8941)

        self.assertEqual(await self.cog._repository.mcp_listener(), ("127.0.0.1", 8941))
        self.assertIn("8941", str(self.bot.corridor.replies[-1]["description"]))
        # Re-registered under the new base_url, and the old one dropped.
        self.assertIn(
            "http://127.0.0.1:8941/mcp",
            [server.base_url for server, _owner in self.bot.corridor.registered_mcp_servers],
        )
        self.assertIn("http://127.0.0.1:8934/mcp", self.bot.corridor.unregistered_mcp_server_urls)

    async def test_mcp_host_restarts_the_listener_on_the_new_host(self) -> None:
        await self.cog.mcp_host.callback(self.cog, self.ctx, "127.0.0.2")

        self.assertEqual(await self.cog._repository.mcp_listener(), ("127.0.0.2", 8934))
        self.assertIn("127.0.0.2", str(self.bot.corridor.replies[-1]["description"]))


class TestAgentsCommand(unittest.IsolatedAsyncioTestCase):
    async def test_agents_sends_a_components_v2_view(self) -> None:
        bot = FakeBot(FakeCorridor(agent_keys=("architect",)))
        cog = Suggestionbox(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)
        ctx = FakeContext()

        await cog.agents.callback(cog, ctx)

        self.assertEqual(len(ctx.sent), 1)


class TestMcpRegistration(unittest.IsolatedAsyncioTestCase):
    """cog_load registers this cog's own MCP server with corridor's
    AgentToolServerRegistry, and cog_unload tears that registration down --
    same lifecycle convention `register_dependent`/`unregister_dependent`
    already establishes."""

    async def test_cog_load_registers_the_mcp_server(self) -> None:
        bot = FakeBot(FakeCorridor())
        cog = Suggestionbox(bot=bot)

        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        [(server, owner)] = bot.corridor.registered_mcp_servers
        self.assertEqual(owner, "Suggestionbox")
        self.assertEqual(server.base_url, "http://127.0.0.1:8934/mcp")

    async def test_cog_unload_unregisters_the_mcp_server(self) -> None:
        bot = FakeBot(FakeCorridor())
        cog = Suggestionbox(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        self.assertIn("http://127.0.0.1:8934/mcp", bot.corridor.unregistered_mcp_server_urls)

    async def test_bind_failure_notifies_owners_and_does_not_raise(self) -> None:
        bot = FakeBot(FakeCorridor())
        cog = Suggestionbox(bot=bot)
        # An OS-assigned (port 0) blocker, not a hardcoded port -- this
        # environment can have unrelated processes already bound to a
        # fixed high port, and an ephemeral one is guaranteed free.
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        await cog._repository.set_mcp_port(port)
        try:
            await cog.cog_load()  # must not raise
            self.addAsyncCleanup(cog.cog_unload)
        finally:
            blocker.close()

        self.assertEqual(len(bot.owner_messages), 1)
        self.assertIn("MCP listener failed to start", bot.owner_messages[0])
        self.assertEqual(bot.corridor.registered_mcp_servers, [])


class TestFeedbackPosting(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot(FakeCorridor())
        self.cog = Suggestionbox(bot=self.bot)
        await self.cog.cog_load()
        self.addAsyncCleanup(self.cog.cog_unload)

    async def test_post_feedback_sends_through_corridors_channel_reply(self) -> None:
        channel = FakeChannel(channel_id=42)
        self.bot.channels[42] = channel

        posted = await self.cog._post_feedback(
            10, 42, "Error report", "something broke", [("Source", "architect")]
        )

        self.assertTrue(posted)
        [reply] = self.bot.corridor.channel_replies
        self.assertEqual(reply["channel"], channel)
        self.assertEqual(reply["guild_id"], 10)
        self.assertEqual(reply["title"], "Error report")
        self.assertEqual([f.name for f in reply["fields"]], ["Source"])  # type: ignore[union-attr]

    async def test_post_feedback_with_an_unresolvable_channel_fails_closed(self) -> None:
        posted = await self.cog._post_feedback(10, 999, "Error report", "x", [])

        self.assertFalse(posted)
        self.assertEqual(self.bot.corridor.channel_replies, [])


class TestCogLoadAutoLoadsCorridor(unittest.IsolatedAsyncioTestCase):
    """required_cogs in info.json only tells Downloader what to install --
    Red does not auto-load a dependency at runtime just because it's
    declared there. Regression test for: unload corridor, then load this
    cog -> it must pull corridor back in instead of failing to load."""

    async def test_cog_load_loads_corridor_when_not_already_loaded(self) -> None:
        bot = FakeBot(preloaded=False)
        cog = Suggestionbox(bot=bot)
        self.assertIsNone(bot.get_cog("Corridor"))

        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        self.assertEqual(bot._cog_mgr.find_cog_calls, ["corridor"])
        self.assertEqual(bot.load_extension_calls, ["corridor"])
        self.assertEqual(bot.loaded_packages, ["corridor"])
        self.assertIsNotNone(cog._corridor)

    async def test_package_setup_loads_corridor_before_adding_the_cog(self) -> None:
        bot = FakeBot(preloaded=False)

        await setup(bot)
        self.addAsyncCleanup(bot.add_cog_calls[0].cog_unload)

        self.assertEqual(bot.load_extension_calls, ["corridor"])
        self.assertEqual(bot.loaded_packages, ["corridor"])
        self.assertEqual(len(bot.add_cog_calls), 1)
        self.assertIs(bot.add_cog_calls[0]._corridor, bot.corridor)

    async def test_missing_corridor_reports_a_user_facing_load_error(self) -> None:
        bot = FakeBot(preloaded=False, corridor_installable=False)

        with self.assertRaisesRegex(CogLoadError, "not installed"):
            await Suggestionbox(bot=bot).cog_load()

        self.assertEqual(bot.load_extension_calls, [])


class TestDependentRegistration(unittest.IsolatedAsyncioTestCase):
    """Regression test for: unloading corridor left dependent cogs like this
    one running with a stale corridor reference instead of also being
    unloaded. cog_load/cog_unload must keep corridor's dependent registry in
    sync so corridor's own cog_unload can cascade correctly."""

    async def test_cog_load_registers_with_corridor(self) -> None:
        bot = FakeBot(FakeCorridor())
        cog = Suggestionbox(bot=bot)

        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        self.assertIn("suggestionbox", bot.corridor.registered_dependents)

    async def test_cog_unload_unregisters_from_corridor(self) -> None:
        bot = FakeBot(FakeCorridor())
        cog = Suggestionbox(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        self.assertNotIn("suggestionbox", bot.corridor.registered_dependents)


if __name__ == "__main__":
    unittest.main()
