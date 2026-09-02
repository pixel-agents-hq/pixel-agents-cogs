"""`GenericAgentExecutor` bridges one inbound A2A message to a tool loop
and publishes the result as a real (not faked) `TaskStatusUpdateEvent` via
a fake `EventQueue` -- the a2a-sdk types themselves are exercised for real
since the package is an actual project dependency, not mocked; only the
queue/context are narrow test doubles.

This is the canonical, single copy of the behavioral coverage architect's
and painter's own `tests/test_a2a_server.py` used to each carry in full --
both cogs' suites now only cover what's genuinely cog-specific (their own
`build_agent_card` strings, and a thin `agent_name`/logger wiring smoke
test), relying on this suite for the shared mechanics."""

from __future__ import annotations

import logging
import unittest
from dataclasses import dataclass, field
from typing import Any

from a2a.types import TaskState
from a2a.utils.errors import UnsupportedOperationError

from ..domain import LLMSettings
from ..domain.agent_executor import GenericAgentExecutor, ToolSpecLike, build_agent_card


@dataclass
class FakeToolLoopResult:
    tool_calls_made: int
    stopped_reason: str
    text: str | None
    successful_tool_calls: int = 0
    failed_tool_calls: int = 0


@dataclass
class FakeAgentSettings:
    system_prompt: str = "sys"
    max_tool_calls: int = 5
    debug_logging: bool = False


@dataclass
class FakeTool:
    name: str
    description: str = "a tool"


class ScriptedToolLoop:
    def __init__(
        self, result: FakeToolLoopResult, *, debug_events: list[str] | None = None
    ) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []
        self._debug_events = debug_events or []

    async def run(self, **kwargs: object) -> FakeToolLoopResult:
        self.calls.append(kwargs)
        on_debug_event = kwargs.get("on_debug_event")
        if on_debug_event is not None:
            for event in self._debug_events:
                await on_debug_event(event)  # type: ignore[operator]
        return self.result


@dataclass
class FakeEventQueue:
    """A bare capture of enqueued A2A events -- real `TaskStatusUpdateEvent`/
    `Message` protobuf objects, not further faked, since `GenericAgentExecutor`
    never inspects the queue itself, only enqueues into it."""

    events: list[Any] = field(default_factory=list)

    async def enqueue_event(self, event: Any) -> None:
        self.events.append(event)


class FakeRequestContext:
    """The narrow slice of `a2a.server.agent_execution.context.RequestContext`
    `GenericAgentExecutor` actually reads."""

    def __init__(
        self, user_input: str, *, task_id: str = "task-1", context_id: str = "ctx-1"
    ) -> None:
        self._user_input = user_input
        self.task_id = task_id
        self.context_id = context_id

    def get_user_input(self, delimiter: str = "\n") -> str:
        return self._user_input


def _executor(
    tool_loop: ScriptedToolLoop,
    *,
    tools: list[ToolSpecLike] | None = None,
    debug_logging: bool = False,
    llm_settings: LLMSettings | None = None,
    publish_activity: Any = None,
    mcp_tools: Any = None,
) -> GenericAgentExecutor:
    settings = FakeAgentSettings(debug_logging=debug_logging)
    resolved_llm_settings = llm_settings or LLMSettings(
        llm_base_url="https://example.test/", llm_api_key="sk-test", llm_model="test-model"
    )

    async def _settings() -> FakeAgentSettings:
        return settings

    async def _llm_settings() -> LLMSettings:
        return resolved_llm_settings

    return GenericAgentExecutor(
        agent_name="Testagent",
        logger=logging.getLogger("red.testagent"),
        tool_loop=tool_loop,  # type: ignore[arg-type]
        tools=tools or [],
        settings=_settings,
        llm_settings=_llm_settings,
        publish_activity=publish_activity,
        mcp_tools=mcp_tools,
    )


class TestGenericAgentExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_execute_completes_the_task_with_the_final_text(self) -> None:
        tool_loop = ScriptedToolLoop(
            FakeToolLoopResult(1, "final_text", "the answer", successful_tool_calls=1)
        )
        executor = _executor(tool_loop, tools=[FakeTool("review_design")])
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

    async def test_execute_reports_tool_calls_made_as_message_metadata(self) -> None:
        tool_loop = ScriptedToolLoop(
            FakeToolLoopResult(
                4, "final_text", "the answer", successful_tool_calls=3, failed_tool_calls=1
            )
        )
        executor = _executor(tool_loop)
        queue = FakeEventQueue()

        await executor.execute(FakeRequestContext("please help"), queue)  # type: ignore[arg-type]

        final_message = queue.events[-1].status.message
        self.assertEqual(final_message.metadata["tool_calls_made"], 4)
        self.assertEqual(final_message.metadata["successful_tool_calls"], 3)
        self.assertEqual(final_message.metadata["failed_tool_calls"], 1)

    async def test_execute_passes_publish_activity_through_to_the_tool_loop(self) -> None:
        tool_loop = ScriptedToolLoop(FakeToolLoopResult(1, "final_text", "the answer"))
        reported: list[str] = []

        async def publish_activity(summary: str) -> None:
            reported.append(summary)

        executor = _executor(tool_loop, publish_activity=publish_activity)
        queue = FakeEventQueue()

        await executor.execute(FakeRequestContext("please help"), queue)  # type: ignore[arg-type]

        self.assertIs(tool_loop.calls[0]["on_activity"], publish_activity)

    async def test_execute_passes_debug_logging_through_to_the_tool_loop(self) -> None:
        tool_loop = ScriptedToolLoop(FakeToolLoopResult(1, "final_text", "the answer"))
        executor = _executor(tool_loop, debug_logging=True)
        queue = FakeEventQueue()

        await executor.execute(FakeRequestContext("please help"), queue)  # type: ignore[arg-type]

        self.assertEqual(tool_loop.calls[0]["debug"], True)

    async def test_execute_fails_the_task_when_llm_is_not_configured(self) -> None:
        tool_loop = ScriptedToolLoop(FakeToolLoopResult(0, "final_text", "unused"))
        unready = LLMSettings(
            llm_base_url="https://example.test/", llm_api_key=None, llm_model=None
        )
        executor = _executor(tool_loop, llm_settings=unready)
        queue = FakeEventQueue()

        await executor.execute(FakeRequestContext("please help"), queue)  # type: ignore[arg-type]

        states = [event.status.state for event in queue.events]
        self.assertEqual(states[-1], TaskState.TASK_STATE_FAILED)
        self.assertEqual(tool_loop.calls, [])

    async def test_execute_appends_mcp_tools_to_the_fixed_tools(self) -> None:
        tool_loop = ScriptedToolLoop(FakeToolLoopResult(1, "final_text", "the answer"))
        calls = 0

        async def mcp_tools() -> list[object]:
            nonlocal calls
            calls += 1
            return [FakeTool("report_error")]

        executor = _executor(tool_loop, tools=[FakeTool("review_design")], mcp_tools=mcp_tools)
        queue = FakeEventQueue()

        await executor.execute(FakeRequestContext("please help"), queue)  # type: ignore[arg-type]

        tool_names = [tool.name for tool in tool_loop.calls[0]["tools"]]  # type: ignore[union-attr]
        self.assertEqual(tool_names, ["review_design", "report_error"])
        self.assertEqual(calls, 1)

    async def test_execute_refetches_mcp_tools_every_call(self) -> None:
        """A bot owner flipping suggestionbox's per-agent toggle must take
        effect on the very next A2A message, not require a reload -- see
        docs/suggestionbox-design.md §6."""

        tool_loop = ScriptedToolLoop(FakeToolLoopResult(1, "final_text", "the answer"))
        calls = 0

        async def mcp_tools() -> list[object]:
            nonlocal calls
            calls += 1
            return []

        executor = _executor(tool_loop, mcp_tools=mcp_tools)
        queue = FakeEventQueue()

        await executor.execute(FakeRequestContext("first"), queue)  # type: ignore[arg-type]
        await executor.execute(FakeRequestContext("second"), queue)  # type: ignore[arg-type]

        self.assertEqual(calls, 2)

    async def test_execute_with_no_mcp_tools_callable_uses_only_the_fixed_tools(self) -> None:
        tool_loop = ScriptedToolLoop(FakeToolLoopResult(1, "final_text", "the answer"))
        executor = _executor(tool_loop, tools=[FakeTool("review_design")])
        queue = FakeEventQueue()

        await executor.execute(FakeRequestContext("please help"), queue)  # type: ignore[arg-type]

        tool_names = [tool.name for tool in tool_loop.calls[0]["tools"]]  # type: ignore[union-attr]
        self.assertEqual(tool_names, ["review_design"])

    async def test_execute_emits_debug_status_updates_when_the_tool_loop_reports_them(
        self,
    ) -> None:
        tool_loop = ScriptedToolLoop(
            FakeToolLoopResult(1, "final_text", "the answer"),
            debug_events=["thinking: let me check", "calling echo({})"],
        )
        executor = _executor(tool_loop, debug_logging=True)
        queue = FakeEventQueue()

        await executor.execute(FakeRequestContext("please help"), queue)  # type: ignore[arg-type]

        working_events = [
            event
            for event in queue.events
            if event.status.state == TaskState.TASK_STATE_WORKING
            and event.status.HasField("message")
        ]
        texts = [event.status.message.parts[0].text for event in working_events]
        self.assertEqual(texts, ["thinking: let me check", "calling echo({})"])
        self.assertEqual(queue.events[-1].status.state, TaskState.TASK_STATE_COMPLETED)

    async def test_execute_swallows_a_failure_to_emit_a_debug_status_update(self) -> None:
        """A transport hiccup while streaming a debug event must never break
        the tool loop or suppress the real final answer -- same convention
        `CogBase._publish_activity`'s own best-effort try/except follows."""

        class RaisingEventQueue(FakeEventQueue):
            async def enqueue_event(self, event: object) -> None:
                await super().enqueue_event(event)
                # event 1 = initial Task, event 2 = start_work()'s WORKING
                # update, event 3 = the debug update itself -- fail exactly
                # that one.
                if len(self.events) == 3:
                    raise RuntimeError("boom")

        tool_loop = ScriptedToolLoop(
            FakeToolLoopResult(1, "final_text", "the answer"),
            debug_events=["thinking: let me check"],
        )
        executor = _executor(tool_loop, debug_logging=True)
        queue = RaisingEventQueue()

        await executor.execute(FakeRequestContext("please help"), queue)  # type: ignore[arg-type]

        self.assertEqual(queue.events[-1].status.state, TaskState.TASK_STATE_COMPLETED)
        self.assertEqual(queue.events[-1].status.message.parts[0].text, "the answer")

    async def test_execute_fails_the_task_when_the_loop_hits_max_tool_calls(self) -> None:
        tool_loop = ScriptedToolLoop(
            FakeToolLoopResult(
                5, "max_tool_calls", None, successful_tool_calls=3, failed_tool_calls=2
            )
        )
        executor = _executor(tool_loop)
        queue = FakeEventQueue()

        await executor.execute(FakeRequestContext("please help"), queue)  # type: ignore[arg-type]

        states = [event.status.state for event in queue.events]
        self.assertEqual(states[-1], TaskState.TASK_STATE_FAILED)

    async def test_cancel_raises_unsupported_operation(self) -> None:
        executor = _executor(ScriptedToolLoop(FakeToolLoopResult(0, "final_text", None)))

        with self.assertRaises(UnsupportedOperationError):
            await executor.cancel(FakeRequestContext("x"), FakeEventQueue())  # type: ignore[arg-type]

    async def test_error_strings_use_the_configured_agent_name(self) -> None:
        tool_loop = ScriptedToolLoop(FakeToolLoopResult(0, "max_tool_calls", None))
        executor = _executor(tool_loop)
        queue = FakeEventQueue()

        await executor.execute(FakeRequestContext("please help"), queue)  # type: ignore[arg-type]

        final_message = queue.events[-1].status.message
        self.assertIn("Testagent", final_message.parts[0].text)


class TestBuildAgentCard(unittest.TestCase):
    def test_one_skill_per_tool(self) -> None:
        card = build_agent_card(
            name="testagent",
            description="a test agent",
            version="0.1.0",
            tools=[FakeTool("review_design")],
            tag="testagent",
        )

        self.assertEqual([skill.id for skill in card.skills], ["review_design"])

    def test_falls_back_to_a_generic_chat_skill_with_no_tools(self) -> None:
        card = build_agent_card(
            name="testagent", description="a test agent", version="0.1.0", tools=[], tag="testagent"
        )

        self.assertEqual([skill.id for skill in card.skills], ["chat"])

    def test_advertises_streaming_so_debug_status_updates_can_reach_a_caller(self) -> None:
        card = build_agent_card(
            name="testagent", description="a test agent", version="0.1.0", tools=[], tag="testagent"
        )

        self.assertTrue(card.capabilities.streaming)

    def test_skills_are_tagged_with_the_given_tag(self) -> None:
        card = build_agent_card(
            name="testagent",
            description="a test agent",
            version="0.1.0",
            tools=[FakeTool("a_tool")],
            tag="testagent",
        )

        self.assertEqual(list(card.skills[0].tags), ["testagent"])


if __name__ == "__main__":
    unittest.main()
