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
from collections.abc import Awaitable, Callable, Sequence
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
    successful_tool_calls: int
    failed_tool_calls: int


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
        on_activity: Callable[[str], Awaitable[None]] | None = None,
        on_debug_event: Callable[[str], Awaitable[None]] | None = None,
    ) -> ToolLoopResult:
        """`on_activity`, if given, is awaited once per "thinking" turn (the
        model's own text alongside a tool-calling turn) and once per tool
        call -- corridor's AgentReplied publish, in architect's case (see
        docs/corridor-pubsub-design.md). Optional and defaults to a no-op
        so tests/callers that don't care about activity reporting are
        unaffected.

        `on_debug_event` is a separate, independent sink -- do not conflate
        it with `on_activity` above. It's only ever awaited when `debug` is
        True, and carries the same full detail `debug`'s own `log.info(...)`
        calls already do (full thinking prose, each tool call's name *and*
        raw arguments, then its result or error) rather than `on_activity`'s
        coarse "thinking: ..."/"using tool X" summaries. Intended for
        streaming a live trace of this turn to an operator (e.g. architect's
        A2A executor turning each string into an intermediate task status
        update) -- `on_activity`'s own consumer (Corridor, and therefore CCTV's
        office activity bubble) is unaffected by this parameter."""

        tools_by_name = {tool.name: tool for tool in tools}
        wire_tools = [_wire_spec(tool) for tool in tools]
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_input),
        ]
        calls_made = 0
        successful_calls = 0
        failed_calls = 0

        while True:
            if calls_made >= max_tool_calls:
                log.warning(
                    "architect: tool loop hit max_tool_calls (%d), stopping", max_tool_calls
                )
                if debug and on_debug_event is not None:
                    await on_debug_event(f"stopping: hit max_tool_calls ({max_tool_calls})")
                return ToolLoopResult(
                    calls_made, "max_tool_calls", None, successful_calls, failed_calls
                )

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
                return ToolLoopResult(calls_made, "llm_error", None, successful_calls, failed_calls)

            if not response.choices:
                return ToolLoopResult(calls_made, "llm_error", None, successful_calls, failed_calls)

            choice_message = response.choices[0].message
            tool_calls = choice_message.tool_calls or []
            if not tool_calls:
                if debug:
                    log.info(
                        "architect: final answer with no tool calls this turn: %r",
                        choice_message.content,
                    )
                return ToolLoopResult(
                    calls_made,
                    "final_text",
                    choice_message.content,
                    successful_calls,
                    failed_calls,
                )

            if on_activity is not None and choice_message.content:
                await on_activity(f"thinking: {choice_message.content}")
            if debug and on_debug_event is not None and choice_message.content:
                await on_debug_event(f"thinking: {choice_message.content}")

            messages.append(
                ChatMessage(role="assistant", content=choice_message.content, tool_calls=tool_calls)
            )
            for call in tool_calls:
                if calls_made >= max_tool_calls:
                    log.warning(
                        "architect: tool loop hit max_tool_calls (%d), stopping", max_tool_calls
                    )
                    if debug and on_debug_event is not None:
                        await on_debug_event(f"stopping: hit max_tool_calls ({max_tool_calls})")
                    return ToolLoopResult(
                        calls_made, "max_tool_calls", None, successful_calls, failed_calls
                    )
                if on_activity is not None:
                    await on_activity(f"using tool {call.function.name}")
                if debug and on_debug_event is not None:
                    await on_debug_event(f"calling {call.function.name}({call.function.arguments})")
                result_text, succeeded = await _execute(tools_by_name, call, debug=debug)
                if debug and on_debug_event is not None:
                    status_word = "ok" if succeeded else "error"
                    await on_debug_event(f"{call.function.name} -> [{status_word}] {result_text}")
                messages.append(ChatMessage(role="tool", tool_call_id=call.id, content=result_text))
                calls_made += 1
                if succeeded:
                    successful_calls += 1
                else:
                    failed_calls += 1


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
) -> tuple[str, bool]:
    """Returns the tool-role message content plus whether the call counts
    as successful. A missing tool or invalid arguments are always a
    failure; a resolved call's outcome follows every real tool's `Output`
    convention (`status: Literal["ok", "error"]`, see office_tools.py's
    `_error()`) -- an `Output` with no `status` field at all (not part of
    that convention) is treated as successful, since nothing signaled a
    failure."""

    if debug:
        log.info("architect: tool call %s(%s)", call.function.name, call.function.arguments)
    tool = tools_by_name.get(call.function.name)
    if tool is None:
        if debug:
            log.info("architect: tool %s does not exist", call.function.name)
        return f"Error: unknown tool {call.function.name!r}", False
    try:
        raw_args = json.loads(call.function.arguments)
        parsed_input = tool.Input.model_validate(raw_args)
    except (json.JSONDecodeError, ValidationError) as exc:
        if debug:
            log.info("architect: tool %s got invalid arguments: %s", call.function.name, exc)
        return f"Error: invalid arguments for {call.function.name}: {exc}", False
    output = await tool.handler(parsed_input)
    result_text = output.model_dump_json()
    if debug:
        log.info("architect: tool %s returned %s", call.function.name, result_text)
    return result_text, getattr(output, "status", None) != "error"


__all__ = ["ToolLLM", "ToolLoopResult", "ToolLoopService"]
