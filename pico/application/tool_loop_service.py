"""ToolLoopService: the bounded tool-calling loop.

Once gated in, Pico may only act via tool calls -- never a raw
assistant-text send. Raw `content` returned alongside a tool call is kept
in the LLM's own message history (so it can see what it "said" on the next
turn of the loop) but is never routed anywhere Discord can see it; the
only path to Discord is a tool's own handler (see `tools/reply_tool.py`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from corridor.infrastructure.llm_client import (
    ChatCompletionResponse,
    ChatMessage,
    LLMRequestError,
    ToolCall,
    ToolFunctionSpec,
    ToolSpecWire,
)

from ..domain import ConversationContext
from ..tools.base import ToolSpec

log = logging.getLogger("red.pico")


class ToolLLM(Protocol):
    """The slice of LiteLLMClient this service depends on -- unlike
    GateService's GateLLM Protocol, this one always sends `tools`."""

    async def complete(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpecWire],
        tool_choice: str,
    ) -> ChatCompletionResponse: ...


@dataclass(frozen=True, slots=True)
class ToolLoopResult:
    tool_calls_made: int
    stopped_reason: str  # "no_tool_calls" | "max_tool_calls" | "llm_error"


class ToolLoopService:
    def __init__(self, llm: ToolLLM) -> None:
        self._llm = llm

    async def run(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str,
        context: ConversationContext,
        tools: Sequence[ToolSpec],
        max_tool_calls: int,
    ) -> ToolLoopResult:
        tools_by_name = {tool.name: tool for tool in tools}
        wire_tools = [_wire_spec(tool) for tool in tools]
        messages = _build_turn_messages(context, system_prompt)
        calls_made = 0

        while True:
            if calls_made >= max_tool_calls:
                log.warning("pico: tool loop hit max_tool_calls (%d), stopping", max_tool_calls)
                return ToolLoopResult(calls_made, "max_tool_calls")

            try:
                response = await self._llm.complete(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    messages=messages,
                    tools=wire_tools,
                    tool_choice="auto",
                )
            except LLMRequestError as exc:
                log.warning("pico: tool loop LLM call failed, stopping: %s", exc)
                return ToolLoopResult(calls_made, "llm_error")

            if not response.choices:
                return ToolLoopResult(calls_made, "llm_error")

            choice_message = response.choices[0].message
            tool_calls = choice_message.tool_calls or []
            if not tool_calls:
                return ToolLoopResult(calls_made, "no_tool_calls")

            messages.append(
                ChatMessage(role="assistant", content=choice_message.content, tool_calls=tool_calls)
            )
            for call in tool_calls:
                if calls_made >= max_tool_calls:
                    log.warning("pico: tool loop hit max_tool_calls (%d), stopping", max_tool_calls)
                    return ToolLoopResult(calls_made, "max_tool_calls")
                result_text = await _execute(tools_by_name, call)
                messages.append(ChatMessage(role="tool", tool_call_id=call.id, content=result_text))
                calls_made += 1


def _wire_spec(tool: ToolSpec) -> ToolSpecWire:
    return ToolSpecWire(
        function=ToolFunctionSpec(
            name=tool.name,
            description=tool.description,
            parameters=tool.Input.model_json_schema(),
        )
    )


async def _execute(tools_by_name: dict[str, ToolSpec], call: ToolCall) -> str:
    tool = tools_by_name.get(call.function.name)
    if tool is None:
        return f"Error: unknown tool {call.function.name!r}"
    try:
        raw_args = json.loads(call.function.arguments)
        parsed_input = tool.Input.model_validate(raw_args)
    except (json.JSONDecodeError, ValidationError) as exc:
        return f"Error: invalid arguments for {call.function.name}: {exc}"
    output = await tool.handler(parsed_input)
    return output.model_dump_json()


def _build_turn_messages(context: ConversationContext, system_prompt: str) -> list[ChatMessage]:
    messages = [ChatMessage(role="system", content=system_prompt)]
    messages.extend(
        ChatMessage(
            role="assistant" if entry.author_is_bot else "user",
            content=f"{entry.author_name}: {entry.content}",
        )
        for entry in context.history
    )
    messages.append(ChatMessage(role="user", content=context.trigger.content))
    return messages


__all__ = ["ToolLLM", "ToolLoopResult", "ToolLoopService"]
