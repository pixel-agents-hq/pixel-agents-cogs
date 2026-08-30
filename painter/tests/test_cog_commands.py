"""The only tests that need the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_domain_models.py / test_tool_loop_service.py /
test_a2a_server.py / test_settings_repository.py).

Owner-gated commands are asserted by introspecting the `__is_owner__` tag
the shared redbot stub (`corridor/testing.py`) attaches -- Red's real check
machinery isn't exercised here, only which decorator each command carries.
A parallel copy of architect/tests/test_cog_commands.py's shape, minus
what only architect's own webview/WebSocket concerns need.
"""

from __future__ import annotations

import unittest

from redbot.core.errors import CogLoadError

from pixelagents.domain import GridPosition, GridRect

from .. import setup
from ..infrastructure.settings_repository import DEFAULT_SYSTEM_PROMPT
from ..painter import Painter
from .conftest import FakeArchitectCog, FakeBot, FakeContext


def _descriptions(bot: FakeBot) -> list[str | None]:
    assert bot.corridor is not None
    return [reply["description"] for reply in bot.corridor.replies]


class TestCommandsAreOwnerGated(unittest.TestCase):
    def setUp(self) -> None:
        self.cog = Painter(bot=FakeBot())

    def test_maxtoolcalls_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.maxtoolcalls.callback, "__is_owner__", False))

    def test_debug_logging_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.debug_logging.callback, "__is_owner__", False))

    def test_prompt_set_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.prompt_set.callback, "__is_owner__", False))

    def test_status_is_not_owner_gated(self) -> None:
        self.assertFalse(getattr(self.cog.status.callback, "__is_owner__", False))


class TestPainterCommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Painter(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def asyncTearDown(self) -> None:
        await self.cog.cog_unload()

    async def test_maxtoolcalls_updates(self) -> None:
        await self.cog.maxtoolcalls.callback(self.cog, self.ctx, 3)

        settings = await self.cog._repository.global_settings()
        self.assertEqual(settings.max_tool_calls, 3)
        self.assertIn("`3`", _descriptions(self.bot)[-1] or "")

    async def test_maxtoolcalls_rejects_non_positive_values(self) -> None:
        await self.cog.maxtoolcalls.callback(self.cog, self.ctx, 0)

        settings = await self.cog._repository.global_settings()
        self.assertEqual(settings.max_tool_calls, 5)
        self.assertIn("positive", _descriptions(self.bot)[-1] or "")

    async def test_debug_logging_enables_and_disables(self) -> None:
        await self.cog.debug_logging.callback(self.cog, self.ctx, True)
        settings = await self.cog._repository.global_settings()
        self.assertTrue(settings.debug_logging)

        await self.cog.debug_logging.callback(self.cog, self.ctx, False)
        settings = await self.cog._repository.global_settings()
        self.assertFalse(settings.debug_logging)

    async def test_prompt_set_and_show(self) -> None:
        await self.cog.prompt_set.callback(self.cog, self.ctx, text="Be terse.")

        await self.cog.prompt_show.callback(self.cog, self.ctx)

        self.assertEqual(_descriptions(self.bot)[-1], "Be terse.")

    async def test_prompt_reset(self) -> None:
        await self.cog.prompt_set.callback(self.cog, self.ctx, text="Be terse.")

        await self.cog.prompt_reset.callback(self.cog, self.ctx)

        settings = await self.cog._repository.global_settings()
        self.assertEqual(settings.system_prompt, DEFAULT_SYSTEM_PROMPT)

    async def test_status_shows_registered_with_corridor_after_cog_load(self) -> None:
        await self.cog.status.callback(self.cog, self.ctx)

        fields = self.bot.corridor.replies[-1]["fields"]
        registration_field = next(f for f in fields if f.name == "A2A Registration")
        self.assertIn("registered", registration_field.value)

    async def test_status_shows_layout_not_seeded_without_a_shared_store(self) -> None:
        await self.cog.status.callback(self.cog, self.ctx)

        fields = self.bot.corridor.replies[-1]["fields"]
        layout_field = next(f for f in fields if f.name == "Office Layout")
        self.assertEqual(layout_field.value, "⚠️ not seeded yet")

    async def test_status_shows_layout_available_once_shared_store_is_seeded(self) -> None:
        await self.cog._office_layout_settings.set_layout({"tiles": [1]})

        await self.cog.status.callback(self.cog, self.ctx)

        fields = self.bot.corridor.replies[-1]["fields"]
        layout_field = next(f for f in fields if f.name == "Office Layout")
        self.assertIn("available", layout_field.value)


class TestCogLoadSurvivesARegistrationFailure(unittest.IsolatedAsyncioTestCase):
    """A broken/raising corridor.register_agent call (a stale reference, a
    corridor-side bug, ...) must never take down painter's own cog_load
    or leave it unusable."""

    async def test_cog_load_does_not_raise_when_registration_fails(self) -> None:
        bot = FakeBot()
        assert bot.corridor is not None

        async def _broken_register_agent(agent: object, *, owner: str) -> None:
            raise RuntimeError("simulated corridor failure")

        bot.corridor.register_agent = _broken_register_agent  # type: ignore[method-assign]
        cog = Painter(bot=bot)

        await cog.cog_load()  # must not raise
        self.addAsyncCleanup(cog.cog_unload)

    async def test_the_cog_stays_usable_via_discord_commands(self) -> None:
        bot = FakeBot()
        assert bot.corridor is not None

        async def _broken_register_agent(agent: object, *, owner: str) -> None:
            raise RuntimeError("simulated corridor failure")

        bot.corridor.register_agent = _broken_register_agent  # type: ignore[method-assign]
        cog = Painter(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)
        ctx = FakeContext()

        await cog.status.callback(cog, ctx)  # must not raise

        assert bot.corridor is not None
        fields = bot.corridor.replies[-1]["fields"]
        registration_field = next(f for f in fields if f.name == "A2A Registration")
        self.assertIn("not registered", registration_field.value)


class TestCogLoadAutoLoadsCorridor(unittest.IsolatedAsyncioTestCase):
    """required_cogs in info.json only tells Downloader what to install --
    Red does not auto-load a dependency at runtime just because it's
    declared there. Regression test for: unload corridor, then load this
    cog -> it must pull corridor back in instead of failing to load."""

    async def test_cog_load_loads_corridor_when_not_already_loaded(self) -> None:
        bot = FakeBot(preloaded=False)
        cog = Painter(bot=bot)
        self.assertIsNone(bot.get_cog("Corridor"))

        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        self.assertEqual(bot._cog_mgr.find_cog_calls, ["corridor", "pixelagents"])
        self.assertEqual(bot.load_extension_calls, ["corridor", "pixelagents"])
        self.assertEqual(bot.loaded_packages, ["corridor", "pixelagents"])
        self.assertIsNotNone(cog._corridor)

    async def test_package_setup_loads_corridor_before_adding_the_cog(self) -> None:
        bot = FakeBot(preloaded=False)

        await setup(bot)

        async def _cleanup() -> None:
            await bot.add_cog_calls[0].cog_unload()

        self.addAsyncCleanup(_cleanup)

        self.assertEqual(bot.load_extension_calls, ["corridor", "pixelagents"])
        self.assertEqual(bot.loaded_packages, ["corridor", "pixelagents"])
        self.assertEqual(len(bot.add_cog_calls), 1)
        self.assertIs(bot.add_cog_calls[0]._corridor, bot.corridor)

    async def test_missing_corridor_reports_a_user_facing_load_error(self) -> None:
        bot = FakeBot(preloaded=False, corridor_installable=False)

        with self.assertRaisesRegex(CogLoadError, "not installed"):
            await Painter(bot=bot).cog_load()

        self.assertEqual(bot.load_extension_calls, [])


class TestDependentRegistration(unittest.IsolatedAsyncioTestCase):
    """Regression test for: unloading corridor left dependent cogs like this
    one running with a stale corridor reference instead of also being
    unloaded. cog_load/cog_unload must keep corridor's dependent registry in
    sync so corridor's own cog_unload can cascade correctly."""

    async def test_cog_load_registers_with_corridor(self) -> None:
        bot = FakeBot()
        cog = Painter(bot=bot)

        await cog.cog_load()

        assert bot.corridor is not None
        self.assertIn("painter", bot.corridor.registered_dependents)

        await cog.cog_unload()

    async def test_cog_unload_unregisters_from_corridor(self) -> None:
        bot = FakeBot()
        cog = Painter(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        assert bot.corridor is not None
        self.assertNotIn("painter", bot.corridor.registered_dependents)


class TestAgentRegistration(unittest.IsolatedAsyncioTestCase):
    """painter registers its AgentCard/AgentExecutor with corridor rather
    than binding an A2A listener of its own, same as architect."""

    async def test_cog_load_registers_the_agent_with_corridor(self) -> None:
        bot = FakeBot()
        cog = Painter(bot=bot)

        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        assert bot.corridor is not None
        self.assertEqual([agent.agent_key for agent in bot.corridor.list_agents()], ["painter"])

    async def test_cog_unload_unregisters_the_agent_from_corridor(self) -> None:
        bot = FakeBot()
        cog = Painter(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        assert bot.corridor is not None
        self.assertEqual(bot.corridor.list_agents(), ())


class TestPresencePublishing(unittest.IsolatedAsyncioTestCase):
    """corridor's own `register_agent`/`unregister_agent_owner` publish
    AgentPresenceChanged as a side effect of painter registering/
    unregistering its A2A agent, same as architect."""

    async def test_cog_load_publishes_online_presence(self) -> None:
        bot = FakeBot()
        cog = Painter(bot=bot)

        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        assert bot.corridor is not None
        presence_events = [
            event
            for event in bot.corridor.published
            if type(event).__name__ == "AgentPresenceChanged"
        ]
        self.assertEqual(len(presence_events), 1)
        event = presence_events[0]
        self.assertEqual(event.status, "online")
        self.assertEqual(event.display_name, "painter")
        self.assertIsNone(event.agent.discord_user_id)
        self.assertIsNone(event.agent.guild_id)
        self.assertTrue(event.agent.is_bot)
        self.assertEqual(event.agent.agent_key, "painter")

    async def test_cog_unload_publishes_offline_presence(self) -> None:
        bot = FakeBot()
        cog = Painter(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        assert bot.corridor is not None
        statuses = [
            event.status
            for event in bot.corridor.published
            if type(event).__name__ == "AgentPresenceChanged"
        ]
        self.assertEqual(statuses, ["online", "offline"])

    async def test_tool_activity_publishes_agent_replied(self) -> None:
        bot = FakeBot()
        cog = Painter(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        await cog._publish_activity("using tool recolor_tiles")

        assert bot.corridor is not None
        replied_events = [
            event for event in bot.corridor.published if type(event).__name__ == "AgentReplied"
        ]
        self.assertEqual(len(replied_events), 1)
        event = replied_events[0]
        self.assertEqual(event.summary, "using tool recolor_tiles")
        self.assertIsNone(event.agent.discord_user_id)
        self.assertIsNone(event.agent.guild_id)
        self.assertTrue(event.agent.is_bot)


class TestNotifyArchitectLayoutChanged(unittest.IsolatedAsyncioTestCase):
    """Painter has no WebSocket clients of its own to push a live update
    through -- `_notify_architect_layout_changed` is the best-effort
    `bot.get_cog("Architect")` hook that asks architect to re-broadcast
    the shared layout to its own connected clients instead, so a painter
    recolor shows up live rather than only on a browser's next reload."""

    async def test_calls_architects_notify_hook_when_loaded(self) -> None:
        fake_architect = FakeArchitectCog()
        bot = FakeBot(architect=fake_architect)
        cog = Painter(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        await cog._notify_architect_layout_changed()

        self.assertEqual(fake_architect.notify_calls, 1)

    async def test_does_nothing_when_architect_is_not_loaded(self) -> None:
        bot = FakeBot(architect=None)
        cog = Painter(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        await cog._notify_architect_layout_changed()  # must not raise

    async def test_swallows_a_failure_from_architects_notify_hook(self) -> None:
        fake_architect = FakeArchitectCog(fail_with=RuntimeError("boom"))
        bot = FakeBot(architect=fake_architect)
        cog = Painter(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        await cog._notify_architect_layout_changed()  # must not raise

        self.assertEqual(fake_architect.notify_calls, 1)

    async def test_a_successful_recolor_notifies_architect(self) -> None:
        """End-to-end through the real wiring: PainterLayoutService's
        on_layout_changed callback, as constructed in CogBase.__init__,
        actually reaches bot.get_cog("Architect")."""

        fake_architect = FakeArchitectCog()
        bot = FakeBot(architect=fake_architect)
        cog = Painter(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        await cog._office_layout_settings.set_layout(
            {"version": 1, "cols": 1, "rows": 1, "tiles": [1], "furniture": []}
        )

        await cog._painter_layout_service.recolor_tiles(
            area=GridRect(GridPosition(0, 0), 1, 1), color={"h": 200, "s": 50, "b": 0, "c": 0}
        )

        self.assertEqual(fake_architect.notify_calls, 1)
