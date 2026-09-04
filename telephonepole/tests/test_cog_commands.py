"""The only tests that need the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_domain_models.py / test_application_service.py)."""

from __future__ import annotations

import unittest

from redbot.core.errors import CogLoadError

from .. import setup
from ..telephonepole import Telephonepole
from .conftest import FakeBot, FakeContext, FakeCorridor


class TestAddRemoveList(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Telephonepole(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_list_reports_no_servers_initially(self) -> None:
        await self.cog.list_servers.callback(self.cog, self.ctx)

        self.assertEqual(
            self.bot.corridor.replies,
            [
                {
                    "title": "MCP servers",
                    "description": "No third-party MCP servers are registered yet.",
                    "content": None,
                }
            ],
        )

    async def test_add_registers_with_corridor_and_replies(self) -> None:
        await self.cog.add.callback(self.cog, self.ctx, "freecad", "http://freecad-mcp:8765/mcp")

        self.assertIn("http://freecad-mcp:8765/mcp", self.bot.corridor.registered_servers)
        reply = self.bot.corridor.replies[-1]
        self.assertEqual(reply["title"], "Add MCP server")
        self.assertIn("Registered `freecad`", reply["description"])

    async def test_add_reports_a_registrar_failure(self) -> None:
        self.bot.corridor = FakeCorridor(register_error="connection refused")
        await self.cog.cog_load()

        await self.cog.add.callback(self.cog, self.ctx, "freecad", "http://freecad-mcp:8765/mcp")

        reply = self.bot.corridor.replies[-1]
        self.assertEqual(reply["title"], "Add MCP server")
        self.assertIn("connection refused", reply["description"])

    async def test_add_rejects_a_duplicate_name(self) -> None:
        await self.cog.add.callback(self.cog, self.ctx, "freecad", "http://freecad-mcp:8765/mcp")

        await self.cog.add.callback(self.cog, self.ctx, "freecad", "http://other-host:9000/mcp")

        reply = self.bot.corridor.replies[-1]
        self.assertIn("already registered", reply["description"])

    async def test_list_shows_a_registered_server(self) -> None:
        await self.cog.add.callback(self.cog, self.ctx, "freecad", "http://freecad-mcp:8765/mcp")

        await self.cog.list_servers.callback(self.cog, self.ctx)

        reply = self.bot.corridor.replies[-1]
        self.assertIn("`freecad` -> `http://freecad-mcp:8765/mcp`", reply["description"])

    async def test_remove_unregisters_with_corridor_and_replies(self) -> None:
        await self.cog.add.callback(self.cog, self.ctx, "freecad", "http://freecad-mcp:8765/mcp")

        await self.cog.remove.callback(self.cog, self.ctx, "freecad")

        self.assertNotIn("http://freecad-mcp:8765/mcp", self.bot.corridor.registered_servers)
        reply = self.bot.corridor.replies[-1]
        self.assertEqual(reply["title"], "Remove MCP server")
        self.assertIn("Removed `freecad`", reply["description"])

    async def test_remove_reports_an_unknown_name(self) -> None:
        await self.cog.remove.callback(self.cog, self.ctx, "does-not-exist")

        reply = self.bot.corridor.replies[-1]
        self.assertIn("no server named", reply["description"])

    async def test_agents_reports_an_unknown_name(self) -> None:
        await self.cog.agents.callback(self.cog, self.ctx, "does-not-exist")

        reply = self.bot.corridor.replies[-1]
        self.assertIn("No server named", reply["description"])
        self.assertEqual(self.ctx.sent, [])

    async def test_agents_opens_a_panel_for_a_registered_server(self) -> None:
        await self.cog.add.callback(self.cog, self.ctx, "freecad", "http://freecad-mcp:8765/mcp")

        await self.cog.agents.callback(self.cog, self.ctx, "freecad")

        self.assertEqual(len(self.ctx.sent), 1)
        self.assertIsNotNone(self.ctx.sent[0]["view"])


class TestCogLoadRestoresPersistedServers(unittest.IsolatedAsyncioTestCase):
    """Corridor's in-memory registry does not survive a bot restart even
    though this cog's own Config does -- cog_load must re-register every
    persisted server. The fake Config (corridor/testing.py) always hands
    back a fresh, independent store per `Config.get_conf` call, so a real
    "new process" restart can't be simulated by constructing a second Cog
    instance here -- instead this reuses one Cog's repository (its Config
    is created once, in `__init__`) and calls `cog_load` on it a second
    time, with corridor's own registry reset in between to stand in for
    corridor having forgotten everything across a real restart."""

    async def test_persisted_servers_are_re_registered_on_the_next_load(self) -> None:
        bot = FakeBot()
        cog = Telephonepole(bot=bot)
        await cog.cog_load()
        ctx = FakeContext()
        await cog.add.callback(cog, ctx, "freecad", "http://freecad-mcp:8765/mcp")
        bot.corridor.registered_servers.clear()

        await cog.cog_load()

        self.assertIn("http://freecad-mcp:8765/mcp", bot.corridor.registered_servers)

    async def test_a_failed_restore_notifies_the_bot_owners(self) -> None:
        bot = FakeBot()
        cog = Telephonepole(bot=bot)
        await cog.cog_load()
        ctx = FakeContext()
        await cog.add.callback(cog, ctx, "freecad", "http://freecad-mcp:8765/mcp")
        bot.corridor = FakeCorridor(register_error="connection refused")

        await cog.cog_load()

        self.assertEqual(len(bot.owner_dms), 1)
        self.assertIn("freecad", bot.owner_dms[0])
        self.assertIn("connection refused", bot.owner_dms[0])


class TestCogLoadAutoLoadsCorridor(unittest.IsolatedAsyncioTestCase):
    """required_cogs in info.json only tells Downloader what to install --
    Red does not auto-load a dependency at runtime just because it's
    declared there. Regression test for: unload corridor, then load this
    cog -> it must pull corridor back in instead of failing to load."""

    async def test_cog_load_loads_corridor_when_not_already_loaded(self) -> None:
        bot = FakeBot(preloaded=False)
        cog = Telephonepole(bot=bot)
        self.assertIsNone(bot.get_cog("Corridor"))

        await cog.cog_load()

        self.assertEqual(bot._cog_mgr.find_cog_calls, ["corridor"])
        self.assertEqual(bot.load_extension_calls, ["corridor"])
        self.assertEqual(bot.loaded_packages, ["corridor"])
        self.assertIsNotNone(cog._corridor)

    async def test_package_setup_loads_corridor_before_adding_the_cog(self) -> None:
        bot = FakeBot(preloaded=False)

        await setup(bot)

        self.assertEqual(bot.load_extension_calls, ["corridor"])
        self.assertEqual(bot.loaded_packages, ["corridor"])
        self.assertEqual(len(bot.add_cog_calls), 1)
        self.assertIs(bot.add_cog_calls[0]._corridor, bot.corridor)

    async def test_missing_corridor_reports_a_user_facing_load_error(self) -> None:
        bot = FakeBot(preloaded=False, corridor_installable=False)

        with self.assertRaisesRegex(CogLoadError, "not installed"):
            await Telephonepole(bot=bot).cog_load()

        self.assertEqual(bot.load_extension_calls, [])


class TestDependentRegistration(unittest.IsolatedAsyncioTestCase):
    """Regression test for: unloading corridor left dependent cogs like this
    one running with a stale corridor reference instead of also being
    unloaded. cog_load/cog_unload must keep corridor's dependent registry in
    sync so corridor's own cog_unload can cascade correctly."""

    async def test_cog_load_registers_with_corridor(self) -> None:
        bot = FakeBot()
        cog = Telephonepole(bot=bot)

        await cog.cog_load()

        self.assertIn("telephonepole", bot.corridor.registered_dependents)

    async def test_cog_unload_unregisters_from_corridor(self) -> None:
        bot = FakeBot()
        cog = Telephonepole(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        self.assertNotIn("telephonepole", bot.corridor.registered_dependents)


class TestCogUnloadUnregistersAllServers(unittest.IsolatedAsyncioTestCase):
    async def test_cog_unload_unregisters_every_server_by_owner(self) -> None:
        bot = FakeBot()
        cog = Telephonepole(bot=bot)
        await cog.cog_load()
        ctx = FakeContext()
        await cog.add.callback(cog, ctx, "freecad", "http://freecad-mcp:8765/mcp")

        await cog.cog_unload()

        self.assertIn("Telephonepole", bot.corridor.unregistered_owners)
        self.assertEqual(bot.corridor.registered_servers, {})
