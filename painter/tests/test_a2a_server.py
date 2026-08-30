"""PainterAgentExecutor bridges one inbound A2A message to
ToolLoopService and publishes the result as a real (not faked)
`TaskStatusUpdateEvent` via a fake `EventQueue` -- the a2a-sdk types
themselves are exercised for real since the package is an actual project
dependency, not mocked; only the queue/context are narrow test doubles
(see conftest.py's FakeEventQueue/FakeRequestContext)."""

from __future__ import annotations

import unittest

from a2a.types import TaskState
from pydantic import BaseModel

from ..application.tool_loop_service import ToolLoopResult
from ..domain import GlobalSettings
from ..infrastructure.a2a_server import PainterAgentExecutor, build_agent_card
from .conftest import FakeEventQueue, FakeLLMSettings, FakeRequestContext


class _StubInput(BaseModel):
    pass


class _StubOutput(BaseModel):
    status: str = "ok"


class _StubTool:
    """A minimal real ToolSpec -- painter has no placeholder-tools module
    (architect's `review_design`/`break_down_task` are architect-specific
    stand-ins, see docs/architect-design.md §8), so tests here just need
    something schema-shaped, not a specific tool's behavior."""

    name = "recolor_tiles"
    description = "A stub tool for exercising the tool loop."

    @property
    def Input(self) -> type[BaseModel]:
        return _StubInput

    @property
    def Output(self) -> type[BaseModel]:
        return _StubOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        return _StubOutput()


def _settings(max_tool_calls: int = 5, *, debug_logging: bool = False) -> GlobalSettings:
    return GlobalSettings(
        max_tool_calls=max_tool_calls,
        system_prompt="sys",
        debug_logging=debug_logging,
    )


class ScriptedToolLoop:
    def __init__(self, result: ToolLoopResult, *, debug_events: list[str] | None = None) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []
        # If given, run() awaits kwargs["on_debug_event"] with each of these
        # in turn -- stands in for painter's own tool loop reporting
        # thinking/tool-call/result text mid-run.
        self._debug_events = debug_events or []

    async def run(self, **kwargs: object) -> ToolLoopResult:
        self.calls.append(kwargs)
        on_debug_event = kwargs.get("on_debug_event")
        if on_debug_event is not None:
            for event in self._debug_events:
                await on_debug_event(event)  # type: ignore[operator]
        return self.result


class TestPainterAgentExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_execute_completes_the_task_with_the_final_text(self) -> None:
        tool_loop = ScriptedToolLoop(
            ToolLoopResult(
                1, "final_text", "the answer", successful_tool_calls=1, failed_tool_calls=0
            )
        )
        executor = PainterAgentExecutor(
            tool_loop=tool_loop,  # type: ignore[arg-type]
            tools=[_StubTool()],
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

    async def test_execute_reports_tool_calls_made_as_message_metadata(self) -> None:
        """pico's `ArchitectClient` reads this back to surface "Tool calls"/
        "Successful tool calls"/"Failing tool calls" fields on the
        "📩 ... replied" Discord embed -- see
        pico/infrastructure/architect_client.py's `_metadata_int`."""

        tool_loop = ScriptedToolLoop(
            ToolLoopResult(
                4, "final_text", "the answer", successful_tool_calls=3, failed_tool_calls=1
            )
        )
        executor = PainterAgentExecutor(
            tool_loop=tool_loop,  # type: ignore[arg-type]
            tools=[],
            settings=lambda: _settings_async(),
            llm_settings=lambda: _llm_settings_async(),  # type: ignore[arg-type, return-value]
        )
        queue = FakeEventQueue()
        context = FakeRequestContext("please help")

        await executor.execute(context, queue)  # type: ignore[arg-type]

        final_message = queue.events[-1].status.message
        self.assertEqual(final_message.metadata["tool_calls_made"], 4)
        self.assertEqual(final_message.metadata["successful_tool_calls"], 3)
        self.assertEqual(final_message.metadata["failed_tool_calls"], 1)

    async def test_execute_passes_publish_activity_through_to_the_tool_loop(self) -> None:
        tool_loop = ScriptedToolLoop(
            ToolLoopResult(
                1, "final_text", "the answer", successful_tool_calls=1, failed_tool_calls=0
            )
        )
        reported: list[str] = []

        async def publish_activity(summary: str) -> None:
            reported.append(summary)

        executor = PainterAgentExecutor(
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
        tool_loop = ScriptedToolLoop(
            ToolLoopResult(
                1, "final_text", "the answer", successful_tool_calls=1, failed_tool_calls=0
            )
        )
        executor = PainterAgentExecutor(
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
        tool_loop = ScriptedToolLoop(
            ToolLoopResult(0, "final_text", "unused", successful_tool_calls=0, failed_tool_calls=0)
        )
        executor = PainterAgentExecutor(
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

    async def test_execute_appends_mcp_tools_to_the_fixed_tools(self) -> None:
        tool_loop = ScriptedToolLoop(
            ToolLoopResult(
                1, "final_text", "the answer", successful_tool_calls=1, failed_tool_calls=0
            )
        )
        mcp_tool = _StubTool()
        mcp_tool.name = "report_error"
        calls = 0

        async def mcp_tools() -> list[object]:
            nonlocal calls
            calls += 1
            return [mcp_tool]

        executor = PainterAgentExecutor(
            tool_loop=tool_loop,  # type: ignore[arg-type]
            tools=[_StubTool()],
            settings=lambda: _settings_async(),
            llm_settings=lambda: _llm_settings_async(),  # type: ignore[arg-type, return-value]
            mcp_tools=mcp_tools,  # type: ignore[arg-type]
        )
        queue = FakeEventQueue()
        context = FakeRequestContext("please help")

        await executor.execute(context, queue)  # type: ignore[arg-type]

        tool_names = [tool.name for tool in tool_loop.calls[0]["tools"]]  # type: ignore[union-attr]
        self.assertEqual(tool_names, ["recolor_tiles", "report_error"])
        self.assertEqual(calls, 1)

    async def test_execute_refetches_mcp_tools_every_call(self) -> None:
        """A bot owner flipping suggestionbox's per-agent toggle must take
        effect on painter's very next A2A message, not require a reload
        -- see docs/suggestionbox-design.md §6."""

        tool_loop = ScriptedToolLoop(
            ToolLoopResult(
                1, "final_text", "the answer", successful_tool_calls=1, failed_tool_calls=0
            )
        )
        calls = 0

        async def mcp_tools() -> list[object]:
            nonlocal calls
            calls += 1
            return []

        executor = PainterAgentExecutor(
            tool_loop=tool_loop,  # type: ignore[arg-type]
            tools=[],
            settings=lambda: _settings_async(),
            llm_settings=lambda: _llm_settings_async(),  # type: ignore[arg-type, return-value]
            mcp_tools=mcp_tools,  # type: ignore[arg-type]
        )
        queue = FakeEventQueue()

        await executor.execute(FakeRequestContext("first"), queue)  # type: ignore[arg-type]
        await executor.execute(FakeRequestContext("second"), queue)  # type: ignore[arg-type]

        self.assertEqual(calls, 2)

    async def test_execute_with_no_mcp_tools_callable_uses_only_the_fixed_tools(self) -> None:
        tool_loop = ScriptedToolLoop(
            ToolLoopResult(
                1, "final_text", "the answer", successful_tool_calls=1, failed_tool_calls=0
            )
        )
        executor = PainterAgentExecutor(
            tool_loop=tool_loop,  # type: ignore[arg-type]
            tools=[_StubTool()],
            settings=lambda: _settings_async(),
            llm_settings=lambda: _llm_settings_async(),  # type: ignore[arg-type, return-value]
        )
        queue = FakeEventQueue()
        context = FakeRequestContext("please help")

        await executor.execute(context, queue)  # type: ignore[arg-type]

        tool_names = [tool.name for tool in tool_loop.calls[0]["tools"]]  # type: ignore[union-attr]
        self.assertEqual(tool_names, ["recolor_tiles"])

    async def test_execute_emits_debug_status_updates_when_the_tool_loop_reports_them(
        self,
    ) -> None:
        tool_loop = ScriptedToolLoop(
            ToolLoopResult(
                1, "final_text", "the answer", successful_tool_calls=1, failed_tool_calls=0
            ),
            debug_events=["thinking: let me check", "calling echo({})"],
        )
        executor = PainterAgentExecutor(
            tool_loop=tool_loop,  # type: ignore[arg-type]
            tools=[],
            settings=lambda: _settings_async(debug_logging=True),
            llm_settings=lambda: _llm_settings_async(),  # type: ignore[arg-type, return-value]
        )
        queue = FakeEventQueue()
        context = FakeRequestContext("please help")

        await executor.execute(context, queue)  # type: ignore[arg-type]

        working_events = [
            event
            for event in queue.events
            if event.status.state == TaskState.TASK_STATE_WORKING
            and event.status.HasField("message")
        ]
        texts = [event.status.message.parts[0].text for event in working_events]
        self.assertEqual(texts, ["thinking: let me check", "calling echo({})"])
        # The debug updates land before the final completion, not after.
        self.assertEqual(queue.events[-1].status.state, TaskState.TASK_STATE_COMPLETED)

    async def test_execute_swallows_a_failure_to_emit_a_debug_status_update(self) -> None:
        """A transport hiccup while streaming a debug event must never break
        the tool loop or suppress the real final answer -- same convention
        CogBase._publish_activity's own best-effort try/except follows."""

        class RaisingEventQueue(FakeEventQueue):
            async def enqueue_event(self, event: object) -> None:
                await super().enqueue_event(event)
                # event 1 = initial Task, event 2 = start_work()'s WORKING
                # update, event 3 = the debug update itself -- fail exactly
                # that one.
                if len(self.events) == 3:
                    raise RuntimeError("boom")

        tool_loop = ScriptedToolLoop(
            ToolLoopResult(
                1, "final_text", "the answer", successful_tool_calls=1, failed_tool_calls=0
            ),
            debug_events=["thinking: let me check"],
        )
        executor = PainterAgentExecutor(
            tool_loop=tool_loop,  # type: ignore[arg-type]
            tools=[],
            settings=lambda: _settings_async(debug_logging=True),
            llm_settings=lambda: _llm_settings_async(),  # type: ignore[arg-type, return-value]
        )
        queue = RaisingEventQueue()
        context = FakeRequestContext("please help")

        await executor.execute(context, queue)  # type: ignore[arg-type]

        self.assertEqual(queue.events[-1].status.state, TaskState.TASK_STATE_COMPLETED)
        self.assertEqual(queue.events[-1].status.message.parts[0].text, "the answer")

    async def test_execute_fails_the_task_when_the_loop_hits_max_tool_calls(self) -> None:
        tool_loop = ScriptedToolLoop(
            ToolLoopResult(5, "max_tool_calls", None, successful_tool_calls=3, failed_tool_calls=2)
        )
        executor = PainterAgentExecutor(
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
        card = build_agent_card(tools=[_StubTool()])

        self.assertEqual([skill.id for skill in card.skills], ["recolor_tiles"])

    def test_falls_back_to_a_generic_chat_skill_with_no_tools(self) -> None:
        card = build_agent_card(tools=[])

        self.assertEqual([skill.id for skill in card.skills], ["chat"])

    def test_advertises_streaming_so_debug_status_updates_can_reach_a_caller(self) -> None:
        card = build_agent_card(tools=[])

        self.assertTrue(card.capabilities.streaming)

    def test_description_warns_that_only_explicit_instructions_are_acted_on(self) -> None:
        """Same convention architect's own card documents (a real
        production incident there: a stated goal alongside an instruction
        was read as a second thing to also do, rather than as context --
        see architect/tests/test_a2a_server.py's own version of this
        test). This card's description is the one place a consulting
        agent's LLM sees painter's own behavior (see
        pico/adapters/listener.py, which sets ConsultAgentTool.description
        to this exact string) -- so painter documents its own literalism
        here too, rather than every caller having to assume it."""

        card = build_agent_card(tools=[])

        self.assertIn("explicit instruction", card.description)

    def test_description_warns_that_it_has_no_memory_of_past_consultations(self) -> None:
        """A follow-up delegation (e.g. asking painter to now recolor a
        wall after an earlier call recolored the floor next to it) is a
        brand-new prompt with no memory of the earlier one -- see
        PainterAgentExecutor's
        own docstring: 'there is no persisted multi-turn conversation'. A
        consulting agent's LLM needs to know that to restate whatever
        context a follow-up depends on, rather than assuming painter
        remembers."""

        card = build_agent_card(tools=[])

        self.assertIn("no memory", card.description)
