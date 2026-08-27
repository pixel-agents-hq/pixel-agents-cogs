"""aiohttp transport for architect's own office WebSocket.

Read-only for now -- there is no ticket/editor-authorization concept here
at all, since layout edits arrive only through future Discord commands and
tools, never live in the browser (see docs/architect-design.md). The only
inbound message this server ever reacts to is `webviewReady`; anything
else is ignored, matching floorplan's own "unknown message types are
forward-compatible no-ops" convention (`floorplan/contracts/websocket.py`).

Exposed externally at `WEBSOCKET_PATH`, a path distinct from floorplan's
own `/ws` -- both this internal aiohttp route and the operator's
reverse-proxy rule for reaching it publicly must use this same path (see
`infrastructure/webview.py`'s `WS_REWRITE_SHIM`, which rewrites the
webview bundle's hardcoded same-path `/ws` connection to point here
instead). Without that reverse-proxy rule, this server is reachable only
on its own local bind (`ws_host`/`ws_port`), the same operational
requirement floorplan's own WebSocket server already has.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping

from aiohttp import WSMsgType, web

from .client_hub import ClientHub

WebviewReadyHandler = Callable[[web.WebSocketResponse], Awaitable[None]]
HealthSnapshot = Callable[[], Mapping[str, object]]

# Must match the path infrastructure/webview.py's WS_REWRITE_SHIM rewrites
# the browser's connection to, and the path an operator's reverse-proxy
# rule forwards to this server's own ws_host:ws_port bind.
WEBSOCKET_PATH = "/architect/ws"


class WebSocketServer:
    """Own aiohttp routes and socket lifecycle for architect's office view."""

    def __init__(
        self,
        *,
        clients: ClientHub,
        on_webview_ready: WebviewReadyHandler,
        health_snapshot: HealthSnapshot,
        logger: logging.Logger | None = None,
    ) -> None:
        self.clients = clients
        self._on_webview_ready = on_webview_ready
        self._health_snapshot = health_snapshot
        self._log = logger or logging.getLogger(__name__)
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

    @property
    def running(self) -> bool:
        return self.runner is not None

    async def start(self, host: str, port: int) -> bool:
        """Start once; a bind failure is cleaned up and leaves a stopped server."""

        if self.runner is not None:
            return True
        app = web.Application()
        app.router.add_get(WEBSOCKET_PATH, self.handle_ws)
        app.router.add_get("/api/health", self.handle_health)
        runner = web.AppRunner(app)
        try:
            await runner.setup()
            site = web.TCPSite(runner, host, port)
            await site.start()
        except Exception as exc:
            try:
                await runner.cleanup()
            except Exception as cleanup_exc:
                self._log.debug("architect: failed to clean up partial server: %s", cleanup_exc)
            self._log.error(
                "architect: could not start office server on %s:%s — %s", host, port, exc
            )
            return False
        self.runner = runner
        self.site = site
        self._log.info("architect: office server listening on %s:%s%s", host, port, WEBSOCKET_PATH)
        return True

    async def stop(self) -> None:
        """Close clients and the aiohttp runner safely and idempotently."""

        await self.clients.close_all()
        runner = self.runner
        self.runner = None
        self.site = None
        if runner is not None:
            try:
                await runner.cleanup()
            except Exception as exc:
                self._log.error("architect: office server cleanup failed: %s", exc)

    async def handle_health(self, request: web.Request) -> web.Response:
        del request
        return web.json_response(dict(self._health_snapshot()))

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse(heartbeat=30.0, max_msg_size=0)
        await socket.prepare(request)
        self.clients.add(socket)
        self._log.info("architect: office client connected (%d total)", self.clients.client_count)
        try:
            async for incoming in socket:
                if incoming.type != WSMsgType.TEXT:
                    if incoming.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                    continue
                try:
                    payload = json.loads(incoming.data)
                except (TypeError, json.JSONDecodeError) as exc:
                    self._log.warning("architect: invalid client JSON ignored: %s", exc)
                    continue
                if isinstance(payload, Mapping) and payload.get("type") == "webviewReady":
                    await self._on_webview_ready(socket)
        finally:
            self.clients.remove(socket)
            self._log.info(
                "architect: office client disconnected (%d left)", self.clients.client_count
            )
        return socket


__all__ = ["WEBSOCKET_PATH", "WebSocketServer"]
