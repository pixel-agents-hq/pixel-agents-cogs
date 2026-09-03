"""The only tests that need the discord/redbot stubs installed by the
package-root conftest.py -- everything below the adapter layer is testable
without them (see test_domain_models.py / test_application_service.py)."""

from __future__ import annotations

import unittest

from redbot.core.errors import CogLoadError

from .. import setup
from ..application.tool_loop_service import ToolLoopResult
from ..bootcamp import Bootcamp
from .conftest import FakeBot, FakeContext, FakeCorridor


class FakeToolLoop:
    """Stands in for `ToolLoopService` on `run_agent`'s own tests -- the
    loop's real tool-calling behavior is covered by
    `test_tool_loop_service.py`; these tests only verify `run_agent` wires
    its inputs/output correctly."""

    def __init__(self, result: ToolLoopResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def run(self, **kwargs: object) -> ToolLoopResult:
        self.calls.append(kwargs)
        return self.result


class TestCreateRemoveListCommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Bootcamp(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()

    async def test_create_registers_and_replies_with_the_new_agent(self) -> None:
        await self.cog.create.callback(
            self.cog, self.ctx, "recruiter", system_prompt="Screen applicants."
        )

        self.assertIn("recruiter", self.bot.corridor.registered_agents)
        self.assertEqual(self.bot.corridor.replies[-1]["title"], "Agent created")
        self.assertIn("recruiter", self.bot.corridor.replies[-1]["description"])

    async def test_create_reports_a_validation_error_without_registering(self) -> None:
        await self.cog.create.callback(self.cog, self.ctx, "Not Valid", system_prompt="p")

        self.assertEqual(self.bot.corridor.registered_agents, {})
        self.assertEqual(self.bot.corridor.replies[-1]["title"], "Could not create agent")

    async def test_remove_unregisters_and_replies(self) -> None:
        await self.cog.create.callback(self.cog, self.ctx, "recruiter", system_prompt="p")

        await self.cog.remove.callback(self.cog, self.ctx, "recruiter")

        self.assertNotIn("recruiter", self.bot.corridor.registered_agents)
        self.assertEqual(self.bot.corridor.replies[-1]["title"], "Agent removed")

    async def test_remove_unknown_agent_reports_an_error(self) -> None:
        await self.cog.remove.callback(self.cog, self.ctx, "ghost")

        self.assertEqual(self.bot.corridor.replies[-1]["title"], "Could not remove agent")

    async def test_list_opens_a_panel_with_no_agents_initially(self) -> None:
        await self.cog.list_agents.callback(self.cog, self.ctx)

        self.assertEqual(len(self.ctx.sent), 1)
        view = self.ctx.sent[0]["view"]
        self.assertIsNotNone(view)
        self.assertEqual(view.agents, [])

    async def test_list_opens_a_panel_listing_every_created_agent(self) -> None:
        await self.cog.create.callback(self.cog, self.ctx, "recruiter", system_prompt="p")
        await self.cog.create.callback(self.cog, self.ctx, "onboarder", system_prompt="p")

        await self.cog.list_agents.callback(self.cog, self.ctx)

        view = self.ctx.sent[-1]["view"]
        self.assertEqual(
            sorted(agent.agent_key for agent in view.agents), ["onboarder", "recruiter"]
        )


class TestEditCommands(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Bootcamp(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()
        await self.cog.create.callback(self.cog, self.ctx, "recruiter", system_prompt="p")

    async def test_permission_updates_the_required_group(self) -> None:
        await self.cog.permission.callback(self.cog, self.ctx, "recruiter", "keyholder")

        agent = await self.cog._service.get_agent("recruiter")  # type: ignore[union-attr]
        assert agent is not None
        self.assertEqual(agent.permission_group, "keyholder")
        self.assertEqual(self.bot.corridor.replies[-1]["title"], "Agent updated")

    async def test_maxtoolcalls_updates_the_budget(self) -> None:
        await self.cog.maxtoolcalls.callback(self.cog, self.ctx, "recruiter", 3)

        agent = await self.cog._service.get_agent("recruiter")  # type: ignore[union-attr]
        assert agent is not None
        self.assertEqual(agent.max_tool_calls, 3)

    async def test_debuglogging_toggles_the_flag(self) -> None:
        await self.cog.debuglogging.callback(self.cog, self.ctx, "recruiter", True)

        agent = await self.cog._service.get_agent("recruiter")  # type: ignore[union-attr]
        assert agent is not None
        self.assertTrue(agent.debug_logging)

    async def test_edit_commands_report_an_error_for_an_unknown_agent(self) -> None:
        await self.cog.permission.callback(self.cog, self.ctx, "ghost", "keyholder")

        self.assertEqual(self.bot.corridor.replies[-1]["title"], "Could not update agent")


class TestAskCommand(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = FakeBot()
        self.cog = Bootcamp(bot=self.bot)
        await self.cog.cog_load()
        self.ctx = FakeContext()
        await self.cog.create.callback(
            self.cog, self.ctx, "recruiter", system_prompt="Screen applicants."
        )

    async def test_unknown_agent_replies_without_checking_permission(self) -> None:
        answer = await self.cog.run_agent(self.ctx, "ghost", "hello")

        self.assertIsNone(answer)
        self.assertEqual(self.bot.corridor.replies[-1]["title"], "Unknown agent")
        self.assertEqual(self.bot.corridor.permission_checks, [])

    async def test_denied_permission_produces_no_answer(self) -> None:
        self.bot.corridor = FakeCorridor(allow_permission=False)
        await self.cog.cog_load()

        answer = await self.cog.run_agent(self.ctx, "recruiter", "hello")

        self.assertIsNone(answer)
        self.assertEqual(self.bot.corridor.permission_checks, ["employee"])

    async def test_llm_not_ready_replies_and_produces_no_answer(self) -> None:
        self.bot.corridor = FakeCorridor(llm_ready=False)
        await self.cog.cog_load()

        answer = await self.cog.run_agent(self.ctx, "recruiter", "hello")

        self.assertIsNone(answer)
        self.assertIn("not configured", self.bot.corridor.replies[-1]["description"])

    async def test_successful_answer_is_replied_and_returned(self) -> None:
        result = ToolLoopResult(
            tool_calls_made=1,
            stopped_reason="final_text",
            text="Looks like a strong candidate.",
            successful_tool_calls=1,
            failed_tool_calls=0,
        )
        self.cog._tool_loop_service = FakeToolLoop(result)  # type: ignore[assignment]

        answer = await self.cog.run_agent(self.ctx, "recruiter", "Evaluate this resume.")

        self.assertEqual(answer, "Looks like a strong candidate.")
        self.assertEqual(self.bot.corridor.replies[-1]["title"], "recruiter")
        self.assertEqual(
            self.bot.corridor.replies[-1]["description"], "Looks like a strong candidate."
        )

    async def test_a_stopped_loop_without_final_text_produces_no_answer(self) -> None:
        result = ToolLoopResult(
            tool_calls_made=8,
            stopped_reason="max_tool_calls",
            text=None,
            successful_tool_calls=8,
            failed_tool_calls=0,
        )
        self.cog._tool_loop_service = FakeToolLoop(result)  # type: ignore[assignment]

        answer = await self.cog.run_agent(self.ctx, "recruiter", "hello")

        self.assertIsNone(answer)
        self.assertIn("could not produce an answer", self.bot.corridor.replies[-1]["description"])

    async def test_activity_is_published_while_running(self) -> None:
        async def _tool_loop_run(**kwargs: object) -> ToolLoopResult:
            on_activity = kwargs["on_activity"]
            assert callable(on_activity)
            await on_activity("thinking")  # type: ignore[misc]
            return ToolLoopResult(
                tool_calls_made=0,
                stopped_reason="final_text",
                text="done",
                successful_tool_calls=0,
                failed_tool_calls=0,
            )

        class _Loop:
            run = staticmethod(_tool_loop_run)

        self.cog._tool_loop_service = _Loop()  # type: ignore[assignment]

        await self.cog.run_agent(self.ctx, "recruiter", "hello")

        self.assertTrue(self.bot.corridor.published_events)


class TestCogLoadAutoLoadsCorridor(unittest.IsolatedAsyncioTestCase):
    """required_cogs in info.json only tells Downloader what to install --
    Red does not auto-load a dependency at runtime just because it's
    declared there. Regression test for: unload corridor, then load this
    cog -> it must pull corridor back in instead of failing to load."""

    async def test_cog_load_loads_corridor_when_not_already_loaded(self) -> None:
        bot = FakeBot(preloaded=False)
        cog = Bootcamp(bot=bot)
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
            await Bootcamp(bot=bot).cog_load()

        self.assertEqual(bot.load_extension_calls, [])


class TestDependentRegistration(unittest.IsolatedAsyncioTestCase):
    """Regression test for: unloading corridor left dependent cogs like this
    one running with a stale corridor reference instead of also being
    unloaded. cog_load/cog_unload must keep corridor's dependent registry in
    sync so corridor's own cog_unload can cascade correctly."""

    async def test_cog_load_registers_with_corridor(self) -> None:
        bot = FakeBot()
        cog = Bootcamp(bot=bot)

        await cog.cog_load()

        self.assertIn("bootcamp", bot.corridor.registered_dependents)

    async def test_cog_unload_unregisters_from_corridor(self) -> None:
        bot = FakeBot()
        cog = Bootcamp(bot=bot)
        await cog.cog_load()

        await cog.cog_unload()

        self.assertNotIn("bootcamp", bot.corridor.registered_dependents)


class TestCogUnloadTearsDownEveryAgent(unittest.IsolatedAsyncioTestCase):
    """cog_unload must remove every custom agent this cog registered, not
    just stop tracking them locally -- corridor's directory (and pico's
    per-turn tool list built from it) would otherwise keep offering a
    now-unloaded agent."""

    async def test_cog_unload_unregisters_the_whole_owner(self) -> None:
        bot = FakeBot()
        cog = Bootcamp(bot=bot)
        await cog.cog_load()
        ctx = FakeContext()
        await cog.create.callback(cog, ctx, "recruiter", system_prompt="p")
        await cog.create.callback(cog, ctx, "onboarder", system_prompt="p")

        await cog.cog_unload()

        self.assertEqual(bot.corridor.unregistered_agent_owners, ["Bootcamp"])
        self.assertEqual(bot.corridor.registered_agents, {})


class TestRestoreOnLoad(unittest.IsolatedAsyncioTestCase):
    """corridor's in-memory AgentDirectoryService does not survive a bot
    restart even though this cog's own Config does -- cog_load must
    re-register every previously-created agent, and notify the owner
    (best-effort) if any fails."""

    async def test_persisted_agents_are_re_registered_on_a_fresh_load(self) -> None:
        bot = FakeBot()
        first = Bootcamp(bot=bot)
        await first.cog_load()
        ctx = FakeContext()
        await first.create.callback(first, ctx, "recruiter", system_prompt="p")
        # Simulate a bot restart: a fresh cog instance, corridor's
        # in-memory directory wiped, but the same underlying Config store
        # (FakeBot's corridor.registered_agents is corridor's own state,
        # not this cog's -- clearing it mirrors a real restart).
        bot.corridor.registered_agents.clear()
        second = Bootcamp(bot=bot)
        second._repository = first._repository  # same "persisted" Config

        await second.cog_load()

        self.assertIn("recruiter", bot.corridor.registered_agents)

    async def test_a_restore_failure_notifies_the_owner_without_failing_load(self) -> None:
        bot = FakeBot()
        first = Bootcamp(bot=bot)
        await first.cog_load()
        ctx = FakeContext()
        await first.create.callback(first, ctx, "recruiter", system_prompt="p")
        second = Bootcamp(bot=bot)
        second._repository = first._repository

        # Force the re-registration itself to fail on the second load, the
        # same way a genuine cross-owner collision would.
        original_register = bot.corridor.register_agent

        async def _failing_register(agent: object, *, owner: str) -> None:
            raise ValueError("agent_key 'recruiter' is already registered by 'Architect'")

        bot.corridor.register_agent = _failing_register  # type: ignore[method-assign]
        await second.cog_load()
        bot.corridor.register_agent = original_register  # type: ignore[method-assign]

        self.assertTrue(bot.owner_messages)
        self.assertIn("recruiter", bot.owner_messages[0])
