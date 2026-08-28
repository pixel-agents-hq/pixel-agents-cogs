"""aiohttp transport for architect's own office WebSocket.

Deliberately no ticket/editor-authorization concept, unlike floorplan's own
`WebSocketServer` -- a live-connected client can save a layout here with no
login check at all. This is an explicit choice, not an oversight: floorplan
gates its live editor to bot-owner/`keyholder`-capability members because
its layout is the one thing Discord presence and the Pixel Index catalogue
both depend on, but architect's layout is a separate, disposable sandbox
(seeded once from pixelagents' bundled default, otherwise owned entirely by
this cog) that anyone who can reach `/third-party/architect` should be able
to freely edit. See docs/architect-design.md section 5.

Every inbound message this server reacts to: `webviewReady` (send the
connecting client the current layout) and `saveLayout` (persist a whole new
layout the browser's in-page editor produced, then broadcast it back out to
every connected client -- `OfficeLayoutService.replace_layout` already does
this last part via its own `broadcast` callback). Anything else is ignored,
matching floorplan's own "unknown message types are forward-compatible
no-ops" convention (`floorplan/contracts/websocket.py`). A malformed
`saveLayout` payload -- or one that fails `OfficeLayoutService`'s own
validation -- is logged and dropped, never allowed to crash the connection;
with no login gate, a broken or hostile payload is exactly as likely as a
legitimate one and must be exactly as harmless.

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
from typing import Any

from aiohttp import WSMsgType, web

from .client_hub import ClientHub

WebviewReadyHandler = Callable[[web.WebSocketResponse], Awaitable[None]]
SaveLayoutHandler = Callable[[dict[str, Any]], Awaitable[None]]
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
        on_save_layout: SaveLayoutHandler,
        health_snapshot: HealthSnapshot,
        logger: logging.Logger | None = None,
    ) -> None:
        self.clients = clients
        self._on_webview_ready = on_webview_ready
        self._on_save_layout = on_save_layout
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
                if not isinstance(payload, Mapping):
                    continue
                message_type = payload.get("type")
                if message_type == "webviewReady":
                    await self._on_webview_ready(socket)
                elif message_type == "saveLayout":
                    await self._handle_save_layout(payload)
        finally:
            self.clients.remove(socket)
            self._log.info(
                "architect: office client disconnected (%d left)", self.clients.client_count
            )
        return socket

    async def _handle_save_layout(self, payload: Mapping[str, object]) -> None:
        """Structural shape check first (no login gate means a malformed
        or hostile payload is exactly as likely as a legitimate one), then
        delegate to `_on_save_layout` -- any exception it raises (invalid
        per `OfficeLayoutService`'s own rules, or a missing/wrong-typed
        field `decode()` itself trips over) is logged and dropped, same as
        any other unauthorized-editor-adjacent failure mode: never crash
        the connection over it."""

        layout = payload.get("layout")
        if not isinstance(layout, Mapping):
            self._log.warning("architect: saveLayout message missing a layout object, ignored")
            return
        try:
            await self._on_save_layout(dict(layout))
        except Exception as exc:
            self._log.warning("architect: saveLayout rejected, ignored: %s", exc)


__all__ = ["WEBSOCKET_PATH", "WebSocketServer"]
