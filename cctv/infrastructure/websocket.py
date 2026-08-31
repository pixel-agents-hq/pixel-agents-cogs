"""One aiohttp listener, two fully independent WebSocket pipelines --
docs/cctv-design.md §2.4/§2.7. Each `Pipeline` owns its own `ClientHub`
and message handlers; only the listener itself (the `web.Application`/
`AppRunner`/`TCPSite`) and, for the Discord pipeline, its `TicketStore`
are anything close to shared state -- there is no shared `OfficeService`
or authorization state crossing between the two.

Message parsing is deliberately a lightweight `dict`-based dispatch, not
a pydantic contract module (unlike floorplan's former, now-retired
`contracts/websocket.py`): the same shape architect's own former
WebSocket server already used successfully in production for its
(then-unauthenticated) editor page, and the actual structural validation
of a layout/seat payload happens exactly once regardless, in
pixelagents' `OfficeStateFacade` (docs/cctv-design.md §2.6) -- a second,
independent pydantic layer here would duplicate that validation, not add
safety.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from aiohttp import WSMsgType, web

from .client_hub import Authorize, ClientHub
from .tickets import TicketStore

HealthSnapshot = Callable[[], Mapping[str, object]]
WebviewReadyHandler = Callable[[web.WebSocketResponse], Awaitable[None]]
SaveLayoutHandler = Callable[[dict[str, object]], Awaitable[None]]
SaveSeatsHandler = Callable[[Mapping[str, object]], Awaitable[None]]

# Gated on the connecting client being an "editor" (Discord pipeline: a
# ticket-resolved bot owner/keyholder; editor pipeline: every client,
# unconditionally -- see Pipeline.tickets's own docstring).
_MUTATING_TYPES = frozenset({"saveLayout", "saveAgentSeats"})


@dataclass
class Pipeline:
    """One WebSocket pipeline's own routing path, client registry, and
    message handlers.

    `tickets`/`authorize` are both `None` for the editor pipeline -- no
    editor-authorization concept at all, mirroring architect's own former
    dashboard (docs/cctv-design.md §2.7's table): every connected socket
    on that pipeline starts (and stays) able to mutate. Both must be set
    together for the Discord pipeline, which starts every socket as a
    read-only viewer until a resolved ticket's owner passes `authorize`.
    """

    path: str
    clients: ClientHub
    on_webview_ready: WebviewReadyHandler
    on_save_layout: SaveLayoutHandler
    on_save_seats: SaveSeatsHandler
    tickets: TicketStore | None = None
    authorize: Authorize | None = None


class WebSocketServer:
    def __init__(
        self,
        *,
        discord: Pipeline,
        editor: Pipeline,
        health_snapshot: HealthSnapshot,
        logger: logging.Logger | None = None,
    ) -> None:
        self._pipelines: dict[str, Pipeline] = {discord.path: discord, editor.path: editor}
        self._health_snapshot = health_snapshot
        self._log = logger or logging.getLogger(__name__)
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None

    @property
    def running(self) -> bool:
        return self.runner is not None

    async def start(self, host: str, port: int) -> bool:
        """Start once; a bind failure is cleaned up and leaves a stopped
        server (never raises -- see docs/cctv-design.md §2.11's degraded-
        operation contract)."""

        if self.runner is not None:
            return True
        app = web.Application()
        for path, pipeline in self._pipelines.items():
            app.router.add_get(path, self._handler_for(pipeline))
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
                self._log.debug("cctv: failed to clean up partial server: %s", cleanup_exc)
            self._log.error("cctv: could not start office server on %s:%s -- %s", host, port, exc)
            return False
        self.runner = runner
        self.site = site
        self._log.info(
            "cctv: office server listening on %s:%s (%s)",
            host,
            port,
            ", ".join(self._pipelines),
        )
        return True

    async def stop(self) -> None:
        """Close every pipeline's clients and the aiohttp runner safely and
        idempotently."""

        for pipeline in self._pipelines.values():
            await pipeline.clients.close_all()
        runner = self.runner
        self.runner = None
        self.site = None
        if runner is not None:
            try:
                await runner.cleanup()
            except Exception as exc:
                self._log.error("cctv: office server cleanup failed: %s", exc)

    async def handle_health(self, request: web.Request) -> web.Response:
        del request
        return web.json_response(dict(self._health_snapshot()))

    def _handler_for(
        self, pipeline: Pipeline
    ) -> Callable[[web.Request], Awaitable[web.WebSocketResponse]]:
        async def handle_ws(request: web.Request) -> web.WebSocketResponse:
            return await self._handle_ws(pipeline, request)

        return handle_ws

    async def _handle_ws(self, pipeline: Pipeline, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse(heartbeat=30.0, max_msg_size=0)
        await socket.prepare(request)

        user_id: int | None = None
        is_editor = pipeline.tickets is None
        if pipeline.tickets is not None:
            ticket = request.query.get("ticket", "")
            user_id = pipeline.tickets.resolve(ticket) if ticket else None
            is_editor = (
                await self._authorize_safely(pipeline, user_id) if user_id is not None else False
            )
        pipeline.clients.add(socket, user_id=user_id, is_editor=is_editor)
        self._log.info(
            "cctv: %s client connected (%s, %d total)",
            pipeline.path,
            "editor" if is_editor else "viewer",
            pipeline.clients.client_count,
        )
        try:
            async for incoming in socket:
                if incoming.type != WSMsgType.TEXT:
                    if incoming.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                    continue
                try:
                    payload = json.loads(incoming.data)
                except (TypeError, json.JSONDecodeError) as exc:
                    self._log.warning("cctv: invalid client JSON ignored: %s", exc)
                    continue
                await self._handle_payload(pipeline, socket, payload)
        finally:
            pipeline.clients.remove(socket)
            self._log.info(
                "cctv: %s client disconnected (%d left)",
                pipeline.path,
                pipeline.clients.client_count,
            )
        return socket

    async def _handle_payload(
        self, pipeline: Pipeline, socket: web.WebSocketResponse, payload: object
    ) -> None:
        if not isinstance(payload, Mapping):
            return
        message_type = payload.get("type")
        if not isinstance(message_type, str):
            return

        if message_type == "authorize":
            await self._handle_authorize(pipeline, socket, payload)
            return

        if message_type in _MUTATING_TYPES:
            state = pipeline.clients.get(socket)
            if not (state is not None and state.is_editor):
                self._log.info(
                    "cctv: dropped %s from an unauthorized %s client", message_type, pipeline.path
                )
                return

        try:
            if message_type == "webviewReady":
                await pipeline.on_webview_ready(socket)
            elif message_type == "saveLayout":
                layout = payload.get("layout")
                if isinstance(layout, Mapping):
                    await pipeline.on_save_layout(dict(layout))
            elif message_type == "saveAgentSeats":
                seats = payload.get("seats")
                if isinstance(seats, Mapping):
                    await pipeline.on_save_seats(seats)
            elif message_type == "requestDiagnostics":
                await pipeline.clients.send_to(socket, {"type": "agentDiagnostics", "agents": []})
            # Any other type (importLayout, a future message) is a
            # deliberate no-op -- forward-compatible, matching floorplan's/
            # architect's own former "ignore unknown" convention.
        except Exception as exc:
            self._log.error("cctv: %s client message error: %s", pipeline.path, exc, exc_info=True)

    async def _handle_authorize(
        self, pipeline: Pipeline, socket: web.WebSocketResponse, payload: Mapping[str, object]
    ) -> None:
        if pipeline.tickets is None:
            return  # no auth concept on this pipeline -- nothing to upgrade
        ticket = payload.get("ticket")
        if not isinstance(ticket, str):
            return
        user_id = pipeline.tickets.resolve(ticket)
        if user_id is None:
            return
        is_editor = await self._authorize_safely(pipeline, user_id)
        pipeline.clients.identify(socket, user_id, is_editor=is_editor)
        if is_editor:
            self._log.info("cctv: %s client upgraded to editor", pipeline.path)

    async def _authorize_safely(self, pipeline: Pipeline, user_id: int) -> bool:
        assert pipeline.authorize is not None
        try:
            return await pipeline.authorize(user_id)
        except Exception as exc:
            self._log.error("cctv: authorization failed for user %d: %s", user_id, exc)
            return False


__all__ = ["Pipeline", "WebSocketServer"]
