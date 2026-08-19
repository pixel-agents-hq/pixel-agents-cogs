"""The only tests that need the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_domain_models.py / test_application_service.py)."""

from __future__ import annotations

import unittest

from redbot.core.errors import CogLoadError

from .. import setup
from ..{{cookiecutter.cog_name}} import {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}
from .conftest import FakeBot, FakeContext, FakeCorridor


class TestCommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_count_reports_zero_initially(self) -> None:
        await self.cog.count.callback(self.cog, self.ctx)

        self.assertEqual(
            self.bot.corridor.replies, [{"title": "Count", "description": "0", "content": None}]
        )

    async def test_bump_increments_and_replies_through_corridor(self) -> None:
        await self.cog.bump.callback(self.cog, self.ctx)
        await self.cog.bump.callback(self.cog, self.ctx)

        descriptions = [reply["description"] for reply in self.bot.corridor.replies]
        self.assertEqual(descriptions, ["Now: 1", "Now: 2"])

    async def test_bump_checks_keyholder_permission(self) -> None:
        await self.cog.bump.callback(self.cog, self.ctx)

        self.assertEqual(self.bot.corridor.permission_checks, ["keyholder"])

    async def test_bump_is_blocked_when_corridor_denies_permission(self) -> None:
        self.bot.corridor = FakeCorridor(allow_permission=False)
        await self.cog.cog_load()

        await self.cog.bump.callback(self.cog, self.ctx)

        self.assertEqual(self.bot.corridor.replies, [])


class TestCogLoadAutoLoadsCorridor(unittest.IsolatedAsyncioTestCase):
    """required_cogs in info.json only tells Downloader what to install --
    Red does not auto-load a dependency at runtime just because it's
    declared there. Regression test for: unload corridor, then load this
    cog -> it must pull corridor back in instead of failing to load."""

    async def test_cog_load_loads_corridor_when_not_already_loaded(self) -> None:
        bot = FakeBot(preloaded=False)
        cog = {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}(bot=bot)
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
            await {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}(bot=bot).cog_load()

        self.assertEqual(bot.load_extension_calls, [])


class TestDependentRegistration(unittest.IsolatedAsyncioTestCase):
    """Regression test for: unloading corridor left dependent cogs like this
    one running with a stale corridor reference instead of also being
    unloaded. cog_load/cog_unload must keep corridor's dependent registry in
    sync so corridor's own cog_unload can cascade correctly."""

    async def test_cog_load_registers_with_corridor(self) -> None:
        bot = FakeBot()
        cog = {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}(bot=bot)

        await cog.cog_load()

        self.assertIn("{{cookiecutter.cog_name}}", bot.corridor.registered_dependents)

    async def test_cog_unload_unregisters_from_corridor(self) -> None:
        bot = FakeBot()
        cog = {{ cookiecutter.cog_name.replace('-', '_').split('_') | map('capitalize') | join }}(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        self.assertNotIn("{{cookiecutter.cog_name}}", bot.corridor.registered_dependents)
