"""aiohttp transport for the Pixel Agents office protocol."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping

from aiohttp import WSMsgType, web

from floorplan.contracts.websocket import (
    AuthorizeMessage,
    ClientMessage,
    ImportLayoutMessage,
    InvalidClientMessageError,
    SaveAgentSeatsMessage,
    SaveLayoutMessage,
    parse_client_message,
)

from .client_hub import Authorize, ClientHub
from .tickets import TicketStore

ApplicationMessageHandler = Callable[[web.WebSocketResponse, ClientMessage], Awaitable[None]]
HealthSnapshot = Callable[[], Mapping[str, object]]

_EDITOR_MESSAGE_TYPES = (SaveLayoutMessage, SaveAgentSeatsMessage, ImportLayoutMessage)


class WebSocketServer:
    """Own aiohttp routes, socket lifecycle, ingress parsing, and authorization."""

    def __init__(
        self,
        *,
        clients: ClientHub,
        tickets: TicketStore,
        authorize: Authorize,
        handle_application_message: ApplicationMessageHandler,
        health_snapshot: HealthSnapshot,
        logger: logging.Logger | None = None,
    ) -> None:
        self.clients = clients
        self.tickets = tickets
        self._authorize = authorize
        self._handle_application_message = handle_application_message
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
        app.router.add_get("/ws", self.handle_ws)
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
                self._log.debug("floorplan: failed to clean up partial server: %s", cleanup_exc)
            self._log.error(
                "floorplan: could not start office server on %s:%s — %s", host, port, exc
            )
            return False
        self.runner = runner
        self.site = site
        self._log.info("floorplan: office server listening on %s:%s/ws", host, port)
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
                self._log.error("floorplan: office server cleanup failed: %s", exc)

    async def handle_health(self, request: web.Request) -> web.Response:
        del request
        return web.json_response(dict(self._health_snapshot()))

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse(heartbeat=30.0, max_msg_size=0)
        await socket.prepare(request)

        ticket = request.query.get("ticket", "")
        user_id = self.tickets.resolve(ticket) if ticket else None
        is_editor = await self._authorize_safely(user_id) if user_id is not None else False
        self.clients.add(socket, user_id=user_id, is_editor=is_editor)
        self._log.info(
            "floorplan: office client connected (%s, %d total)",
            "editor" if is_editor else "viewer",
            self.clients.client_count,
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
                    self._log.warning("floorplan: invalid client JSON ignored: %s", exc)
                    continue
                await self.handle_payload(socket, payload)
        finally:
            self.clients.remove(socket)
            self._log.info(
                "floorplan: office client disconnected (%d left)", self.clients.client_count
            )
        return socket

    async def handle_payload(self, socket: web.WebSocketResponse, payload: object) -> None:
        """Validate one decoded payload, ignoring malformed and future messages."""

        try:
            message = parse_client_message(payload)
        except InvalidClientMessageError as exc:
            self._log.warning("floorplan: invalid known client message ignored: %s", exc)
            return
        if message is None:
            return
        await self.handle_message(socket, message)

    async def handle_message(self, socket: web.WebSocketResponse, message: ClientMessage) -> None:
        """Apply transport authorization, then delegate application behavior."""

        if isinstance(message, AuthorizeMessage):
            user_id = self.tickets.resolve(message.ticket)
            if user_id is None:
                return
            is_editor = await self._authorize_safely(user_id)
            self.clients.identify(socket, user_id, is_editor=is_editor)
            if is_editor:
                self._log.info("floorplan: office client upgraded to editor")
            return

        state = self.clients.get(socket)
        if isinstance(message, _EDITOR_MESSAGE_TYPES) and not (
            state is not None and state.is_editor
        ):
            self._log.info("floorplan: dropped %s from an unauthorized office client", message.type)
            return
        try:
            await self._handle_application_message(socket, message)
        except Exception as exc:
            self._log.error("floorplan: client message error: %s", exc, exc_info=True)

    async def _authorize_safely(self, user_id: int) -> bool:
        try:
            return await self._authorize(user_id)
        except Exception as exc:
            self._log.error("floorplan: authorization failed for user %d: %s", user_id, exc)
            return False
