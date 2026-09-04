"""Shared A2A `AgentExecutor` scaffolding for architect and painter.

Both cogs' own `infrastructure/a2a_server.py` used to carry a byte-identical
~130-line `AgentExecutor` subclass (`execute`/`_run_turn`/`_fail_safely`/
`cancel`) and an identical-shaped `build_agent_card` -- differing only in
agent-name strings. This module is the one copy; each cog keeps a thin
subclass fixing its own `agent_name`/`logger` (see `architect/infrastructure/
a2a_server.py`/`painter/infrastructure/a2a_server.py`).

a2a-sdk is corridor's one named domain exception (see
`corridor/domain/agent_directory.py`'s own docstring) -- this module follows
the same precedent rather than pushing framework imports out of domain/.

Deliberately does not import architect's or painter's own `ToolSpec`/
`ToolLoopService`/`GlobalSettings` types (corridor must never depend on a
consuming cog): the structural Protocols below are the minimal slice this
module actually touches, satisfied by either cog's own concrete types
without either needing to know this module exists.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils import TransportProtocol
from a2a.utils.errors import UnsupportedOperationError

from .models import LLMSettings


class ToolSpecLike(Protocol):
    """All `build_agent_card` touches -- satisfied by either cog's own
    `tools.base.ToolSpec` without importing it. Read-only properties, not
    plain attributes -- mypy treats a Protocol's plain attribute
    declarations as settable, which a frozen dataclass's own attributes
    (e.g. neither cog's `ToolSpec`s are frozen, but `ToolLoopResult`/
    `GlobalSettings` below are) can't satisfy; a property's type is
    checked covariantly instead. See `pico/tools/base.py::ToolSpec`'s own
    comment for the precedent this follows."""

    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...


class ToolLoopResult(Protocol):
    @property
    def stopped_reason(self) -> str: ...
    @property
    def text(self) -> str | None: ...
    @property
    def tool_calls_made(self) -> int: ...
    @property
    def successful_tool_calls(self) -> int: ...
    @property
    def failed_tool_calls(self) -> int: ...


class SupportsToolLoop(Protocol):
    async def run(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str,
        user_input: str,
        tools: Sequence[Any],
        max_tool_calls: int,
        debug: bool,
        on_activity: Callable[[str], Awaitable[None]] | None,
        on_debug_event: Callable[[str], Awaitable[None]],
        request_timeout_seconds: float | None,
    ) -> ToolLoopResult: ...


class SupportsAgentSettings(Protocol):
    @property
    def system_prompt(self) -> str: ...
    @property
    def max_tool_calls(self) -> int: ...
    @property
    def debug_logging(self) -> bool: ...
    @property
    def request_timeout_seconds(self) -> float | None: ...


def build_agent_card(
    *,
    name: str,
    description: str,
    version: str,
    tools: Sequence[ToolSpecLike],
    tag: str,
) -> AgentCard:
    """One skill per tool the agent currently offers. The URL is a
    placeholder -- `corridor.register_agent` overwrites it before storing
    this card, since the registering agent has no way to know its own
    eventual mount path."""

    skills = [
        AgentSkill(id=tool.name, name=tool.name, description=tool.description, tags=[tag])
        for tool in tools
    ] or [
        AgentSkill(
            id="chat",
            name="chat",
            description="General-purpose delegated task, answered as plain text.",
            tags=[tag],
        )
    ]
    return AgentCard(
        name=name,
        description=description,
        version=version,
        supported_interfaces=[
            AgentInterface(
                url="http://placeholder/", protocol_binding=TransportProtocol.JSONRPC.value
            )
        ],
        # streaming=True: required for a caller (pico) to receive the
        # intermediate TASK_STATE_WORKING status updates execute() emits
        # below when debug_logging is on -- a2a-sdk's own client falls back
        # to a single aggregated final response otherwise (verified against
        # the installed a2a-sdk: BaseClient.send_message only calls the
        # streaming transport when both the client's ClientConfig.streaming
        # and this card's capabilities.streaming are True).
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=skills,
    )


class GenericAgentExecutor(AgentExecutor):
    """Bridges one inbound A2A message to the owning cog's own bounded
    tool loop, using the corridor-shared LLM connection. `agent_name` (e.g.
    `"Architect"`) drives every user-facing/log string that used to be
    hand-copied per cog; `logger` stays each cog's own
    `logging.getLogger("red.<cog>")` rather than this module inventing a
    name. `context.get_user_input()` (the inbound message's text parts,
    joined) becomes the tool loop's one user turn -- there is no persisted
    multi-turn conversation."""

    def __init__(
        self,
        *,
        agent_name: str,
        logger: logging.Logger,
        tool_loop: SupportsToolLoop,
        tools: Sequence[object],
        settings: Callable[[], Awaitable[SupportsAgentSettings]],
        llm_settings: Callable[[], Awaitable[LLMSettings]],
        publish_activity: Callable[[str], Awaitable[None]] | None = None,
        mcp_tools: Callable[[], Awaitable[Sequence[object]]] | None = None,
    ) -> None:
        self._agent_name = agent_name
        self._log = logger
        self._tool_loop = tool_loop
        self._tools = tools
        self._settings = settings
        self._llm_settings = llm_settings
        self._publish_activity = publish_activity
        # Fetched fresh every turn, not cached -- corridor's registered MCP
        # tool servers (suggestionbox) are gated by a live, owner-toggleable
        # per-agent Components V2 panel; re-fetching here means a toggle
        # flip takes effect on the agent's very next A2A message, no cog
        # reload required. `None` (the default) means no corridor bridge is
        # wired up at all -- every existing caller that never passes this
        # keeps working unchanged. See docs/suggestionbox-design.md §6.
        self._mcp_tools = mcp_tools

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or uuid.uuid4().hex
        context_id = context.context_id or uuid.uuid4().hex
        # The framework requires a Task to be enqueued before any
        # TaskStatusUpdateEvent for it -- see AgentExecutor.execute's own
        # docstring ("Allowed Workflows"). TaskUpdater only builds status
        # updates/messages, so this initial Task is built and enqueued by
        # hand, once, before the updater does anything else.
        await event_queue.enqueue_event(
            Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            )
        )
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.start_work()

        try:
            await self._run_turn(context, updater)
        except Exception:
            # Anything past this point streams over a2a-sdk's SSE
            # transport, which silently drops the connection on an
            # uncaught exception -- neither the a2a-sdk layer nor uvicorn
            # logs a traceback for it (verified against the installed
            # a2a-sdk: `on_message_send_stream`'s except only catches
            # `CancelledError`/`GeneratorExit`, `_wrap_stream`'s only
            # catches `A2AError`), so a real bug here previously surfaced
            # to a caller only as an opaque "peer closed connection
            # without sending complete message body" with zero trace in
            # this container's logs. `log.exception` here is the only
            # place this failure mode gets a traceback at all -- keep it
            # noisy. The Discord/A2A-facing message stays generic: no
            # exception text, since that could echo secrets (API keys,
            # internal paths) into a channel or another agent's context.
            self._log.exception(
                "%s: tool loop crashed for task %s", self._agent_name.lower(), task_id
            )
            await self._fail_safely(
                updater,
                f"{self._agent_name} hit an internal error and could not produce an answer "
                f"(task {task_id} -- check the bot's container logs).",
            )

    async def _run_turn(self, context: RequestContext, updater: TaskUpdater) -> None:
        user_input = context.get_user_input()
        llm_settings = await self._llm_settings()
        if not llm_settings.ready:
            await updater.failed(
                updater.new_agent_message(
                    [
                        Part(
                            text=f"{self._agent_name}'s shared LLM connection is not "
                            "configured yet."
                        )
                    ]
                )
            )
            return

        async def _emit_debug(text: str) -> None:
            # Best-effort, same convention CogBase._publish_activity uses
            # for its own pub/sub publish -- a transport hiccup here must
            # never break the tool loop or suppress the final answer.
            try:
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=updater.new_agent_message([Part(text=text)]),
                )
            except Exception:
                self._log.warning(
                    "%s: failed to emit debug status update",
                    self._agent_name.lower(),
                    exc_info=True,
                )

        settings = await self._settings()
        tools = list(self._tools)
        if self._mcp_tools is not None:
            tools.extend(await self._mcp_tools())
        result = await self._tool_loop.run(
            base_url=llm_settings.llm_base_url,
            api_key=llm_settings.llm_api_key or "",
            model=llm_settings.llm_model or "",
            system_prompt=settings.system_prompt,
            user_input=user_input,
            tools=tools,
            max_tool_calls=settings.max_tool_calls,
            debug=settings.debug_logging,
            on_activity=self._publish_activity,
            on_debug_event=_emit_debug,
            request_timeout_seconds=settings.request_timeout_seconds,
        )

        if result.stopped_reason != "final_text" or result.text is None:
            await updater.failed(
                updater.new_agent_message(
                    [
                        Part(
                            text=f"{self._agent_name} could not produce an answer "
                            f"({result.stopped_reason})."
                        )
                    ]
                )
            )
            return

        await updater.complete(
            updater.new_agent_message(
                [Part(text=result.text)],
                metadata={
                    "tool_calls_made": result.tool_calls_made,
                    "successful_tool_calls": result.successful_tool_calls,
                    "failed_tool_calls": result.failed_tool_calls,
                },
            )
        )

    async def _fail_safely(self, updater: TaskUpdater, text: str) -> None:
        """Best-effort: the task may already be in a terminal state (e.g.
        `_run_turn` crashed after its own `updater.failed`/`.complete`
        already ran), and `TaskUpdater.update_status` raises `RuntimeError`
        for that -- this must never mask the original crash with a second,
        unrelated traceback."""

        try:
            await updater.failed(updater.new_agent_message([Part(text=text)]))
        except Exception:
            self._log.warning(
                "%s: could not report task failure back to the caller",
                self._agent_name.lower(),
                exc_info=True,
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise UnsupportedOperationError(
            f"{self._agent_name.lower()} tasks run to completion synchronously and cannot "
            "be cancelled"
        )


__all__ = [
    "GenericAgentExecutor",
    "SupportsAgentSettings",
    "SupportsToolLoop",
    "ToolLoopResult",
    "ToolSpecLike",
    "build_agent_card",
]
