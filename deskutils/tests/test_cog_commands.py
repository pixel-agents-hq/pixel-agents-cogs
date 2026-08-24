"""The only tests that need the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_domain_models.py / test_application_service.py).

Commands are exercised against a fake Clock (the same FakeClock
application-layer tests use), never the real system clock -- a fixed
instant is what makes the expected `<t:...>` markup and formatted strings
assertable at all."""

from __future__ import annotations

import unittest

from redbot.core.errors import CogLoadError

from corridor.domain import EMPLOYEE_KEY, llm_tool_spec

from .. import setup
from ..application import TimeService
from ..deskutils import Deskutils
from .conftest import FakeBot, FakeContext, FakeCorridor
from .test_application_service import FIXED_INSTANT, FakeClock

EPOCH = int(FIXED_INSTANT.timestamp())


class TestTimeCommand(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Deskutils(bot=self.bot)
        self.cog._service = TimeService(FakeClock())
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_time_shows_discord_markup_and_utc(self) -> None:
        await self.cog.time_command.callback(self.cog, self.ctx, None)

        self.assertEqual(len(self.bot.corridor.replies), 1)
        reply = self.bot.corridor.replies[0]
        self.assertEqual(reply["title"], "Current time")
        self.assertEqual(
            reply["fields"][0].value,
            f"<t:{EPOCH}:F> (<t:{EPOCH}:R>)",
        )
        self.assertEqual(reply["fields"][1].value, "2026-08-23 12:30:00 UTC")

    async def test_time_checks_employee_permission(self) -> None:
        await self.cog.time_command.callback(self.cog, self.ctx, None)

        self.assertEqual(self.bot.corridor.permission_checks, ["employee"])

    async def test_time_is_blocked_when_corridor_denies_permission(self) -> None:
        self.bot.corridor = FakeCorridor(allow_permission=False)
        await self.cog.cog_load()

        await self.cog.time_command.callback(self.cog, self.ctx, None)

        self.assertEqual(self.bot.corridor.replies, [])

    async def test_time_with_a_valid_zone_adds_a_localized_field(self) -> None:
        await self.cog.time_command.callback(self.cog, self.ctx, "America/New_York")

        reply = self.bot.corridor.replies[0]
        self.assertEqual(len(reply["fields"]), 3)
        zone_field = reply["fields"][2]
        self.assertEqual(zone_field.name, "America/New_York")
        self.assertEqual(zone_field.value, "2026-08-23 08:30:00 EDT")

    async def test_time_with_an_unknown_zone_replies_with_a_warning_instead(self) -> None:
        await self.cog.time_command.callback(self.cog, self.ctx, "Not/A_Real_Zone")

        self.assertEqual(len(self.bot.corridor.replies), 1)
        reply = self.bot.corridor.replies[0]
        self.assertEqual(reply["title"], "deskutils")
        self.assertIn("Unknown time zone", reply["description"])
        self.assertIn("Not/A_Real_Zone", reply["description"])


class TestCogLoadAutoLoadsCorridor(unittest.IsolatedAsyncioTestCase):
    """required_cogs in info.json only tells Downloader what to install --
    Red does not auto-load a dependency at runtime just because it's
    declared there. Regression test for: unload corridor, then load this
    cog -> it must pull corridor back in instead of failing to load."""

    async def test_cog_load_loads_corridor_when_not_already_loaded(self) -> None:
        bot = FakeBot(preloaded=False)
        cog = Deskutils(bot=bot)
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
            await Deskutils(bot=bot).cog_load()

        self.assertEqual(bot.load_extension_calls, [])


class TestDependentRegistration(unittest.IsolatedAsyncioTestCase):
    """Regression test for: unloading corridor left dependent cogs like this
    one running with a stale corridor reference instead of also being
    unloaded. cog_load/cog_unload must keep corridor's dependent registry in
    sync so corridor's own cog_unload can cascade correctly."""

    async def test_cog_load_registers_with_corridor(self) -> None:
        bot = FakeBot()
        cog = Deskutils(bot=bot)

        await cog.cog_load()

        self.assertIn("deskutils", bot.corridor.registered_dependents)

    async def test_cog_unload_unregisters_from_corridor(self) -> None:
        bot = FakeBot()
        cog = Deskutils(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        self.assertNotIn("deskutils", bot.corridor.registered_dependents)


class TestTimeCommandIsAnLLMTool(unittest.TestCase):
    """`time_command` carries `@llm_tool(...)` directly -- this is
    deskutils' own regression guard that the decoration is correct,
    without needing corridor's adapter-layer scanner (that's covered by
    corridor's own test suite)."""

    def test_decoration_matches_the_commands_own_permission_tier(self) -> None:
        spec = llm_tool_spec(Deskutils.time_command.callback)

        assert spec is not None
        self.assertEqual(spec.name, "deskutils_time")
        self.assertEqual(spec.required_group, EMPLOYEE_KEY)
        self.assertEqual(
            spec.parameters,
            {"type": "object", "properties": {"timezone": {"type": "string"}}, "required": []},
        )


class TestToolRegistration(unittest.IsolatedAsyncioTestCase):
    """`time_command`'s `@llm_tool` decoration is scanned and registered
    into corridor's cross-cog tool registry at cog_load -- inert unless
    something (pico) reads corridor's registry, but always
    registered/unregistered in step with the cog's own lifecycle
    regardless of whether anything ever does. What `register_llm_tools`
    actually does with a decorated command is corridor's own concern
    (corridor/tests/test_llm_tool_registration.py), not duplicated here --
    this only verifies deskutils asks for it correctly."""

    async def test_cog_load_registers_the_time_tool(self) -> None:
        bot = FakeBot()
        cog = Deskutils(bot=bot)

        await cog.cog_load()

        self.assertEqual(bot.corridor.registered_llm_tools_calls, [(cog, "Deskutils")])

    async def test_cog_unload_unregisters_the_time_tool(self) -> None:
        bot = FakeBot()
        cog = Deskutils(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        self.assertIn("Deskutils", bot.corridor.unregistered_tool_owners)
