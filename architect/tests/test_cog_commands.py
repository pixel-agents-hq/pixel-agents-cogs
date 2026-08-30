"""The only tests that need the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_domain_models.py / test_tool_loop_service.py /
test_a2a_server.py / test_settings_repository.py).

Owner-gated commands are asserted by introspecting the `__is_owner__` tag
the shared redbot stub (`corridor/testing.py`) attaches -- Red's real check
machinery isn't exercised here, only which decorator each command carries.
"""

from __future__ import annotations

import unittest

from redbot.core.errors import CogLoadError

from .. import setup
from ..architect import Architect
from ..infrastructure.settings_repository import DEFAULT_SYSTEM_PROMPT
from .conftest import FakeBot, FakeContext, FakeLLMSettings


def _descriptions(bot: FakeBot) -> list[str | None]:
    assert bot.corridor is not None
    return [reply["description"] for reply in bot.corridor.replies]


class TestCommandsAreOwnerGated(unittest.TestCase):
    def setUp(self) -> None:
        self.cog = Architect(bot=FakeBot())

    def test_ws_group_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.ws_group.callback, "__is_owner__", False))

    def test_ws_host_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.ws_host.callback, "__is_owner__", False))

    def test_ws_port_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.ws_port.callback, "__is_owner__", False))

    def test_maxtoolcalls_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.maxtoolcalls.callback, "__is_owner__", False))

    def test_debug_logging_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.debug_logging.callback, "__is_owner__", False))

    def test_prompt_set_is_owner_gated(self) -> None:
        self.assertTrue(getattr(self.cog.prompt_set.callback, "__is_owner__", False))

    def test_status_is_not_owner_gated(self) -> None:
        self.assertFalse(getattr(self.cog.status.callback, "__is_owner__", False))


class TestArchitectCommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Architect(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def asyncTearDown(self) -> None:
        await self.cog.cog_unload()

    async def test_ws_host_persists_and_tells_the_owner_to_reload(self) -> None:
        await self.cog.ws_host.callback(self.cog, self.ctx, "0.0.0.0")

        settings = await self.cog._repository.global_settings()
        self.assertEqual(settings.ws_host, "0.0.0.0")
        self.assertIn("Reload the cog", _descriptions(self.bot)[-1] or "")

    async def test_ws_port_persists_and_tells_the_owner_to_reload(self) -> None:
        await self.cog.ws_port.callback(self.cog, self.ctx, 9001)

        settings = await self.cog._repository.global_settings()
        self.assertEqual(settings.ws_port, 9001)
        self.assertIn("Reload the cog", _descriptions(self.bot)[-1] or "")

    async def test_ws_port_rejects_out_of_range_values(self) -> None:
        await self.cog.ws_port.callback(self.cog, self.ctx, 0)

        self.assertIn("Port must be", _descriptions(self.bot)[-1] or "")

    async def test_maxtoolcalls_updates(self) -> None:
        await self.cog.maxtoolcalls.callback(self.cog, self.ctx, 3)

        settings = await self.cog._repository.global_settings()
        self.assertEqual(settings.max_tool_calls, 3)

    async def test_maxtoolcalls_rejects_non_positive_values(self) -> None:
        await self.cog.maxtoolcalls.callback(self.cog, self.ctx, 0)

        self.assertIn("positive", _descriptions(self.bot)[-1] or "")

    async def test_debug_logging_enables_and_disables(self) -> None:
        await self.cog.debug_logging.callback(self.cog, self.ctx, True)

        settings = await self.cog._repository.global_settings()
        self.assertTrue(settings.debug_logging)
        self.assertEqual(_descriptions(self.bot)[-1], "Debug logging enabled.")

        await self.cog.debug_logging.callback(self.cog, self.ctx, False)

        settings = await self.cog._repository.global_settings()
        self.assertFalse(settings.debug_logging)
        self.assertEqual(_descriptions(self.bot)[-1], "Debug logging disabled.")

    async def test_status_shows_debug_logging_state(self) -> None:
        await self.cog.status.callback(self.cog, self.ctx)

        assert self.bot.corridor is not None
        fields = self.bot.corridor.replies[-1]["fields"]
        debug_field = next(f for f in fields if f.name == "Debug Logging")
        self.assertEqual(debug_field.value, "off")

    async def test_status_shows_registered_with_corridor_after_cog_load(self) -> None:
        # cog_load() (asyncSetUp) already registered architect with the
        # FakeCorridor -- see docs/agent-directory-design.md.
        await self.cog.status.callback(self.cog, self.ctx)

        assert self.bot.corridor is not None
        fields = self.bot.corridor.replies[-1]["fields"]
        registration_field = next(f for f in fields if f.name == "A2A Registration")
        self.assertIn("registered", registration_field.value)

    async def test_prompt_set_and_show(self) -> None:
        await self.cog.prompt_set.callback(self.cog, self.ctx, text="Be terse.")

        await self.cog.prompt_show.callback(self.cog, self.ctx)

        self.assertEqual(_descriptions(self.bot)[-1], "Be terse.")

    async def test_prompt_reset(self) -> None:
        await self.cog.prompt_set.callback(self.cog, self.ctx, text="Be terse.")

        await self.cog.prompt_reset.callback(self.cog, self.ctx)

        settings = await self.cog._repository.global_settings()
        self.assertEqual(settings.system_prompt, DEFAULT_SYSTEM_PROMPT)

    async def test_status_masks_the_key_when_set(self) -> None:
        assert self.bot.corridor is not None
        self.bot.corridor._llm_settings = FakeLLMSettings(llm_api_key="sk-super-secret")

        await self.cog.status.callback(self.cog, self.ctx)

        fields = self.bot.corridor.replies[-1]["fields"]
        key_field = next(f for f in fields if f.name == "LLM Key")
        self.assertNotIn("sk-super-secret", key_field.value)

    async def test_status_shows_unset_placeholder_when_no_key(self) -> None:
        assert self.bot.corridor is not None
        self.bot.corridor._llm_settings = FakeLLMSettings(llm_api_key=None)

        await self.cog.status.callback(self.cog, self.ctx)

        fields = self.bot.corridor.replies[-1]["fields"]
        key_field = next(f for f in fields if f.name == "LLM Key")
        self.assertEqual(key_field.value, "*(not set)*")

    async def test_status_shows_webview_health(self) -> None:
        await self.cog.status.callback(self.cog, self.ctx)

        assert self.bot.corridor is not None
        fields = self.bot.corridor.replies[-1]["fields"]
        webview_field = next(f for f in fields if f.name == "Webview")
        # The default FakePixelAgents dist_path is an empty temp dir --
        # ready per its status, but with no real index.html/sprites to load.
        self.assertEqual(webview_field.value, "⚠️ missing")

    async def test_status_shows_layout_not_seeded_without_a_bundled_default(self) -> None:
        await self.cog.status.callback(self.cog, self.ctx)

        assert self.bot.corridor is not None
        fields = self.bot.corridor.replies[-1]["fields"]
        layout_field = next(f for f in fields if f.name == "Layout")
        self.assertEqual(layout_field.value, "⚠️ not seeded yet")

    async def test_status_shows_layout_seeded_once_set(self) -> None:
        await self.cog._office_layout_settings.set_layout({"tiles": [1]})

        await self.cog.status.callback(self.cog, self.ctx)

        assert self.bot.corridor is not None
        fields = self.bot.corridor.replies[-1]["fields"]
        layout_field = next(f for f in fields if f.name == "Layout")
        self.assertIn("seeded", layout_field.value)


class TestCogLoadSurvivesARegistrationFailure(unittest.IsolatedAsyncioTestCase):
    """architect no longer binds its own A2A listener (see
    docs/agent-directory-design.md) -- that bind-failure risk now lives
    entirely in corridor's own test suite
    (corridor/tests/test_a2a_server.py). What remains architect's own
    concern is that a broken/raising corridor.register_agent call (a
    stale reference, a corridor-side bug, ...) must never take down
    architect's own cog_load or leave it unusable."""

    async def test_cog_load_does_not_raise_when_registration_fails(self) -> None:
        bot = FakeBot()
        assert bot.corridor is not None

        async def _broken_register_agent(agent: object, *, owner: str) -> None:
            raise RuntimeError("simulated corridor failure")

        bot.corridor.register_agent = _broken_register_agent  # type: ignore[method-assign]
        cog = Architect(bot=bot)

        await cog.cog_load()  # must not raise
        self.addAsyncCleanup(cog.cog_unload)

    async def test_no_presence_published_when_registration_fails(self) -> None:
        """Presence is now only a side effect of a *successful*
        registration -- an agent that failed to register isn't reachable,
        so no online AgentPresenceChanged should be published for it."""

        bot = FakeBot()
        assert bot.corridor is not None

        async def _broken_register_agent(agent: object, *, owner: str) -> None:
            raise RuntimeError("simulated corridor failure")

        bot.corridor.register_agent = _broken_register_agent  # type: ignore[method-assign]
        cog = Architect(bot=bot)

        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        presence_events = [
            event
            for event in bot.corridor.published
            if type(event).__name__ == "AgentPresenceChanged"
        ]
        self.assertEqual(presence_events, [])

    async def test_the_cog_stays_usable_via_discord_commands(self) -> None:
        bot = FakeBot()
        assert bot.corridor is not None

        async def _broken_register_agent(agent: object, *, owner: str) -> None:
            raise RuntimeError("simulated corridor failure")

        bot.corridor.register_agent = _broken_register_agent  # type: ignore[method-assign]
        cog = Architect(bot=bot)
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
        cog = Architect(bot=bot)
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
            await Architect(bot=bot).cog_load()

        self.assertEqual(bot.load_extension_calls, [])


class TestDependentRegistration(unittest.IsolatedAsyncioTestCase):
    """Regression test for: unloading corridor left dependent cogs like this
    one running with a stale corridor reference instead of also being
    unloaded. cog_load/cog_unload must keep corridor's dependent registry in
    sync so corridor's own cog_unload can cascade correctly."""

    async def test_cog_load_registers_with_corridor(self) -> None:
        bot = FakeBot()
        cog = Architect(bot=bot)

        await cog.cog_load()

        assert bot.corridor is not None
        self.assertIn("architect", bot.corridor.registered_dependents)

        await cog.cog_unload()

    async def test_cog_unload_unregisters_from_corridor(self) -> None:
        bot = FakeBot()
        cog = Architect(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        assert bot.corridor is not None
        self.assertNotIn("architect", bot.corridor.registered_dependents)


class TestAgentRegistration(unittest.IsolatedAsyncioTestCase):
    """architect no longer binds its own A2A listener -- it registers its
    AgentCard/AgentExecutor with corridor instead, see
    docs/agent-directory-design.md."""

    async def test_cog_load_registers_the_agent_with_corridor(self) -> None:
        bot = FakeBot()
        cog = Architect(bot=bot)

        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        assert bot.corridor is not None
        self.assertEqual([agent.agent_key for agent in bot.corridor.list_agents()], ["architect"])

    async def test_cog_unload_unregisters_the_agent_from_corridor(self) -> None:
        bot = FakeBot()
        cog = Architect(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        assert bot.corridor is not None
        self.assertEqual(bot.corridor.list_agents(), ())


class TestPresencePublishing(unittest.IsolatedAsyncioTestCase):
    """architect no longer hand-rolls its own presence publish -- corridor's
    own `register_agent`/`unregister_agent_owner` now publish
    AgentPresenceChanged as a side effect of architect registering/
    unregistering its A2A agent (see docs/agent-directory-design.md), the
    same event shape floorplan's/architect's own subscribers already
    consume for a genuine agent."""

    async def test_cog_load_publishes_online_presence(self) -> None:
        bot = FakeBot()
        cog = Architect(bot=bot)

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
        self.assertEqual(event.display_name, "architect")
        self.assertIsNone(event.agent.discord_user_id)
        self.assertIsNone(event.agent.guild_id)
        self.assertTrue(event.agent.is_bot)
        self.assertEqual(event.agent.agent_key, "architect")

    async def test_cog_unload_publishes_offline_presence(self) -> None:
        bot = FakeBot()
        cog = Architect(bot=bot)
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
        cog = Architect(bot=bot)
        await cog.cog_load()
        self.addAsyncCleanup(cog.cog_unload)

        await cog._publish_activity("using tool describe_office")

        assert bot.corridor is not None
        replied_events = [
            event for event in bot.corridor.published if type(event).__name__ == "AgentReplied"
        ]
        self.assertEqual(len(replied_events), 1)
        event = replied_events[0]
        self.assertEqual(event.summary, "using tool describe_office")
        self.assertIsNone(event.agent.discord_user_id)
        self.assertIsNone(event.agent.guild_id)
        self.assertTrue(event.agent.is_bot)
        self.assertEqual(event.agent.agent_key, "architect")
