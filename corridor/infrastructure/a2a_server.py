"""Corridor's one process-wide A2A listener -- every registered agent
(architect today, more later) is mounted under its own path
(`/<agent_key>/`) on this single `uvicorn`/Starlette server, instead of
each agent binding a socket of its own. See
docs/agent-directory-design.md for the full design rationale.

This is a **relocation** of `architect/infrastructure/a2a_server.py`'s
former `A2AServer` class (bind-probe, uvicorn lifecycle, the
`SystemExit`-from-uvicorn defense -- see `docs/architect-design.md` §9's
incident writeup for why that code looks the way it does), generalized
from "one agent's fixed routes" to "whatever's currently in corridor's
`AgentDirectoryService`". Built against the real, installed `a2a-sdk`
(1.x) API -- its wire types are protobuf messages (`a2a.types`, generated
from `a2a_pb2`), not the plain pydantic models an earlier SDK generation
used.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import uvicorn
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from sse_starlette.sse import AppStatus
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import BaseRoute, Mount, Route

from ..domain.agent_directory import RegisteredAgent

log = logging.getLogger("red.corridor")

# This process runs more than one `uvicorn.Server` as a concurrent asyncio
# task (this listener, plus suggestionbox's own MCP listener) -- and, as of
# the installed uvicorn (0.52.4), `Server.serve()` itself unconditionally
# wraps its body in `capture_signals()`, which calls `signal.signal(sig,
# self.handle_exit)` for SIGINT/SIGTERM. That's a change from older uvicorn,
# where only `Server.run()` installed signal handlers -- this class's own
# `start()` docstring below still describes that now-stale assumption.
# Every `server.serve()` call here therefore silently steals the process's
# SIGTERM/SIGINT handler from whichever listener held it before.
# `sse_starlette` additionally patches `uvicorn.main.Server.handle_exit` at
# import time and runs a background watcher that finds "the" uvicorn server
# via `signal.getsignal(SIGTERM)` and polls *that one instance's*
# `should_exit` -- treating it as authoritative for every SSE stream in the
# whole process. Net effect, verified by direct reproduction with two
# trivial, otherwise-unrelated uvicorn servers: stopping *any* listener in
# this process (`self._server.should_exit = True` below, or suggestionbox's
# own `McpListener.stop()`) can be picked up by that watcher as "the process
# is shutting down" and silently kill every *other* in-flight SSE stream,
# including this listener's own -- surfacing to a caller only as "peer
# closed connection without sending complete message body". Each listener
# here already manages its own shutdown explicitly; sse_starlette's
# automatic cross-process drain-on-signal behavior is not needed and is
# actively unsafe with multiple independent listeners, so it's disabled
# once, process-wide, here.
#
# `disable_automatic_graceful_drain()` alone only stops *future* poisoning
# -- `_shutdown_watcher`'s very first check is a bare `if AppStatus.should_exit:
# break`, unconditional on the drain-enabled flag, so a process that already
# tripped `should_exit` (any earlier listener restart before this fix was
# loaded) stays permanently poisoned across cog reloads: `AppStatus` lives in
# `sse_starlette`'s own module, which office-cogs' hot-reload never touches,
# so that class attribute survives every reload of this file. Force it back
# to False here too, every time this module (re)loads, so a hot-reload of
# this fix actually clears an already-poisoned process instead of only
# preventing the next poisoning.
AppStatus.disable_automatic_graceful_drain()  # type: ignore[no-untyped-call]
AppStatus.should_exit = False


def _avatar_route(agent: RegisteredAgent) -> Route:
    """Serves `agent.avatar_path`'s bytes fresh on every request -- same
    no-caching tradeoff `ReplySender`'s own attachment re-upload accepts
    (docs/reply-identity-design.md section 2). 404 if the configured file
    doesn't currently exist, never a 500 -- a missing avatar is an
    absent-icon condition, not a server error."""

    avatar_path = agent.avatar_path
    assert avatar_path is not None

    async def handler(request: Request) -> Response:
        if not avatar_path.exists():
            return Response(status_code=404)
        return FileResponse(avatar_path)

    return Route("/avatar.png", handler)


def _build_routes(agents: Sequence[RegisteredAgent]) -> list[BaseRoute]:
    """One Starlette `Mount` per agent, at `/<agent_key>/` -- a fresh
    `InMemoryTaskStore` per agent (matching architect's former
    one-task-store-per-agent scope, not shared across agents) and a
    fresh `DefaultRequestHandler` built from that agent's own
    `card`/`executor`. An agent with `avatar_path` set additionally gets
    a static `/avatar.png` route under the same mount -- see
    docs/reply-identity-design.md section 7."""

    routes: list[BaseRoute] = []
    for agent in agents:
        handler = DefaultRequestHandler(agent.executor, InMemoryTaskStore(), agent.card)
        agent_routes: list[BaseRoute] = [
            *create_agent_card_routes(agent.card),
            *create_jsonrpc_routes(handler, "/"),
        ]
        if agent.avatar_path is not None:
            agent_routes.append(_avatar_route(agent))
        routes.append(Mount(f"/{agent.agent_key}", routes=agent_routes))
    return routes


def _build_app(agents: Sequence[RegisteredAgent]) -> Starlette:
    return Starlette(routes=_build_routes(agents))


class A2AServer:
    """Owns corridor's one process-wide A2A listener for corridor's own
    Cog lifetime -- started once from corridor's own `cog_load`,
    host/port configured via `[p]corridor a2a host/port` (bot owner),
    NOT per-agent. A separate bind from Discord and from Red Dashboard's
    HTTP server, same as architect's former listener was."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._log = logger or log
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None
        self._app: Starlette | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(
        self, *, host: str, port: int, agents: Sequence[RegisteredAgent] = ()
    ) -> str | None:
        """Idempotent: stops any previous listener first, so a settings
        change (host/port) can call this again to rebind. `agents` seeds
        the initial route table -- pass corridor's current
        `AgentDirectoryService.list_agents()` so a rebind doesn't lose
        whatever was already registered.

        Never raises. A bind failure (bad host, port already in use, no
        working resolver for the configured host, ...) is reported back
        as an error string instead -- corridor must keep working as a
        Discord cog even when its A2A listener can't come up.

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
        failure surfaces as an ordinary, synchronously-raised `OSError`
        in this coroutine, which is safe to catch normally, before
        uvicorn's task (and its `sys.exit()`-on-failure path) is ever
        even created. See `docs/architect-design.md` §9 for the real
        production incident this defends against -- this is the same
        fix, relocated verbatim.

        The probe socket is closed once it proves the address is
        bindable, and uvicorn is left to do its own real bind
        immediately after -- `asyncio.Server.close()` actually closes
        the underlying listening socket, so the probe's socket object
        can't itself be handed off for uvicorn to reuse. That leaves a
        narrow, inherent check-then-act race (something else could take
        the port in the gap); the same defense-in-depth
        `except BaseException` below remains for that sliver, even
        though it can't catch a re-raised SystemExit either."""

        await self.stop()
        loop = asyncio.get_running_loop()
        try:
            probe = await loop.create_server(asyncio.Protocol, host=host, port=port)
        except OSError as exc:
            message = f"could not bind {host}:{port}: {exc}"
            self._log.error("corridor: A2A listener failed to start (%s)", message)
            return message
        probe.close()
        await probe.wait_closed()

        app = _build_app(agents)
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        # `server.run()` is NOT called here (that installs signal handlers
        # meant for owning the whole process) -- but `serve()` itself, as
        # of the installed uvicorn, installs its own SIGINT/SIGTERM handler
        # too, via `capture_signals()`. Running this (and suggestionbox's
        # own MCP listener) as tasks in Red's event loop means each one
        # steals that signal slot from the other -- see the module-level
        # `AppStatus.disable_automatic_graceful_drain()` call above for why
        # that's neutralized rather than avoided outright.
        task = asyncio.create_task(server.serve(), name="corridor-a2a-server")
        # serve() reaches `server.started = True` only after the socket is
        # actually (re-)bound and accepting connections -- wait for that
        # here so callers (including a real client hitting this listener
        # immediately after start() returns) never race it.
        while not server.started and not task.done():  # noqa: ASYNC110
            await asyncio.sleep(0)
        if task.done():
            try:
                task.result()
            except BaseException as exc:
                message = f"A2A listener failed to start on {host}:{port}: {exc}"
                self._log.error("corridor: %s", message)
                return message
        self._server = server
        self._task = task
        self._app = app
        self._log.info("corridor: A2A listener starting on %s:%d", host, port)
        return None

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await self._task
            except Exception:
                self._log.exception("corridor: A2A listener did not shut down cleanly")
            self._task = None
        self._server = None
        self._app = None

    def rebuild_routes(self, agents: Sequence[RegisteredAgent]) -> None:
        """Replace the live app's route list with a freshly built one, in
        a single attribute assignment -- never `.append()`/`.remove()` in
        place. Starlette's `Router.app` re-reads `self.routes` fresh on
        every incoming request (it is never cached across requests), so
        a clean swap between requests is all that's needed: an in-flight
        request that already started iterating the *old* list object is
        never mutated out from under it, since that old list is left
        untouched and simply discarded once no longer referenced.

        A no-op if the listener isn't currently running -- nothing to
        mount onto; the next successful `start()` picks up the current
        directory contents via its own `agents` argument instead."""

        if self._app is None:
            return
        self._app.router.routes = _build_routes(agents)


__all__ = ["A2AServer"]
