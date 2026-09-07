"""ToolLoopService: animator's bounded tool-calling loop.

A deliberate parallel copy of `painter/application/tool_loop_service.py` --
see docs/architect-design.md §8 on duplicating this shape per agent rather
than factoring it into a shared library.

Animator has no other output channel at all -- its final plain-text
assistant reply *is* the result handed back over A2A, same as
architect/painter. This loop keeps calling tools for as long as the model
requests them, and stops as soon as the model returns a turn with no tool
calls, treating that turn's `content` as the finished answer.

Unlike architect/painter, this copy also accumulates `Attachment`s: any
tool call whose `Output` carries a non-empty `attachments` field (currently
only `DeliverPixelAgentsAssetsTool`, see `tools/deliver_assets_tool.py`) has
those attachments collected into the final `ToolLoopResult`, which
`corridor.domain.agent_executor.GenericAgentExecutor._run_turn` reads via
`getattr(result, "attachments", ())` and turns into extra `Part(raw=...)`
entries on the A2A response."""

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

from ..tools.attachment import Attachment
from ..tools.base import ToolSpec

log = logging.getLogger("red.animator")

# Shown in place of a superseded get_job poll's own result -- see
# `run()`'s own handling of `_last_get_job_message_index` below. Kept short
# and plain, matching every other non-JSON tool-error string `_execute`
# already returns (e.g. "Error: unknown tool ...").
_SUPERSEDED_GET_JOB_PLACEHOLDER = (
    "(superseded by a later get_job poll for this job_id -- see the most recent one instead)"
)


class ToolLLM(Protocol):
    """The slice of LiteLLMClient (via CorridorLLMClient) this service
    depends on -- always sends `tools`, mirrors architect's/painter's own
    ToolLLM."""

    async def complete(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpecWire],
        tool_choice: str,
        timeout_seconds: float | None = None,
    ) -> ChatCompletionResponse: ...


@dataclass(frozen=True, slots=True)
class ToolLoopResult:
    tool_calls_made: int
    stopped_reason: str  # "final_text" | "max_tool_calls" | "llm_error"
    text: str | None
    successful_tool_calls: int
    failed_tool_calls: int
    attachments: tuple[Attachment, ...] = ()


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
        request_timeout_seconds: float | None = None,
    ) -> ToolLoopResult:
        """`request_timeout_seconds`, when given, overrides the shared LLM
        connection's own default total-request timeout for every call this
        run makes -- always `None` today (animator has no per-agent
        settings surface of its own; this parameter exists only so
        `GlobalSettings` keeps satisfying corridor's shared
        `SupportsAgentSettings` protocol).

        `on_activity`, if given, is awaited once per "thinking" turn (the
        model's own text alongside a tool-calling turn) and once per tool
        call. `on_debug_event` is a separate, independent sink -- only
        ever awaited when `debug` is True, carrying full detail rather
        than `on_activity`'s coarse summaries."""

        tools_by_name = {tool.name: tool for tool in tools}
        wire_tools = [_wire_spec(tool) for tool in tools]
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_input),
        ]
        calls_made = 0
        successful_calls = 0
        failed_calls = 0
        attachments: list[Attachment] = []
        # job_id -> index into `messages` of that job's most recent get_job
        # tool-result message -- see the trimming step inside the tool-call
        # loop below. A real incident: repeated get_job polls against one
        # long-running animated render each carried that job's full
        # (verbose Blender) log, and every earlier poll stayed in this
        # turn's own history forever, multiplying an already-large payload.
        # Only the newest poll for a given job is ever useful to the model
        # -- earlier ones are collapsed to a short placeholder in place,
        # not removed outright (removing a tool-role message entirely would
        # leave its tool_call_id dangling against the assistant turn that
        # requested it, which some providers reject).
        last_get_job_message_index: dict[str, int] = {}

        while True:
            if calls_made >= max_tool_calls:
                log.warning("animator: tool loop hit max_tool_calls (%d), stopping", max_tool_calls)
                if debug and on_debug_event is not None:
                    await on_debug_event(f"stopping: hit max_tool_calls ({max_tool_calls})")
                return ToolLoopResult(
                    calls_made,
                    "max_tool_calls",
                    None,
                    successful_calls,
                    failed_calls,
                    tuple(attachments),
                )

            try:
                response = await self._llm.complete(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    messages=messages,
                    tools=wire_tools,
                    tool_choice="auto",
                    timeout_seconds=request_timeout_seconds,
                )
            except LLMRequestError as exc:
                log.warning("animator: tool loop LLM call failed, stopping: %s", exc)
                return ToolLoopResult(
                    calls_made,
                    "llm_error",
                    None,
                    successful_calls,
                    failed_calls,
                    tuple(attachments),
                )

            if not response.choices:
                return ToolLoopResult(
                    calls_made,
                    "llm_error",
                    None,
                    successful_calls,
                    failed_calls,
                    tuple(attachments),
                )

            choice_message = response.choices[0].message
            tool_calls = choice_message.tool_calls or []
            if not tool_calls:
                if debug:
                    log.info(
                        "animator: final answer with no tool calls this turn: %r",
                        choice_message.content,
                    )
                final_text = choice_message.content
                if not final_text and attachments:
                    # A real incident: the model stopped with an empty
                    # message right after a successful
                    # deliver_pixel_agents_assets call, despite the system
                    # prompt instructing it to always send a wrap-up reply
                    # -- corridor's GenericAgentExecutor treats
                    # stopped_reason="final_text" with `text is None` as a
                    # hard task failure (_run_turn's own check), which then
                    # discarded already-successfully-staged attachments and
                    # surfaced as a confusing consult_animator failure to
                    # pico. Model prompt-following is inherently
                    # unreliable, so this substitutes a fallback text
                    # rather than relying on the prompt alone -- the files
                    # were genuinely delivered; failing the whole turn over
                    # missing prose would be strictly worse than a terse
                    # default caption.
                    log.warning(
                        "animator: model returned no final text despite delivering "
                        "%d attachment(s); substituting a fallback caption",
                        len(attachments),
                    )
                    final_text = "Done -- attached the requested file(s)."
                return ToolLoopResult(
                    calls_made,
                    "final_text",
                    final_text,
                    successful_calls,
                    failed_calls,
                    tuple(attachments),
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
                        "animator: tool loop hit max_tool_calls (%d), stopping", max_tool_calls
                    )
                    if debug and on_debug_event is not None:
                        await on_debug_event(f"stopping: hit max_tool_calls ({max_tool_calls})")
                    return ToolLoopResult(
                        calls_made,
                        "max_tool_calls",
                        None,
                        successful_calls,
                        failed_calls,
                        tuple(attachments),
                    )
                if on_activity is not None:
                    await on_activity(f"using tool {call.function.name}")
                if debug and on_debug_event is not None:
                    await on_debug_event(f"calling {call.function.name}({call.function.arguments})")
                result_text, succeeded, call_attachments = await _execute(
                    tools_by_name, call, debug=debug
                )
                attachments.extend(call_attachments)
                if debug and on_debug_event is not None:
                    status_word = "ok" if succeeded else "error"
                    await on_debug_event(f"{call.function.name} -> [{status_word}] {result_text}")
                messages.append(ChatMessage(role="tool", tool_call_id=call.id, content=result_text))
                if call.function.name == "get_job":
                    job_id = _extract_job_id(call.function.arguments)
                    if job_id is not None:
                        previous_index = last_get_job_message_index.get(job_id)
                        if previous_index is not None:
                            messages[previous_index].content = _SUPERSEDED_GET_JOB_PLACEHOLDER
                        last_get_job_message_index[job_id] = len(messages) - 1
                calls_made += 1
                if succeeded:
                    successful_calls += 1
                else:
                    failed_calls += 1


def _extract_job_id(raw_arguments: str) -> str | None:
    """Best-effort: `raw_arguments` is the model's own raw JSON-arguments
    string for a `get_job` call (`{"job_id": "..."}`) -- malformed or
    missing is a normal case here, not an error worth logging, since this
    is only used to decide whether an earlier poll can be trimmed, never
    to actually call the tool (that already happens, and already reports
    its own error, in `_execute`)."""

    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    job_id = parsed.get("job_id")
    return job_id if isinstance(job_id, str) else None


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
) -> tuple[str, bool, tuple[Attachment, ...]]:
    """Returns the tool-role message content, whether the call counts as
    successful, and any `Attachment`s the tool's `Output` carried (empty
    for every tool except `DeliverPixelAgentsAssetsTool` today). A missing
    tool or invalid arguments are always a failure; a resolved call's
    outcome follows every real tool's `Output` convention
    (`status: Literal["ok", "error"]`) -- an `Output` with no `status`
    field at all is treated as successful, since nothing signaled a
    failure."""

    if debug:
        log.info("animator: tool call %s(%s)", call.function.name, call.function.arguments)
    tool = tools_by_name.get(call.function.name)
    if tool is None:
        if debug:
            log.info("animator: tool %s does not exist", call.function.name)
        return f"Error: unknown tool {call.function.name!r}", False, ()
    try:
        raw_args = json.loads(call.function.arguments)
        parsed_input = tool.Input.model_validate(raw_args)
    except (json.JSONDecodeError, ValidationError) as exc:
        if debug:
            log.info("animator: tool %s got invalid arguments: %s", call.function.name, exc)
        return f"Error: invalid arguments for {call.function.name}: {exc}", False, ()
    output = await tool.handler(parsed_input)
    result_text = output.model_dump_json()
    if debug:
        log.info("animator: tool %s returned %s", call.function.name, result_text)
    attachments: tuple[Attachment, ...] = tuple(getattr(output, "attachments", None) or ())
    return result_text, getattr(output, "status", None) != "error", attachments


__all__ = ["ToolLLM", "ToolLoopResult", "ToolLoopService"]
