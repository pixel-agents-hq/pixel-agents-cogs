"""ToolLoopService: a fake LiteLLMClient double returns scripted tool_calls
sequences. Unlike pico's loop (which never returns raw text), architect's
loop treats the model's final no-tool-calls turn as the answer -- see
`application/tool_loop_service.py`'s module docstring."""

from __future__ import annotations

import unittest
from typing import Any

from pydantic import BaseModel

from corridor.infrastructure.llm_client import (
    ChatCompletionChoice,
    ChatCompletionResponse,
    ChatCompletionResponseMessage,
    LLMRequestError,
    ToolCall,
    ToolCallFunction,
)

from ..application.tool_loop_service import ToolLoopService


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
    architect tool's `Output` follows (see office_tools.py) -- unlike
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

        with self.assertNoLogs("red.architect"):
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

        with self.assertLogs("red.architect", level="INFO") as captured:
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

        with self.assertLogs("red.architect", level="INFO") as captured:
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
    docs/corridor-pubsub-design.md's architect AgentReplied mapping."""

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
