"""Painter's A2A surface: agent card + executor. A deliberate parallel
copy of `architect/infrastructure/a2a_server.py`'s shape -- see that
module's own docstring and docs/agent-directory-design.md.

Painter no longer owns an A2A listener of its own (there is no such
listener in this repo for any agent -- see docs/agent-directory-design.md):
the `AgentCard`/`AgentExecutor` built here are handed to
`corridor.register_agent(...)` at `cog_load`, and corridor mounts them on
its own shared listener alongside every other registered agent.
`AgentCard.supported_interfaces[0].url` set here is a placeholder --
corridor overwrites it with its own configured host/port + this agent's
mount path before storing it."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence

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

from corridor.domain import LLMSettings

from ..application import ToolLoopService
from ..domain import GlobalSettings
from ..tools.base import ToolSpec

log = logging.getLogger("red.painter")

AGENT_NAME = "painter"
AGENT_VERSION = "0.1.0"
AGENT_DESCRIPTION = (
    "A second, independent LLM agent reachable only over A2A -- never "
    "Discord-user-facing. Consult it to delegate a color-related sub-task: "
    "recoloring floor tiles, walls, or furniture, or reporting the office's "
    "current colors. It only acts on what the delegated prompt states as "
    "an explicit instruction. It has no memory of past consultations -- "
    "each prompt is answered on its own, so restate any earlier context a "
    "follow-up needs."
    "\n\nIt shares one persistent office layout with Architect: Architect "
    "knows what tiles, walls, and furniture exist and where, but is "
    "colorblind and has no notion of color. Painter is the color "
    "specialist and can read/change color, but can never add, remove, "
    "move, or otherwise restructure anything -- forward structural "
    "requests (adding furniture, resizing zones, etc.) to Architect "
    "instead, not to Painter."
)


def build_agent_card(*, tools: Sequence[ToolSpec]) -> AgentCard:
    """One skill per tool painter currently offers. The URL is a
    placeholder (`corridor.register_agent` overwrites it)."""

    skills = [
        AgentSkill(id=tool.name, name=tool.name, description=tool.description, tags=["painter"])
        for tool in tools
    ] or [
        AgentSkill(
            id="chat",
            name="chat",
            description="General-purpose delegated color task, answered as plain text.",
            tags=["painter"],
        )
    ]
    return AgentCard(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        version=AGENT_VERSION,
        supported_interfaces=[
            AgentInterface(
                url="http://placeholder/", protocol_binding=TransportProtocol.JSONRPC.value
            )
        ],
        # streaming=True: required for a caller (pico) to receive the
        # intermediate TASK_STATE_WORKING status updates execute() emits
        # below when debug_logging is on -- see architect's own
        # build_agent_card for the verified a2a-sdk rationale, unchanged.
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=skills,
    )


class PainterAgentExecutor(AgentExecutor):
    """Bridges one inbound A2A message to painter's own bounded
    `ToolLoopService`, using the same corridor-shared LLM connection pico
    and architect use. `context.get_user_input()` (the inbound message's
    text parts, joined) becomes the tool loop's one user turn -- there is
    no persisted multi-turn conversation, mirroring architect's own
    no-session design."""

    def __init__(
        self,
        *,
        tool_loop: ToolLoopService,
        tools: Sequence[ToolSpec],
        settings: Callable[[], Awaitable[GlobalSettings]],
        llm_settings: Callable[[], Awaitable[LLMSettings]],
        publish_activity: Callable[[str], Awaitable[None]] | None = None,
        mcp_tools: Callable[[], Awaitable[Sequence[ToolSpec]]] | None = None,
    ) -> None:
        self._tool_loop = tool_loop
        self._tools = tools
        self._settings = settings
        self._llm_settings = llm_settings
        self._publish_activity = publish_activity
        self._mcp_tools = mcp_tools

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or uuid.uuid4().hex
        context_id = context.context_id or uuid.uuid4().hex
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
            log.exception("painter: tool loop crashed for task %s", task_id)
            await self._fail_safely(
                updater,
                f"Painter hit an internal error and could not produce an answer "
                f"(task {task_id} -- check the bot's container logs).",
            )

    async def _run_turn(self, context: RequestContext, updater: TaskUpdater) -> None:
        user_input = context.get_user_input()
        llm_settings = await self._llm_settings()
        if not llm_settings.ready:
            await updater.failed(
                updater.new_agent_message(
                    [Part(text="Painter's shared LLM connection is not configured yet.")]
                )
            )
            return

        async def _emit_debug(text: str) -> None:
            try:
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=updater.new_agent_message([Part(text=text)]),
                )
            except Exception:
                log.warning("painter: failed to emit debug status update", exc_info=True)

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
        )

        if result.stopped_reason != "final_text" or result.text is None:
            await updater.failed(
                updater.new_agent_message(
                    [Part(text=f"Painter could not produce an answer ({result.stopped_reason}).")]
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

    @staticmethod
    async def _fail_safely(updater: TaskUpdater, text: str) -> None:
        """Best-effort: the task may already be in a terminal state (e.g.
        `_run_turn` crashed after its own `updater.failed`/`.complete`
        already ran), and `TaskUpdater.update_status` raises
        `RuntimeError` for that -- this must never mask the original
        crash with a second, unrelated traceback."""

        try:
            await updater.failed(updater.new_agent_message([Part(text=text)]))
        except Exception:
            log.warning("painter: could not report task failure back to the caller", exc_info=True)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise UnsupportedOperationError(
            "painter tasks run to completion synchronously and cannot be cancelled"
        )


__all__ = ["PainterAgentExecutor", "build_agent_card"]
