"""The only tests that need the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_domain_models.py / test_application_service.py)."""

from __future__ import annotations

import unittest

from redbot.core.errors import CogLoadError

from .. import setup
from ..adapters.views import EventPickerView
from ..application import list_publishable_events
from ..testbench import Testbench
from .conftest import FakeBot, FakeContext


class TestCommandGates(unittest.TestCase):
    """testbench is owner-only: letting anyone but the bot owner publish
    arbitrary corridor bus events would be a real footgun. Matches the
    getattr(cmd.callback, "__is_owner__", False) assertion style used by
    pico/toolbox's own owner-gated commands -- see corridor/testing.py's
    is_owner() stub for what tags this attribute."""

    def test_group_requires_bot_owner(self) -> None:
        self.assertTrue(getattr(Testbench.testbench_group.callback, "__is_owner__", False))


class TestPublishCommand(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Testbench(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_sends_the_event_picker_view(self) -> None:
        await self.cog.publish.callback(self.cog, self.ctx)

        self.assertEqual(len(self.ctx.sent_views), 1)
        self.assertIsInstance(self.ctx.sent_views[0], EventPickerView)


class TestListCommand(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Testbench(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_lists_every_publishable_event_through_corridor(self) -> None:
        await self.cog.list_events.callback(self.cog, self.ctx)

        self.assertEqual(len(self.bot.corridor.replies), 1)
        description = self.bot.corridor.replies[0]["description"]
        for spec in list_publishable_events():
            self.assertIn(spec.name, description)


class TestCogLoadAutoLoadsCorridor(unittest.IsolatedAsyncioTestCase):
    """required_cogs in info.json only tells Downloader what to install --
    Red does not auto-load a dependency at runtime just because it's
    declared there. Regression test for: unload corridor, then load this
    cog -> it must pull corridor back in instead of failing to load."""

    async def test_cog_load_loads_corridor_when_not_already_loaded(self) -> None:
        bot = FakeBot(preloaded=False)
        cog = Testbench(bot=bot)
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
            await Testbench(bot=bot).cog_load()

        self.assertEqual(bot.load_extension_calls, [])


class TestDependentRegistration(unittest.IsolatedAsyncioTestCase):
    """Regression test for: unloading corridor left dependent cogs like this
    one running with a stale corridor reference instead of also being
    unloaded. cog_load/cog_unload must keep corridor's dependent registry in
    sync so corridor's own cog_unload can cascade correctly."""

    async def test_cog_load_registers_with_corridor(self) -> None:
        bot = FakeBot()
        cog = Testbench(bot=bot)

        await cog.cog_load()

        self.assertIn("testbench", bot.corridor.registered_dependents)

    async def test_cog_unload_unregisters_from_corridor(self) -> None:
        bot = FakeBot()
        cog = Testbench(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        self.assertNotIn("testbench", bot.corridor.registered_dependents)
