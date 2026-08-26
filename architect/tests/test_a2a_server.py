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
from ..infrastructure.a2a_server import A2AServer, ArchitectAgentExecutor, build_agent_card
from ..tools.placeholder_tools import ReviewDesignTool
from .conftest import FakeEventQueue, FakeLLMSettings, FakeRequestContext


def _settings(max_tool_calls: int = 5, *, debug_logging: bool = False) -> GlobalSettings:
    return GlobalSettings(
        max_tool_calls=max_tool_calls,
        system_prompt="sys",
        a2a_host="127.0.0.1",
        a2a_port=8931,
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


def _dummy_executor() -> ArchitectAgentExecutor:
    return ArchitectAgentExecutor(
        tool_loop=ScriptedToolLoop(ToolLoopResult(0, "final_text", text="unused")),  # type: ignore[arg-type]
        tools=[],
        settings=lambda: _settings_async(),
        llm_settings=lambda: _llm_settings_async(),  # type: ignore[arg-type, return-value]
    )


class TestA2AServerBindFailure(unittest.IsolatedAsyncioTestCase):
    """Regression test for a real production incident: uvicorn's own
    Server.startup() calls sys.exit() on a bind failure (there, a broken
    resolver made even 127.0.0.1 unbindable; here, a port already in use
    forces the same failure path deterministically). That raised
    SystemExit -- a BaseException, not an Exception -- straight out of
    A2AServer.start(), uncaught by anything up the call chain, and crashed
    the entire bot process instead of just failing to start this one
    listener. start() must report the failure instead of raising it."""

    async def test_start_reports_a_bind_failure_instead_of_raising(self) -> None:
        first = A2AServer(_dummy_executor())
        first_error = await first.start(host="127.0.0.1", port=8940, tools=[])
        self.addAsyncCleanup(first.stop)
        self.assertIsNone(first_error)

        second = A2AServer(_dummy_executor())

        second_error = await second.start(host="127.0.0.1", port=8940, tools=[])

        self.assertIsNotNone(second_error)
        self.assertFalse(second.running)

    async def test_a_failed_start_does_not_leave_a_dangling_server_or_task(self) -> None:
        server = A2AServer(_dummy_executor())
        blocker = A2AServer(_dummy_executor())
        await blocker.start(host="127.0.0.1", port=8941, tools=[])
        self.addAsyncCleanup(blocker.stop)

        error = await server.start(host="127.0.0.1", port=8941, tools=[])

        self.assertIsNotNone(error)
        self.assertFalse(server.running)
        await server.stop()  # must not raise even though start() never fully succeeded


class TestBuildAgentCard(unittest.TestCase):
    def test_one_skill_per_tool(self) -> None:
        card = build_agent_card(host="127.0.0.1", port=8931, tools=[ReviewDesignTool()])

        self.assertEqual([skill.id for skill in card.skills], ["review_design"])
        self.assertEqual(card.supported_interfaces[0].url, "http://127.0.0.1:8931/")

    def test_falls_back_to_a_generic_chat_skill_with_no_tools(self) -> None:
        card = build_agent_card(host="127.0.0.1", port=8931, tools=[])

        self.assertEqual([skill.id for skill in card.skills], ["chat"])
