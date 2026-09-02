"""What's genuinely architect-specific about its A2A surface: `build_agent_card`'s
own name/description/tag strings, and that `ArchitectAgentExecutor` wires
`agent_name="Architect"` through to `GenericAgentExecutor` correctly. The
shared executor mechanics (`execute`/`_run_turn`/`_fail_safely`/`cancel`)
are covered once, generically, by `corridor/tests/test_agent_executor.py` --
see that module's own docstring."""

from __future__ import annotations

import unittest

from a2a.types import TaskState

from ..application.tool_loop_service import ToolLoopResult
from ..domain import GlobalSettings
from ..infrastructure.a2a_server import ArchitectAgentExecutor, build_agent_card
from ..tools.placeholder_tools import ReviewDesignTool
from .conftest import FakeEventQueue, FakeLLMSettings, FakeRequestContext


class ScriptedToolLoop:
    def __init__(self, result: ToolLoopResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def run(self, **kwargs: object) -> ToolLoopResult:
        self.calls.append(kwargs)
        return self.result


class TestArchitectAgentExecutorWiring(unittest.IsolatedAsyncioTestCase):
    """A thin smoke test, not the full scenario matrix (see this module's
    own docstring): confirms `ArchitectAgentExecutor` actually reaches
    `GenericAgentExecutor`'s real behavior end to end, and that its
    error/log strings carry "Architect", not a generic placeholder."""

    async def test_completes_with_the_final_text_and_names_itself_in_failures(self) -> None:
        tool_loop = ScriptedToolLoop(
            ToolLoopResult(
                1, "final_text", "the answer", successful_tool_calls=1, failed_tool_calls=0
            )
        )
        executor = ArchitectAgentExecutor(
            tool_loop=tool_loop,  # type: ignore[arg-type]
            tools=[ReviewDesignTool()],
            settings=lambda: _settings_async(),
            llm_settings=lambda: _llm_settings_async(),  # type: ignore[arg-type, return-value]
        )
        queue = FakeEventQueue()

        await executor.execute(FakeRequestContext("please help"), queue)  # type: ignore[arg-type]

        self.assertEqual(queue.events[-1].status.state, TaskState.TASK_STATE_COMPLETED)
        self.assertEqual(tool_loop.calls[0]["user_input"], "please help")

    async def test_names_architect_in_the_not_configured_message(self) -> None:
        tool_loop = ScriptedToolLoop(
            ToolLoopResult(0, "final_text", "unused", successful_tool_calls=0, failed_tool_calls=0)
        )
        executor = ArchitectAgentExecutor(
            tool_loop=tool_loop,  # type: ignore[arg-type]
            tools=[],
            settings=lambda: _settings_async(),
            llm_settings=lambda: _unready_llm_settings_async(),  # type: ignore[arg-type, return-value]
        )
        queue = FakeEventQueue()

        await executor.execute(FakeRequestContext("please help"), queue)  # type: ignore[arg-type]

        final_message = queue.events[-1].status.message
        self.assertIn("Architect", final_message.parts[0].text)


async def _settings_async() -> GlobalSettings:
    return GlobalSettings(max_tool_calls=5, system_prompt="sys", debug_logging=False)


async def _llm_settings_async() -> FakeLLMSettings:
    return FakeLLMSettings()


async def _unready_llm_settings_async() -> FakeLLMSettings:
    return FakeLLMSettings(llm_api_key=None)


class TestBuildAgentCard(unittest.TestCase):
    """The card's URL is a placeholder here -- corridor.register_agent
    overwrites it with its own shared listener's URL (see
    docs/agent-directory-design.md; that rewrite is covered by corridor's
    own test suite, corridor/tests/test_agent_directory_domain.py). Skill
    construction itself is covered generically by
    corridor/tests/test_agent_executor.py::TestBuildAgentCard -- these
    only check architect's own name/description/tag content."""

    def test_one_skill_per_tool(self) -> None:
        card = build_agent_card(tools=[ReviewDesignTool()])

        self.assertEqual([skill.id for skill in card.skills], ["review_design"])

    def test_skills_are_tagged_architect(self) -> None:
        card = build_agent_card(tools=[ReviewDesignTool()])

        self.assertEqual(list(card.skills[0].tags), ["architect"])

    def test_name_is_architect(self) -> None:
        card = build_agent_card(tools=[])

        self.assertEqual(card.name, "architect")

    def test_description_warns_that_only_explicit_instructions_are_acted_on(self) -> None:
        """Regression guard for a real incident: a user asked (via pico) for
        architect to move a table and stated a goal that a chair also end up
        in the freed corner; architect moved only the table, reading the
        stated goal as context rather than a second instruction, and the
        user had to ask again. This card's description is the one place a
        consulting agent's LLM sees architect's own behavior (see
        pico/adapters/listener.py, which sets ConsultAgentTool.description
        to this exact string) -- so architect documents its own literalism
        here rather than every caller having to assume it."""

        card = build_agent_card(tools=[])

        self.assertIn("explicit instruction", card.description)

    def test_description_warns_that_it_has_no_memory_of_past_consultations(self) -> None:
        """A follow-up delegation (e.g. asking architect to now place the
        chair after an earlier call moved the table) is a brand-new prompt
        with no memory of the earlier one -- see `GenericAgentExecutor`'s
        own docstring: 'there is no persisted multi-turn conversation'. A
        consulting agent's LLM needs to know that to restate whatever
        context a follow-up depends on, rather than assuming architect
        remembers."""

        card = build_agent_card(tools=[])

        self.assertIn("no memory", card.description)


if __name__ == "__main__":
    unittest.main()
