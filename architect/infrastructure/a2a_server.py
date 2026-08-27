"""Architect's A2A surface: agent card + executor. See
docs/architect-design.md section 4 and docs/agent-directory-design.md.

Built against the real, installed `a2a-sdk` (1.x) API -- its wire types are
protobuf messages (`a2a.types`, generated from `a2a_pb2`), not the plain
pydantic models an earlier SDK generation used.

Architect no longer owns an A2A listener of its own (see
docs/agent-directory-design.md): the `AgentCard`/`AgentExecutor` built here
are handed to `corridor.register_agent(...)` at `cog_load`, and corridor
mounts them on its own shared listener alongside every other registered
agent. `AgentCard.supported_interfaces[0].url` set here is a placeholder --
corridor overwrites it with its own configured host/port + this agent's
mount path before storing it (`corridor.domain.card_with_url`)."""

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

log = logging.getLogger("red.architect")

AGENT_NAME = "architect"
AGENT_VERSION = "0.1.0"
AGENT_DESCRIPTION = (
    "A second, independent LLM agent reachable only over A2A -- never "
    "Discord-user-facing. Consult it to delegate a sub-task."
)


def build_agent_card(*, tools: Sequence[ToolSpec]) -> AgentCard:
    """One skill per tool architect currently offers. The URL is a
    placeholder (`corridor.register_agent` overwrites it) -- this card no
    longer describes a listener architect itself binds."""

    skills = [
        AgentSkill(id=tool.name, name=tool.name, description=tool.description, tags=["architect"])
        for tool in tools
    ] or [
        AgentSkill(
            id="chat",
            name="chat",
            description="General-purpose delegated task, answered as plain text.",
            tags=["architect"],
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
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=skills,
    )


class ArchitectAgentExecutor(AgentExecutor):
    """Bridges one inbound A2A message to architect's own bounded
    `ToolLoopService`, using the same corridor-shared LLM connection pico
    uses. `context.get_user_input()` (the inbound message's text parts,
    joined) becomes the tool loop's one user turn -- there is no persisted
    multi-turn conversation, mirroring pico's own no-session design."""

    def __init__(
        self,
        *,
        tool_loop: ToolLoopService,
        tools: Sequence[ToolSpec],
        settings: Callable[[], Awaitable[GlobalSettings]],
        llm_settings: Callable[[], Awaitable[LLMSettings]],
        publish_activity: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._tool_loop = tool_loop
        self._tools = tools
        self._settings = settings
        self._llm_settings = llm_settings
        self._publish_activity = publish_activity

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

        user_input = context.get_user_input()
        llm_settings = await self._llm_settings()
        if not llm_settings.ready:
            await updater.failed(
                updater.new_agent_message(
                    [Part(text="Architect's shared LLM connection is not configured yet.")]
                )
            )
            return

        settings = await self._settings()
        result = await self._tool_loop.run(
            base_url=llm_settings.llm_base_url,
            api_key=llm_settings.llm_api_key or "",
            model=llm_settings.llm_model or "",
            system_prompt=settings.system_prompt,
            user_input=user_input,
            tools=self._tools,
            max_tool_calls=settings.max_tool_calls,
            debug=settings.debug_logging,
            on_activity=self._publish_activity,
        )

        if result.stopped_reason != "final_text" or result.text is None:
            await updater.failed(
                updater.new_agent_message(
                    [Part(text=f"Architect could not produce an answer ({result.stopped_reason}).")]
                )
            )
            return

        await updater.complete(updater.new_agent_message([Part(text=result.text)]))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise UnsupportedOperationError(
            "architect tasks run to completion synchronously and cannot be cancelled"
        )


__all__ = ["ArchitectAgentExecutor", "build_agent_card"]
