"""ToolLoopService: a fake LiteLLMClient double returns scripted tool_calls
sequences. Unlike pico's loop (which never returns raw text), animator's
loop treats the model's final no-tool-calls turn as the answer -- see
`application/tool_loop_service.py`'s module docstring."""

from __future__ import annotations

import unittest
from typing import Any

from pydantic import BaseModel, Field

from corridor.infrastructure.llm_client import (
    ChatCompletionChoice,
    ChatCompletionResponse,
    ChatCompletionResponseMessage,
    LLMRequestError,
    ToolCall,
    ToolCallFunction,
)

from ..application.tool_loop_service import ToolLoopService
from ..tools.attachment import Attachment


class AttachingInput(BaseModel):
    pass


class AttachingOutput(BaseModel):
    """Mirrors `DeliverPixelAgentsAssetsTool.Output`'s shape -- a `status`
    field plus attachments the LLM never sees serialized (this test reads
    them off the returned `Output` object directly, the same way
    `_execute` does, not via `model_dump_json()`)."""

    model_config = {"arbitrary_types_allowed": True}

    status: str = "ok"
    attachments: list[Attachment] = Field(default_factory=list, exclude=True)


class AttachingTool:
    name = "deliver"
    description = "Returns a fixed attachment."
    Input = AttachingInput
    Output = AttachingOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        return AttachingOutput(
            attachments=[
                Attachment(filename="preview.png", media_type="image/png", data=b"\x89PNG")
            ]
        )


class EchoInput(BaseModel):
    text: str


class EchoOutput(BaseModel):
    heard: str


class EchoTool:
    name = "echo"
    description = "Echoes text back."
    Input = EchoInput
    Output = EchoOutput

    def __init__(self) -> None:
        self.calls: list[EchoInput] = []

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, EchoInput)
        self.calls.append(raw_input)
        return EchoOutput(heard=raw_input.text)


class StatusInput(BaseModel):
    should_fail: bool = False


class StatusOutput(BaseModel):
    """Mirrors the real `status: Literal["ok", "error"]` convention every
    animator tool's `Output` follows (see office_tools.py) -- unlike
    `EchoOutput`, which deliberately has no `status` field at all, to cover
    the "no status field reported" success case too."""

    status: str = "ok"


class StatusTool:
    name = "status_tool"
    description = "Reports ok or error via its Output's status field."
    Input = StatusInput
    Output = StatusOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, StatusInput)
        return StatusOutput(status="error" if raw_input.should_fail else "ok")


class GetJobInput(BaseModel):
    job_id: str


class GetJobOutput(BaseModel):
    status: str = "ok"
    poll: int = 0


class GetJobTool:
    """A stub `get_job` -- real polls return a growing `logs` field, but
    this only needs a distinct `poll` counter per call to tell responses
    apart in an assertion."""

    name = "get_job"
    description = "Poll a job."
    Input = GetJobInput
    Output = GetJobOutput

    def __init__(self) -> None:
        self.calls = 0

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, GetJobInput)
        self.calls += 1
        return GetJobOutput(poll=self.calls)


def _tool_call(call_id: str, *, name: str = "echo", arguments: str = '{"text": "hi"}') -> ToolCall:
    return ToolCall(id=call_id, function=ToolCallFunction(name=name, arguments=arguments))


def _response(
    *, content: str | None = None, tool_calls: list[ToolCall] | None = None
) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionResponseMessage(
                    role="assistant", content=content, tool_calls=tool_calls
                )
            )
        ]
    )


class ScriptedLLM:
    def __init__(self, responses: list[ChatCompletionResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> ChatCompletionResponse:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class TestToolLoopService(unittest.IsolatedAsyncioTestCase):
    async def test_no_tool_calls_returns_the_final_text(self) -> None:
        llm = ScriptedLLM([_response(content="the answer is 42")])
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="what is the answer?",
            tools=[EchoTool()],
            max_tool_calls=5,
        )

        self.assertEqual(result.stopped_reason, "final_text")
        self.assertEqual(result.text, "the answer is 42")
        self.assertEqual(result.tool_calls_made, 0)
        self.assertEqual(result.successful_tool_calls, 0)
        self.assertEqual(result.failed_tool_calls, 0)

    async def test_forwards_request_timeout_seconds_to_each_llm_call(self) -> None:
        llm = ScriptedLLM([_response(content="ok")])
        service = ToolLoopService(llm)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="hi",
            tools=[],
            max_tool_calls=5,
            request_timeout_seconds=45.0,
        )

        self.assertEqual(llm.calls[0]["timeout_seconds"], 45.0)

    async def test_omitted_request_timeout_seconds_forwards_none(self) -> None:
        llm = ScriptedLLM([_response(content="ok")])
        service = ToolLoopService(llm)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="hi",
            tools=[],
            max_tool_calls=5,
        )

        self.assertIsNone(llm.calls[0]["timeout_seconds"])

    async def test_executes_a_tool_call_and_returns_the_eventual_final_text(self) -> None:
        llm = ScriptedLLM(
            [_response(tool_calls=[_tool_call("call-1")]), _response(content="done: hi")]
        )
        tool = EchoTool()
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo hi",
            tools=[tool],
            max_tool_calls=5,
        )

        self.assertEqual(result.stopped_reason, "final_text")
        self.assertEqual(result.text, "done: hi")
        self.assertEqual(result.tool_calls_made, 1)
        self.assertEqual(result.successful_tool_calls, 1)
        self.assertEqual(result.failed_tool_calls, 0)
        self.assertEqual(tool.calls[0].text, "hi")

    async def test_stops_at_max_tool_calls_with_no_text(self) -> None:
        llm = ScriptedLLM(
            [
                _response(tool_calls=[_tool_call("call-1")]),
                _response(tool_calls=[_tool_call("call-2")]),
            ]
        )
        tool = EchoTool()
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo hi",
            tools=[tool],
            max_tool_calls=2,
        )

        self.assertEqual(result.stopped_reason, "max_tool_calls")
        self.assertIsNone(result.text)
        self.assertEqual(result.tool_calls_made, 2)
        self.assertEqual(result.successful_tool_calls, 2)
        self.assertEqual(result.failed_tool_calls, 0)
        self.assertEqual(len(llm.calls), 2)

    async def test_llm_failure_stops_the_loop(self) -> None:
        llm = ScriptedLLM([LLMRequestError("boom")])
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo hi",
            tools=[EchoTool()],
            max_tool_calls=5,
        )

        self.assertEqual(result.stopped_reason, "llm_error")
        self.assertIsNone(result.text)
        self.assertEqual(result.tool_calls_made, 0)
        self.assertEqual(result.successful_tool_calls, 0)
        self.assertEqual(result.failed_tool_calls, 0)

    async def test_unknown_tool_name_reports_an_error_without_crashing_the_loop(self) -> None:
        llm = ScriptedLLM(
            [_response(tool_calls=[_tool_call("call-1", name="nope")]), _response(content="ok")]
        )
        tool = EchoTool()
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo hi",
            tools=[tool],
            max_tool_calls=5,
        )

        self.assertEqual(result.tool_calls_made, 1)
        self.assertEqual(result.successful_tool_calls, 0)
        self.assertEqual(result.failed_tool_calls, 1)
        self.assertEqual(tool.calls, [])
        tool_messages = [m for m in llm.calls[1]["messages"] if m.role == "tool"]
        self.assertIn("unknown tool", tool_messages[0].content)

    async def test_invalid_arguments_report_an_error_without_crashing_the_loop(self) -> None:
        llm = ScriptedLLM(
            [
                _response(tool_calls=[_tool_call("call-1", arguments="not json")]),
                _response(content="ok"),
            ]
        )
        tool = EchoTool()
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo hi",
            tools=[tool],
            max_tool_calls=5,
        )

        self.assertEqual(result.tool_calls_made, 1)
        self.assertEqual(result.successful_tool_calls, 0)
        self.assertEqual(result.failed_tool_calls, 1)
        self.assertEqual(tool.calls, [])
        tool_messages = [m for m in llm.calls[1]["messages"] if m.role == "tool"]
        self.assertIn("invalid arguments", tool_messages[0].content)

    async def test_classifies_a_mix_of_successful_and_failing_tool_calls(self) -> None:
        """Covers every classification path `_execute` can hit in one loop
        run: an unknown-tool call and an invalid-arguments call (both
        always failures), a `StatusTool` call that reports `status="error"`
        (a failure), a `StatusTool` call that reports `status="ok"` (a
        success), and an `EchoTool` call whose `Output` has no `status`
        field at all (also a success -- nothing signaled a failure)."""

        llm = ScriptedLLM(
            [
                _response(
                    tool_calls=[
                        _tool_call("call-1", name="nope"),
                        _tool_call("call-2", arguments="not json"),
                        _tool_call("call-3", name="status_tool", arguments='{"should_fail": true}'),
                        _tool_call(
                            "call-4", name="status_tool", arguments='{"should_fail": false}'
                        ),
                        _tool_call("call-5"),
                    ]
                ),
                _response(content="done"),
            ]
        )
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="go",
            tools=[EchoTool(), StatusTool()],
            max_tool_calls=10,
        )

        self.assertEqual(result.tool_calls_made, 5)
        self.assertEqual(result.successful_tool_calls, 2)
        self.assertEqual(result.failed_tool_calls, 3)

    async def test_debug_off_emits_no_logs(self) -> None:
        llm = ScriptedLLM(
            [_response(tool_calls=[_tool_call("call-1")]), _response(content="done: hi")]
        )
        service = ToolLoopService(llm)

        with self.assertNoLogs("red.animator"):
            await service.run(
                base_url="https://x",
                api_key="k",
                model="m",
                system_prompt="sys",
                user_input="echo hi",
                tools=[EchoTool()],
                max_tool_calls=5,
                debug=False,
            )

    async def test_debug_on_logs_the_tool_call_and_its_result(self) -> None:
        llm = ScriptedLLM(
            [_response(tool_calls=[_tool_call("call-1")]), _response(content="done: hi")]
        )
        service = ToolLoopService(llm)

        with self.assertLogs("red.animator", level="INFO") as captured:
            await service.run(
                base_url="https://x",
                api_key="k",
                model="m",
                system_prompt="sys",
                user_input="echo hi",
                tools=[EchoTool()],
                max_tool_calls=5,
                debug=True,
            )

        joined = "\n".join(captured.output)
        self.assertIn("echo", joined)
        self.assertIn('"text": "hi"', joined)
        self.assertIn("heard", joined)

    async def test_debug_on_logs_a_final_answer_with_no_tool_calls(self) -> None:
        llm = ScriptedLLM([_response(content="the answer is 42")])
        service = ToolLoopService(llm)

        with self.assertLogs("red.animator", level="INFO") as captured:
            await service.run(
                base_url="https://x",
                api_key="k",
                model="m",
                system_prompt="sys",
                user_input="what is the answer?",
                tools=[EchoTool()],
                max_tool_calls=5,
                debug=True,
            )

        self.assertIn("the answer is 42", "\n".join(captured.output))


class TestOnActivity(unittest.IsolatedAsyncioTestCase):
    """on_activity reports each tool call and each "thinking" turn (a
    tool-calling turn's own text content) -- see
    docs/corridor-pubsub-design.md's animator AgentReplied mapping."""

    async def test_reports_a_tool_call(self) -> None:
        llm = ScriptedLLM(
            [_response(tool_calls=[_tool_call("call-1")]), _response(content="done: hi")]
        )
        service = ToolLoopService(llm)
        activity: list[str] = []

        async def record(summary: str) -> None:
            activity.append(summary)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo hi",
            tools=[EchoTool()],
            max_tool_calls=5,
            on_activity=record,
        )

        self.assertEqual(activity, ["using tool echo"])

    async def test_reports_thinking_content_alongside_a_tool_call(self) -> None:
        llm = ScriptedLLM(
            [
                _response(content="let me check that", tool_calls=[_tool_call("call-1")]),
                _response(content="done: hi"),
            ]
        )
        service = ToolLoopService(llm)
        activity: list[str] = []

        async def record(summary: str) -> None:
            activity.append(summary)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo hi",
            tools=[EchoTool()],
            max_tool_calls=5,
            on_activity=record,
        )

        self.assertEqual(activity, ["thinking: let me check that", "using tool echo"])

    async def test_no_activity_reported_for_a_final_answer_with_no_tool_calls(self) -> None:
        llm = ScriptedLLM([_response(content="the answer is 42")])
        service = ToolLoopService(llm)
        activity: list[str] = []

        async def record(summary: str) -> None:
            activity.append(summary)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="what is the answer?",
            tools=[EchoTool()],
            max_tool_calls=5,
            on_activity=record,
        )

        self.assertEqual(activity, [])

    async def test_omitting_on_activity_does_not_raise(self) -> None:
        llm = ScriptedLLM(
            [_response(tool_calls=[_tool_call("call-1")]), _response(content="done: hi")]
        )
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo hi",
            tools=[EchoTool()],
            max_tool_calls=5,
        )

        self.assertEqual(result.stopped_reason, "final_text")


class TestOnDebugEvent(unittest.IsolatedAsyncioTestCase):
    """`on_debug_event` is a separate sink from `on_activity` -- richer text
    (full thinking prose, tool name + raw arguments, then result/error) and
    only ever fires when `debug=True`. See tool_loop_service.py's own
    docstring on why the two must not be conflated."""

    async def test_reports_thinking_call_and_result_when_debug_is_on(self) -> None:
        llm = ScriptedLLM(
            [
                _response(content="let me check that", tool_calls=[_tool_call("call-1")]),
                _response(content="done: hi"),
            ]
        )
        service = ToolLoopService(llm)
        events: list[str] = []

        async def record(event: str) -> None:
            events.append(event)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo hi",
            tools=[EchoTool()],
            max_tool_calls=5,
            debug=True,
            on_debug_event=record,
        )

        self.assertEqual(events[0], "thinking: let me check that")
        self.assertEqual(events[1], 'calling echo({"text": "hi"})')
        self.assertEqual(events[2], 'echo -> [ok] {"heard":"hi"}')

    async def test_reports_error_status_word_for_a_failed_call(self) -> None:
        llm = ScriptedLLM(
            [_response(tool_calls=[_tool_call("call-1", name="nope")]), _response(content="ok")]
        )
        service = ToolLoopService(llm)
        events: list[str] = []

        async def record(event: str) -> None:
            events.append(event)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo hi",
            tools=[EchoTool()],
            max_tool_calls=5,
            debug=True,
            on_debug_event=record,
        )

        self.assertIn("nope -> [error]", events[-1])

    async def test_reports_when_max_tool_calls_is_hit(self) -> None:
        llm = ScriptedLLM(
            [
                _response(tool_calls=[_tool_call("call-1")]),
                _response(tool_calls=[_tool_call("call-2")]),
            ]
        )
        service = ToolLoopService(llm)
        events: list[str] = []

        async def record(event: str) -> None:
            events.append(event)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo hi",
            tools=[EchoTool()],
            max_tool_calls=1,
            debug=True,
            on_debug_event=record,
        )

        self.assertIn("stopping: hit max_tool_calls (1)", events[-1])

    async def test_no_debug_events_when_debug_is_off(self) -> None:
        llm = ScriptedLLM(
            [
                _response(content="let me check that", tool_calls=[_tool_call("call-1")]),
                _response(content="done: hi"),
            ]
        )
        service = ToolLoopService(llm)
        events: list[str] = []

        async def record(event: str) -> None:
            events.append(event)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo hi",
            tools=[EchoTool()],
            max_tool_calls=5,
            debug=False,
            on_debug_event=record,
        )

        self.assertEqual(events, [])

    async def test_on_activity_is_unaffected_by_on_debug_event(self) -> None:
        """Both sinks can be wired at once and each gets its own shape of
        text -- on_activity's coarse summaries are untouched by
        on_debug_event's richer ones."""

        llm = ScriptedLLM(
            [
                _response(content="let me check that", tool_calls=[_tool_call("call-1")]),
                _response(content="done: hi"),
            ]
        )
        service = ToolLoopService(llm)
        activity: list[str] = []
        debug_events: list[str] = []

        async def record_activity(summary: str) -> None:
            activity.append(summary)

        async def record_debug(event: str) -> None:
            debug_events.append(event)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo hi",
            tools=[EchoTool()],
            max_tool_calls=5,
            debug=True,
            on_activity=record_activity,
            on_debug_event=record_debug,
        )

        self.assertEqual(activity, ["thinking: let me check that", "using tool echo"])
        self.assertEqual(
            debug_events,
            [
                "thinking: let me check that",
                'calling echo({"text": "hi"})',
                'echo -> [ok] {"heard":"hi"}',
            ],
        )

    async def test_omitting_on_debug_event_does_not_raise(self) -> None:
        llm = ScriptedLLM(
            [_response(tool_calls=[_tool_call("call-1")]), _response(content="done: hi")]
        )
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo hi",
            tools=[EchoTool()],
            max_tool_calls=5,
            debug=True,
        )

        self.assertEqual(result.stopped_reason, "final_text")


class TestAttachments(unittest.IsolatedAsyncioTestCase):
    """`ToolLoopResult.attachments` accumulates whatever a tool's `Output`
    carried in its own `attachments` field -- animator's own extension over
    architect's/painter's identical loop shape, see this module's own
    docstring."""

    async def test_no_attachments_by_default(self) -> None:
        llm = ScriptedLLM([_response(content="the answer is 42")])
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="hi",
            tools=[EchoTool()],
            max_tool_calls=5,
        )

        self.assertEqual(result.attachments, ())

    async def test_collects_an_attachment_from_a_tool_call(self) -> None:
        llm = ScriptedLLM(
            [
                _response(tool_calls=[_tool_call("call-1", name="deliver", arguments="{}")]),
                _response(content="delivered"),
            ]
        )
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="deliver it",
            tools=[AttachingTool()],
            max_tool_calls=5,
        )

        self.assertEqual(result.stopped_reason, "final_text")
        self.assertEqual(len(result.attachments), 1)
        self.assertEqual(result.attachments[0].filename, "preview.png")
        self.assertEqual(result.attachments[0].data, b"\x89PNG")

    async def test_attachments_are_never_serialized_into_the_tool_result_text(self) -> None:
        llm = ScriptedLLM(
            [
                _response(tool_calls=[_tool_call("call-1", name="deliver", arguments="{}")]),
                _response(content="delivered"),
            ]
        )
        service = ToolLoopService(llm)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="deliver it",
            tools=[AttachingTool()],
            max_tool_calls=5,
        )

        tool_messages = [m for m in llm.calls[1]["messages"] if m.role == "tool"]
        self.assertNotIn("preview.png", tool_messages[0].content)
        self.assertNotIn("PNG", tool_messages[0].content)

    async def test_attachments_survive_a_later_max_tool_calls_stop(self) -> None:
        llm = ScriptedLLM(
            [
                _response(tool_calls=[_tool_call("call-1", name="deliver", arguments="{}")]),
                _response(tool_calls=[_tool_call("call-2", name="deliver", arguments="{}")]),
            ]
        )
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="deliver it",
            tools=[AttachingTool()],
            max_tool_calls=2,
        )

        self.assertEqual(result.stopped_reason, "max_tool_calls")
        self.assertEqual(len(result.attachments), 2)

    async def test_empty_final_text_with_attachments_gets_a_fallback_caption(self) -> None:
        """Regression test for a real incident: the model stopped with
        `content=None` and no further tool calls right after
        deliver_pixel_agents_assets succeeded -- corridor's
        GenericAgentExecutor treats `final_text` with `text is None` as a
        hard failure, which would have discarded the already-staged
        attachments entirely."""

        llm = ScriptedLLM(
            [
                _response(tool_calls=[_tool_call("call-1", name="deliver", arguments="{}")]),
                _response(content=None),
            ]
        )
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="deliver it",
            tools=[AttachingTool()],
            max_tool_calls=5,
        )

        self.assertEqual(result.stopped_reason, "final_text")
        self.assertIsNotNone(result.text)
        self.assertTrue(result.text)
        self.assertEqual(len(result.attachments), 1)

    async def test_empty_final_text_with_no_attachments_stays_none(self) -> None:
        """No fallback substitution when there's nothing to salvage --
        `_run_turn`'s existing failure path is still the right outcome for
        a genuinely empty answer with no attachments."""

        llm = ScriptedLLM([_response(content=None)])
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="hi",
            tools=[EchoTool()],
            max_tool_calls=5,
        )

        self.assertEqual(result.stopped_reason, "final_text")
        self.assertIsNone(result.text)


class TestGetJobTrimming(unittest.IsolatedAsyncioTestCase):
    """Regression coverage for a real incident: an animated render's
    repeated get_job polls each carried that job's full (verbose Blender)
    log, and every earlier poll stayed in the turn's own chat history
    forever -- eventually exceeding the model's context window. Only the
    newest poll for a given job_id should still carry its real content by
    the time the model sees it."""

    async def test_an_earlier_poll_of_the_same_job_is_replaced_with_a_placeholder(self) -> None:
        llm = ScriptedLLM(
            [
                _response(
                    tool_calls=[_tool_call("call-1", name="get_job", arguments='{"job_id": "j1"}')]
                ),
                _response(
                    tool_calls=[_tool_call("call-2", name="get_job", arguments='{"job_id": "j1"}')]
                ),
                _response(content="done"),
            ]
        )
        service = ToolLoopService(llm)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="poll it",
            tools=[GetJobTool()],
            max_tool_calls=5,
        )

        # The 3rd LLM call is the one that finally sees both tool results.
        tool_messages = [m for m in llm.calls[2]["messages"] if m.role == "tool"]
        self.assertEqual(len(tool_messages), 2)
        self.assertIn("superseded", tool_messages[0].content)
        self.assertIn('"poll":2', tool_messages[1].content)

    async def test_polls_of_different_job_ids_are_not_trimmed(self) -> None:
        llm = ScriptedLLM(
            [
                _response(
                    tool_calls=[_tool_call("call-1", name="get_job", arguments='{"job_id": "j1"}')]
                ),
                _response(
                    tool_calls=[_tool_call("call-2", name="get_job", arguments='{"job_id": "j2"}')]
                ),
                _response(content="done"),
            ]
        )
        service = ToolLoopService(llm)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="poll both",
            tools=[GetJobTool()],
            max_tool_calls=5,
        )

        tool_messages = [m for m in llm.calls[2]["messages"] if m.role == "tool"]
        self.assertEqual(len(tool_messages), 2)
        self.assertNotIn("superseded", tool_messages[0].content)
        self.assertNotIn("superseded", tool_messages[1].content)

    async def test_a_third_poll_supersedes_the_second_not_just_the_first(self) -> None:
        llm = ScriptedLLM(
            [
                _response(
                    tool_calls=[_tool_call("call-1", name="get_job", arguments='{"job_id": "j1"}')]
                ),
                _response(
                    tool_calls=[_tool_call("call-2", name="get_job", arguments='{"job_id": "j1"}')]
                ),
                _response(
                    tool_calls=[_tool_call("call-3", name="get_job", arguments='{"job_id": "j1"}')]
                ),
                _response(content="done"),
            ]
        )
        service = ToolLoopService(llm)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="poll it",
            tools=[GetJobTool()],
            max_tool_calls=5,
        )

        tool_messages = [m for m in llm.calls[3]["messages"] if m.role == "tool"]
        self.assertIn("superseded", tool_messages[0].content)
        self.assertIn("superseded", tool_messages[1].content)
        self.assertIn('"poll":3', tool_messages[2].content)

    async def test_malformed_get_job_arguments_do_not_crash_the_loop(self) -> None:
        llm = ScriptedLLM(
            [
                _response(tool_calls=[_tool_call("call-1", name="get_job", arguments="not json")]),
                _response(content="done"),
            ]
        )
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="poll it",
            tools=[GetJobTool()],
            max_tool_calls=5,
        )

        self.assertEqual(result.stopped_reason, "final_text")
        self.assertEqual(result.text, "done")

    async def test_a_non_get_job_tool_with_a_job_id_argument_is_never_trimmed(self) -> None:
        """Trimming is scoped to the tool named `get_job` specifically, not
        any tool call that happens to carry a `job_id` argument."""

        llm = ScriptedLLM(
            [
                _response(
                    tool_calls=[_tool_call("call-1", name="echo", arguments='{"text": "hi"}')]
                ),
                _response(
                    tool_calls=[_tool_call("call-2", name="echo", arguments='{"text": "hi"}')]
                ),
                _response(content="done"),
            ]
        )
        service = ToolLoopService(llm)

        await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            user_input="echo twice",
            tools=[EchoTool()],
            max_tool_calls=5,
        )

        tool_messages = [m for m in llm.calls[2]["messages"] if m.role == "tool"]
        self.assertNotIn("superseded", tool_messages[0].content)
        self.assertNotIn("superseded", tool_messages[1].content)
