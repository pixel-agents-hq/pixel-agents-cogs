"""A2A server surface: agent card, executor, and the dedicated listener
lifecycle. See docs/architect-design.md section 4.

Built against the real, installed `a2a-sdk` (1.x) API -- its wire types are
protobuf messages (`a2a.types`, generated from `a2a_pb2`), not the plain
pydantic models an earlier SDK generation used. This is a separate network
listener from both Discord and Red Dashboard: A2A is a machine-to-machine
JSON-RPC surface, not a browser page, so it does not go through the
Dashboard third-party page router (see `webview.py` for that surface).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence

import uvicorn
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
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
from starlette.applications import Starlette
from starlette.routing import BaseRoute

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


def build_agent_card(*, host: str, port: int, tools: Sequence[ToolSpec]) -> AgentCard:
    """One skill per tool architect currently offers. Rebuilt on every
    listener (re)start (see `A2AServer.start`), so a host/port or tool-set
    change is reflected the next time this is read."""

    url = f"http://{host}:{port}/"
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
            AgentInterface(url=url, protocol_binding=TransportProtocol.JSONRPC.value)
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


def _build_app(
    *, host: str, port: int, executor: AgentExecutor, tools: Sequence[ToolSpec]
) -> Starlette:
    agent_card = build_agent_card(host=host, port=port, tools=tools)
    request_handler = DefaultRequestHandler(executor, InMemoryTaskStore(), agent_card)
    routes: list[BaseRoute] = [
        *create_agent_card_routes(agent_card),
        *create_jsonrpc_routes(request_handler, "/"),
    ]
    return Starlette(routes=routes)


class A2AServer:
    """Owns the dedicated A2A listener for architect's Cog lifetime -- same
    start-in-cog_load/stop-in-cog_unload shape floorplan already uses for
    its own WebSocket server (`floorplan/infrastructure/websocket.py`).
    A separate bind from Discord and from Red Dashboard's HTTP server."""

    def __init__(self, executor: AgentExecutor, *, logger: logging.Logger | None = None) -> None:
        self._executor = executor
        self._log = logger or log
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, *, host: str, port: int, tools: Sequence[ToolSpec]) -> str | None:
        """Idempotent: stops any previous listener first, so a settings
        change (host/port) can call this again to rebind.

        Never raises. A bind failure (bad host, port already in use, no
        working resolver for the configured host, ...) is reported back as
        an error string instead -- architect must keep working as a
        Discord cog even when its A2A listener can't come up, the same
        "must never raise" convention floorplan's own
        `_notify_owners_dashboard_missing_if_unloaded` follows.

        The bind is deliberately probed here, in this coroutine, *before*
        handing off to uvicorn's own `Server.serve()` background task --
        not merely wrapped in a broad `except` around that task's result.
        uvicorn's `Server.startup()` calls `sys.exit()` on a bind failure,
        raising `SystemExit`. A `SystemExit`/`KeyboardInterrupt` raised
        inside an `asyncio.Task` is a special case CPython's own Task
        implementation re-raises directly out of the *event loop itself*
        (`Task.__step`), not merely something delivered through that
        task's `result()` -- so no `try/except`, however broad, wrapped
        around awaiting or reading the result of a task that runs
        `server.serve()` can catch it; it crashes the whole process
        regardless. Doing the real bind ourselves first means the common
        failure -- including the `socket.gaierror` a broken resolver
        produces even for a loopback address, which is what took down a
        real deployment before this fix -- surfaces as an ordinary,
        synchronously-raised `OSError` in this coroutine, which is safe to
        catch normally, before uvicorn's task (and its `sys.exit()`-on-
        failure path) is ever even created.

        The probe socket is closed once it proves the address is bindable,
        and uvicorn is left to do its own real bind immediately after --
        `asyncio.Server.close()` actually closes the underlying listening
        socket (verified: its fileno becomes -1), so the probe's socket
        object can't itself be handed off for uvicorn to reuse. That
        leaves a narrow, inherent check-then-act race (something else
        could take the port in the gap); the same defense-in-depth
        `except BaseException` below remains for that sliver, even though
        it can't catch a re-raised SystemExit either. The failure this
        fixes -- a persistently broken resolver or a genuinely wrong host
        -- fails at the probe every time, before any task is created."""

        await self.stop()
        loop = asyncio.get_running_loop()
        try:
            probe = await loop.create_server(asyncio.Protocol, host=host, port=port)
        except OSError as exc:
            message = f"could not bind {host}:{port}: {exc}"
            self._log.error("architect: A2A listener failed to start (%s)", message)
            return message
        probe.close()
        await probe.wait_closed()

        app = _build_app(host=host, port=port, executor=self._executor, tools=tools)
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        # No install_signal_handlers here -- that's uvicorn.Server.run()'s
        # job; serve() alone is safe to run as a task inside Red's own
        # event loop, same as floorplan's aiohttp server task.
        task = asyncio.create_task(server.serve(), name="architect-a2a-server")
        # serve() reaches `server.started = True` only after the socket is
        # actually (re-)bound and accepting connections -- wait for that
        # here so callers (including a real client hitting this listener
        # immediately after start() returns) never race it. `server.started`
        # is a plain bool uvicorn sets internally, not an asyncio.Event
        # this code owns, so there's nothing to await instead of polling it.
        while not server.started and not task.done():  # noqa: ASYNC110
            await asyncio.sleep(0)
        if task.done():
            try:
                task.result()
            except BaseException as exc:
                message = f"A2A listener failed to start on {host}:{port}: {exc}"
                self._log.error("architect: %s", message)
                return message
        self._server = server
        self._task = task
        self._log.info("architect: A2A listener starting on %s:%d", host, port)
        return None

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await self._task
            except Exception:
                self._log.exception("architect: A2A listener did not shut down cleanly")
            self._task = None
        self._server = None


__all__ = ["A2AServer", "ArchitectAgentExecutor", "build_agent_card"]
