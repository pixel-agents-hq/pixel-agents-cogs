"""ToolLoopService: architect's bounded tool-calling loop.

Unlike pico's loop (which may only ever act via a tool call and never sends
raw LLM text anywhere), architect has no other output channel at all -- its
final plain-text assistant reply *is* the result handed back over A2A. So
this loop keeps calling tools for as long as the model requests them, and
stops as soon as the model returns a turn with no tool calls, treating that
turn's `content` as the finished answer.
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

from ..tools.base import ToolSpec

log = logging.getLogger("red.architect")


class ToolLLM(Protocol):
    """The slice of LiteLLMClient (via CorridorLLMClient) this service
    depends on -- always sends `tools`, mirrors pico's own ToolLLM."""

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
    stopped_reason: str  # "final_text" | "max_tool_calls" | "llm_error"
    text: str | None


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
        user_input: str,
        tools: Sequence[ToolSpec],
        max_tool_calls: int,
        debug: bool = False,
    ) -> ToolLoopResult:
        tools_by_name = {tool.name: tool for tool in tools}
        wire_tools = [_wire_spec(tool) for tool in tools]
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_input),
        ]
        calls_made = 0

        while True:
            if calls_made >= max_tool_calls:
                log.warning(
                    "architect: tool loop hit max_tool_calls (%d), stopping", max_tool_calls
                )
                return ToolLoopResult(calls_made, "max_tool_calls", text=None)

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
                log.warning("architect: tool loop LLM call failed, stopping: %s", exc)
                return ToolLoopResult(calls_made, "llm_error", text=None)

            if not response.choices:
                return ToolLoopResult(calls_made, "llm_error", text=None)

            choice_message = response.choices[0].message
            tool_calls = choice_message.tool_calls or []
            if not tool_calls:
                if debug:
                    log.info(
                        "architect: final answer with no tool calls this turn: %r",
                        choice_message.content,
                    )
                return ToolLoopResult(calls_made, "final_text", text=choice_message.content)

            messages.append(
                ChatMessage(role="assistant", content=choice_message.content, tool_calls=tool_calls)
            )
            for call in tool_calls:
                if calls_made >= max_tool_calls:
                    log.warning(
                        "architect: tool loop hit max_tool_calls (%d), stopping", max_tool_calls
                    )
                    return ToolLoopResult(calls_made, "max_tool_calls", text=None)
                result_text = await _execute(tools_by_name, call, debug=debug)
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


async def _execute(
    tools_by_name: dict[str, ToolSpec], call: ToolCall, *, debug: bool = False
) -> str:
    if debug:
        log.info("architect: tool call %s(%s)", call.function.name, call.function.arguments)
    tool = tools_by_name.get(call.function.name)
    if tool is None:
        if debug:
            log.info("architect: tool %s does not exist", call.function.name)
        return f"Error: unknown tool {call.function.name!r}"
    try:
        raw_args = json.loads(call.function.arguments)
        parsed_input = tool.Input.model_validate(raw_args)
    except (json.JSONDecodeError, ValidationError) as exc:
        if debug:
            log.info("architect: tool %s got invalid arguments: %s", call.function.name, exc)
        return f"Error: invalid arguments for {call.function.name}: {exc}"
    output = await tool.handler(parsed_input)
    result_text = output.model_dump_json()
    if debug:
        log.info("architect: tool %s returned %s", call.function.name, result_text)
    return result_text


__all__ = ["ToolLLM", "ToolLoopResult", "ToolLoopService"]
