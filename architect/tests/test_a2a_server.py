"""ArchitectAgentExecutor bridges one inbound A2A message to
ToolLoopService and publishes the result as a real (not faked)
`TaskStatusUpdateEvent` via a fake `EventQueue` -- the a2a-sdk types
themselves are exercised for real since the package is an actual project
dependency, not mocked; only the queue/context are narrow test doubles
(see conftest.py's FakeEventQueue/FakeRequestContext)."""

from __future__ import annotations

import unittest

from a2a.types import TaskState

from ..application.tool_loop_service import ToolLoopResult
from ..domain import GlobalSettings
from ..infrastructure.a2a_server import ArchitectAgentExecutor, build_agent_card
from ..tools.placeholder_tools import ReviewDesignTool
from .conftest import FakeEventQueue, FakeLLMSettings, FakeRequestContext


def _settings(max_tool_calls: int = 5, *, debug_logging: bool = False) -> GlobalSettings:
    return GlobalSettings(
        max_tool_calls=max_tool_calls,
        system_prompt="sys",
        ws_host="127.0.0.1",
        ws_port=8932,
        debug_logging=debug_logging,
    )


class ScriptedToolLoop:
    def __init__(self, result: ToolLoopResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def run(self, **kwargs: object) -> ToolLoopResult:
        self.calls.append(kwargs)
        return self.result


class TestArchitectAgentExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_execute_completes_the_task_with_the_final_text(self) -> None:
        tool_loop = ScriptedToolLoop(ToolLoopResult(1, "final_text", text="the answer"))
        executor = ArchitectAgentExecutor(
            tool_loop=tool_loop,  # type: ignore[arg-type]
            tools=[ReviewDesignTool()],
            settings=lambda: _settings_async(),
            llm_settings=lambda: _llm_settings_async(),  # type: ignore[arg-type, return-value]
        )
        queue = FakeEventQueue()
        context = FakeRequestContext("please help")

        await executor.execute(context, queue)  # type: ignore[arg-type]

        states = [event.status.state for event in queue.events]
        self.assertIn(TaskState.TASK_STATE_WORKING, states)
        self.assertEqual(states[-1], TaskState.TASK_STATE_COMPLETED)
        final_message = queue.events[-1].status.message
        self.assertEqual(final_message.parts[0].text, "the answer")
        self.assertEqual(tool_loop.calls[0]["user_input"], "please help")
        self.assertEqual(tool_loop.calls[0]["debug"], False)

    async def test_execute_passes_publish_activity_through_to_the_tool_loop(self) -> None:
        tool_loop = ScriptedToolLoop(ToolLoopResult(1, "final_text", text="the answer"))
        reported: list[str] = []

        async def publish_activity(summary: str) -> None:
            reported.append(summary)

        executor = ArchitectAgentExecutor(
            tool_loop=tool_loop,  # type: ignore[arg-type]
            tools=[],
            settings=lambda: _settings_async(),
            llm_settings=lambda: _llm_settings_async(),  # type: ignore[arg-type, return-value]
            publish_activity=publish_activity,
        )
        queue = FakeEventQueue()
        context = FakeRequestContext("please help")

        await executor.execute(context, queue)  # type: ignore[arg-type]

        self.assertIs(tool_loop.calls[0]["on_activity"], publish_activity)

    async def test_execute_passes_debug_logging_through_to_the_tool_loop(self) -> None:
        tool_loop = ScriptedToolLoop(ToolLoopResult(1, "final_text", text="the answer"))
        executor = ArchitectAgentExecutor(
            tool_loop=tool_loop,  # type: ignore[arg-type]
            tools=[],
            settings=lambda: _settings_async(debug_logging=True),
            llm_settings=lambda: _llm_settings_async(),  # type: ignore[arg-type, return-value]
        )
        queue = FakeEventQueue()
        context = FakeRequestContext("please help")

        await executor.execute(context, queue)  # type: ignore[arg-type]

        self.assertEqual(tool_loop.calls[0]["debug"], True)

    async def test_execute_fails_the_task_when_llm_is_not_configured(self) -> None:
        tool_loop = ScriptedToolLoop(ToolLoopResult(0, "final_text", text="unused"))
        executor = ArchitectAgentExecutor(
            tool_loop=tool_loop,  # type: ignore[arg-type]
            tools=[],
            settings=lambda: _settings_async(),
            llm_settings=lambda: _unready_llm_settings_async(),  # type: ignore[arg-type, return-value]
        )
        queue = FakeEventQueue()
        context = FakeRequestContext("please help")

        await executor.execute(context, queue)  # type: ignore[arg-type]

        states = [event.status.state for event in queue.events]
        self.assertEqual(states[-1], TaskState.TASK_STATE_FAILED)
        self.assertEqual(tool_loop.calls, [])

    async def test_execute_fails_the_task_when_the_loop_hits_max_tool_calls(self) -> None:
        tool_loop = ScriptedToolLoop(ToolLoopResult(5, "max_tool_calls", text=None))
        executor = ArchitectAgentExecutor(
            tool_loop=tool_loop,  # type: ignore[arg-type]
            tools=[],
            settings=lambda: _settings_async(),
            llm_settings=lambda: _llm_settings_async(),  # type: ignore[arg-type, return-value]
        )
        queue = FakeEventQueue()
        context = FakeRequestContext("please help")

        await executor.execute(context, queue)  # type: ignore[arg-type]

        states = [event.status.state for event in queue.events]
        self.assertEqual(states[-1], TaskState.TASK_STATE_FAILED)


async def _settings_async(*, debug_logging: bool = False) -> GlobalSettings:
    return _settings(debug_logging=debug_logging)


async def _llm_settings_async() -> FakeLLMSettings:
    return FakeLLMSettings()


async def _unready_llm_settings_async() -> FakeLLMSettings:
    return FakeLLMSettings(llm_api_key=None)


class TestBuildAgentCard(unittest.TestCase):
    """The card's URL is a placeholder here -- corridor.register_agent
    overwrites it with its own shared listener's URL (see
    docs/agent-directory-design.md; that rewrite is covered by corridor's
    own test suite, corridor/tests/test_agent_directory_domain.py)."""

    def test_one_skill_per_tool(self) -> None:
        card = build_agent_card(tools=[ReviewDesignTool()])

        self.assertEqual([skill.id for skill in card.skills], ["review_design"])

    def test_falls_back_to_a_generic_chat_skill_with_no_tools(self) -> None:
        card = build_agent_card(tools=[])

        self.assertEqual([skill.id for skill in card.skills], ["chat"])

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
