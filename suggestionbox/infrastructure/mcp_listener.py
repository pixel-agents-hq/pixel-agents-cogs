"""suggestionbox's own MCP listener -- binds a dedicated port for its
Streamable HTTP MCP server. Deliberately its own listener, not mounted
onto corridor's shared A2A listener: a different wire protocol serving a
different audience (external MCP clients, plus corridor's own MCP client
on behalf of a registered A2A agent), and there is exactly one MCP-serving
cog today -- see docs/suggestionbox-design.md §3/§9 on revisiting this if
a second one ever shows up.

Bind-probe/uvicorn-lifecycle shape is a straight copy of `corridor.
infrastructure.a2a_server.A2AServer` (itself relocated from architect's
former listener, see `docs/architect-design.md` §9's incident writeup for
why the bind is probed synchronously before handing off to uvicorn).

This listener's `stop()` (`should_exit = True`) can, without
`corridor.infrastructure.a2a_server`'s module-level
`AppStatus.disable_automatic_graceful_drain()`, silently kill every
*other* in-flight SSE stream in this process -- including corridor's own
A2A traffic -- via a cross-listener signal-handler/shutdown-watcher
interaction in `uvicorn`/`sse_starlette`. See that module's own comment
for the full mechanism. Not re-disabled here: it's a process-wide flag,
and corridor is guaranteed loaded (and that module imported) before this
listener ever starts, since suggestionbox depends on corridor.
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn
from mcp.server.fastmcp import FastMCP

log = logging.getLogger("red.suggestionbox")


class McpListener:
    """Started/stopped from this cog's own `cog_load`/`cog_unload`, and
    restarted whenever `[p]suggestionbox mcp host/port` changes -- one
    listener per Cog lifetime, like `A2AServer`."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._log = logger or log
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, mcp: FastMCP, *, host: str, port: int) -> str | None:
        """Idempotent: stops any previous listener first, so a settings
        change (host/port, or a freshly rebuilt `FastMCP` after the
        feedback channel/agent-access state it closes over changed) can
        call this again to rebind. Never raises -- a bind failure is
        reported back as an error string, same never-raise convention
        `A2AServer.start` already documents in full (including why the
        bind is probed here, synchronously, before uvicorn's own
        `Server.serve()` task is created)."""

        await self.stop()
        loop = asyncio.get_running_loop()
        try:
            probe = await loop.create_server(asyncio.Protocol, host=host, port=port)
        except OSError as exc:
            message = f"could not bind {host}:{port}: {exc}"
            self._log.error("suggestionbox: MCP listener failed to start (%s)", message)
            return message
        probe.close()
        await probe.wait_closed()

        config = uvicorn.Config(
            mcp.streamable_http_app(), host=host, port=port, log_level="warning"
        )
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve(), name="suggestionbox-mcp-server")
        while not server.started and not task.done():  # noqa: ASYNC110
            await asyncio.sleep(0)
        if task.done():
            try:
                task.result()
            except BaseException as exc:
                message = f"MCP listener failed to start on {host}:{port}: {exc}"
                self._log.error("suggestionbox: %s", message)
                return message
        self._server = server
        self._task = task
        self._log.info("suggestionbox: MCP listener starting on %s:%d", host, port)
        return None

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await self._task
            except Exception:
                self._log.exception("suggestionbox: MCP listener did not shut down cleanly")
            self._task = None
        self._server = None


__all__ = ["McpListener"]
