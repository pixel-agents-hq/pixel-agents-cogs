"""ToolLoopService: a fake LiteLLMClient double returns scripted tool_calls
sequences. Asserts the loop stops at max_tool_calls, stops when no
tool_calls come back, and that raw assistant content never reaches
anywhere but the LLM's own next-call message history."""

from __future__ import annotations

import unittest
from typing import Any

from pydantic import BaseModel

from ..application.tool_loop_service import ToolLoopService
from ..domain import ConversationContext, MessageSnapshot
from ..infrastructure.llm_client import (
    ChatCompletionChoice,
    ChatCompletionResponse,
    ChatCompletionResponseMessage,
    LLMRequestError,
    ToolCall,
    ToolCallFunction,
)


def _snapshot(content: str = "hi") -> MessageSnapshot:
    return MessageSnapshot(
        guild_id=1,
        channel_id=2,
        message_id=3,
        author_id=4,
        author_is_bot=False,
        content=content,
        mentions_bot=True,
        is_reply_to_bot=False,
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
    async def test_stops_when_no_tool_calls_come_back(self) -> None:
        llm = ScriptedLLM([_response(content="just chatting, no tool needed")])
        tool = EchoTool()
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            context=ConversationContext(trigger=_snapshot()),
            tools=[tool],
            max_tool_calls=5,
        )

        self.assertEqual(result.stopped_reason, "no_tool_calls")
        self.assertEqual(result.tool_calls_made, 0)
        self.assertEqual(tool.calls, [])
        self.assertEqual(len(llm.calls), 1)

    async def test_executes_a_tool_call_and_continues_the_loop(self) -> None:
        llm = ScriptedLLM([_response(tool_calls=[_tool_call("call-1")]), _response(content="done")])
        tool = EchoTool()
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            context=ConversationContext(trigger=_snapshot()),
            tools=[tool],
            max_tool_calls=5,
        )

        self.assertEqual(result.stopped_reason, "no_tool_calls")
        self.assertEqual(result.tool_calls_made, 1)
        self.assertEqual(len(tool.calls), 1)
        self.assertEqual(tool.calls[0].text, "hi")

    async def test_stops_at_max_tool_calls(self) -> None:
        llm = ScriptedLLM(
            [
                _response(tool_calls=[_tool_call("call-1")]),
                _response(tool_calls=[_tool_call("call-2")]),
                _response(tool_calls=[_tool_call("call-3")]),
            ]
        )
        tool = EchoTool()
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            context=ConversationContext(trigger=_snapshot()),
            tools=[tool],
            max_tool_calls=2,
        )

        self.assertEqual(result.stopped_reason, "max_tool_calls")
        self.assertEqual(result.tool_calls_made, 2)
        self.assertEqual(len(tool.calls), 2)
        # Only 2 LLM calls were needed to make 2 tool calls -- the loop must
        # not make a third LLM call once the budget is already exhausted.
        self.assertEqual(len(llm.calls), 2)

    async def test_a_single_response_over_budget_stops_mid_batch(self) -> None:
        llm = ScriptedLLM(
            [
                _response(
                    tool_calls=[_tool_call("call-1"), _tool_call("call-2"), _tool_call("call-3")]
                )
            ]
        )
        tool = EchoTool()
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            context=ConversationContext(trigger=_snapshot()),
            tools=[tool],
            max_tool_calls=2,
        )

        self.assertEqual(result.stopped_reason, "max_tool_calls")
        self.assertEqual(result.tool_calls_made, 2)
        self.assertEqual(len(tool.calls), 2)

    async def test_llm_failure_stops_the_loop(self) -> None:
        llm = ScriptedLLM([LLMRequestError("boom")])
        tool = EchoTool()
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            context=ConversationContext(trigger=_snapshot()),
            tools=[tool],
            max_tool_calls=5,
        )

        self.assertEqual(result.stopped_reason, "llm_error")
        self.assertEqual(result.tool_calls_made, 0)

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
            context=ConversationContext(trigger=_snapshot()),
            tools=[tool],
            max_tool_calls=5,
        )

        self.assertEqual(result.tool_calls_made, 1)
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
            context=ConversationContext(trigger=_snapshot()),
            tools=[tool],
            max_tool_calls=5,
        )

        self.assertEqual(result.tool_calls_made, 1)
        self.assertEqual(tool.calls, [])
        tool_messages = [m for m in llm.calls[1]["messages"] if m.role == "tool"]
        self.assertIn("invalid arguments", tool_messages[0].content)

    async def test_raw_assistant_content_is_never_surfaced_outside_llm_history(self) -> None:
        """The only egress to Discord is a tool's own handler -- raw
        assistant `content` alongside a tool call must never reach
        anything but the next LLM call's `messages`."""

        llm = ScriptedLLM(
            [
                _response(content="I'll echo that for you", tool_calls=[_tool_call("call-1")]),
                _response(content="all done"),
            ]
        )
        tool = EchoTool()
        service = ToolLoopService(llm)

        result = await service.run(
            base_url="https://x",
            api_key="k",
            model="m",
            system_prompt="sys",
            context=ConversationContext(trigger=_snapshot()),
            tools=[tool],
            max_tool_calls=5,
        )

        self.assertEqual(result.tool_calls_made, 1)
        self.assertFalse(hasattr(result, "content"))
        second_call_messages = llm.calls[1]["messages"]
        assistant_messages = [m for m in second_call_messages if m.role == "assistant"]
        self.assertEqual(len(assistant_messages), 1)
        self.assertEqual(assistant_messages[0].content, "I'll echo that for you")
