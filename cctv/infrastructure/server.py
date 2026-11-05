"""One aiohttp listener routing every guild office and the editor office."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Protocol

from aiohttp import WSMsgType, web

from ..contracts import (
    AuthorizeMessage,
    ClientMessage,
    ImportLayoutMessage,
    InvalidClientMessageError,
    SaveAgentSeatsMessage,
    SaveLayoutMessage,
    WebviewReadyMessage,
    parse_client_message,
)
from .client_hub import ClientHub
from .tickets import TicketStore

HealthSnapshot = Callable[[], Mapping[str, object]]
_WRITE_MESSAGES = (SaveLayoutMessage, SaveAgentSeatsMessage, ImportLayoutMessage)
# Close code for a socket to an office the client may not watch: no open
# office for that id (unknown guild, not a guild, CCTV disabled there), or a
# private office the socket didn't prove membership of. One code, and the
# same wait, for every such case, so a client can't tell a guild that
# doesn't exist from one that doesn't use CCTV or keeps its office private.
OFFICE_UNAVAILABLE_CLOSE_CODE = 4404
# How long a socket to a gated (private or unavailable) office may take to
# present its ticket before it is closed.
AUTHORIZE_TIMEOUT_SECONDS = 10.0


class PagePipeline(Protocol):
    page: str
    clients: ClientHub
    open_editor: bool
    # A private guild office: sockets see nothing until they prove membership.
    private: bool

    @property
    def guild_id(self) -> int | None: ...

    async def authorize(self, user_id: int) -> bool: ...
    async def can_view(self, user_id: int) -> bool: ...
    async def handle_message(
        self, socket: web.WebSocketResponse, message: ClientMessage
    ) -> None: ...


OfficeResolver = Callable[[int], "PagePipeline | None"]
OfficeList = Callable[[], Iterable["PagePipeline"]]
# The origin browsers reach the Dashboard at (`[p]cctv publicurl`), or None.
OriginSource = Callable[[], Awaitable["str | None"]]


async def _no_origin() -> str | None:
    return None


class CctvServer:
    def __init__(
        self,
        offices: OfficeResolver,
        all_offices: OfficeList,
        editor: PagePipeline,
        tickets: TicketStore,
        health: HealthSnapshot,
        *,
        public_origin: OriginSource = _no_origin,
        authorize_timeout: float = AUTHORIZE_TIMEOUT_SECONDS,
        logger: logging.Logger | None = None,
    ) -> None:
        self._public_origin = public_origin
        self._authorize_timeout = authorize_timeout
        self._offices = offices
        self._all_offices = all_offices
        self._editor = editor
        self._tickets = tickets
        self._health = health
        self._log = logger or logging.getLogger(__name__)
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.app: web.Application | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self.runner is not None

    async def start(self, host: str, port: int) -> bool:
        if self.runner is not None:
            return True
        app = web.Application()
        app.router.add_get("/cctv/office/{guild_id}/ws", self.handle_office)
        app.router.add_get("/cctv/editor/ws", self.handle_editor)
        app.router.add_get("/cctv/health", self.handle_health)
        runner = web.AppRunner(app)
        try:
            await runner.setup()
            site = web.TCPSite(runner, host, port)
            await site.start()
        except Exception as exc:
            try:
                await runner.cleanup()
            except Exception:
                self._log.debug("cctv: partial listener cleanup failed", exc_info=True)
            self.last_error = f"could not bind {host}:{port}: {exc}"
            self._log.error("cctv: %s", self.last_error)
            return False
        self.app = app
        self.runner = runner
        self.site = site
        self.last_error = None
        self._log.info(
            "cctv: listening on %s:%d (/cctv/office/<guild>/ws, /cctv/editor/ws)",
            host,
            port,
        )
        return True

    async def stop(self) -> None:
        for pipeline in (*self._all_offices(), self._editor):
            await pipeline.clients.close_all()
        runner = self.runner
        self.runner = None
        self.site = None
        self.app = None
        if runner is not None:
            try:
                await runner.cleanup()
            except Exception:
                self._log.error("cctv: listener cleanup failed", exc_info=True)

    async def handle_health(self, request: web.Request) -> web.Response:
        del request
        return web.json_response(dict(self._health()))

    async def handle_office(self, request: web.Request) -> web.WebSocketResponse:
        raw = request.match_info.get("guild_id", "")
        guild_id = int(raw) if raw.isdecimal() and len(raw) <= 20 else None
        pipeline = self._offices(guild_id) if guild_id is not None else None
        if pipeline is not None and not pipeline.private:
            return await self._handle_ws(pipeline, request)
        if pipeline is not None and not await self._origin_allowed(request):
            pipeline = None
        return await self._handle_gated(request, guild_id, pipeline)

    async def handle_editor(self, request: web.Request) -> web.WebSocketResponse:
        return await self._handle_ws(self._editor, request)

    async def _origin_allowed(self, request: web.Request) -> bool:
        """A private office's socket must come from the Dashboard's own
        origin once `[p]cctv publicurl` names it (cross-site socket
        hijacking guard; tickets are the real gate)."""

        expected = await self._public_origin()
        return expected is None or request.headers.get("Origin", "") == expected

    async def _handle_ws(
        self, pipeline: PagePipeline, request: web.Request
    ) -> web.WebSocketResponse:
        socket = web.WebSocketResponse(heartbeat=30.0, max_msg_size=0)
        await socket.prepare(request)
        pipeline.clients.add(socket, is_editor=pipeline.open_editor)
        self._log.info(
            "cctv/%s: client connected (%d total)",
            pipeline.page,
            pipeline.clients.client_count,
        )
        try:
            await self._serve(pipeline, socket)
        finally:
            pipeline.clients.remove(socket)
        return socket

    async def _handle_gated(
        self, request: web.Request, guild_id: int | None, pipeline: PagePipeline | None
    ) -> web.WebSocketResponse:
        """A private office -- or an id with no open office, which answers
        exactly the same way. Nothing is sent until the socket presents a
        ticket minted for this guild by a member; a `webviewReady` sent
        before that is held and replayed. No valid ticket in time: closed."""

        socket = web.WebSocketResponse(heartbeat=30.0, max_msg_size=0)
        await socket.prepare(request)
        if pipeline is not None:
            pipeline.clients.add(socket, can_view=False)
        try:
            ready = await self._await_ticket(socket, guild_id, pipeline)
            if ready is None:
                await socket.close(code=OFFICE_UNAVAILABLE_CLOSE_CODE, message=b"unavailable")
                return socket
            assert pipeline is not None
            if ready:
                await self._dispatch(pipeline, socket, WebviewReadyMessage(type="webviewReady"))
            await self._serve(pipeline, socket)
        finally:
            if pipeline is not None:
                pipeline.clients.remove(socket)
        return socket

    async def _await_ticket(
        self,
        socket: web.WebSocketResponse,
        guild_id: int | None,
        pipeline: PagePipeline | None,
    ) -> bool | None:
        """Wait for the socket's `authorize`. Returns whether a held
        `webviewReady` should be replayed, or None to refuse the socket."""

        deadline = time.monotonic() + self._authorize_timeout
        ready = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                incoming = await socket.receive(timeout=remaining)
            except TimeoutError:
                return None
            if incoming.type != WSMsgType.TEXT:
                if incoming.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                    return None
                continue
            try:
                message = parse_client_message(json.loads(incoming.data))
            except (TypeError, json.JSONDecodeError, InvalidClientMessageError):
                continue
            if isinstance(message, WebviewReadyMessage):
                ready = True
            elif isinstance(message, AuthorizeMessage):
                user_id = (
                    self._tickets.consume(message.ticket, guild_id)
                    if guild_id is not None
                    else None
                )
                if pipeline is None or user_id is None:
                    return None
                if not await self._can_view_safely(pipeline, user_id):
                    return None
                pipeline.clients.identify(
                    socket,
                    user_id,
                    is_editor=await self._authorize_safely(pipeline, user_id),
                    can_view=True,
                )
                return ready

    async def _serve(self, pipeline: PagePipeline, socket: web.WebSocketResponse) -> None:
        async for incoming in socket:
            if incoming.type != WSMsgType.TEXT:
                if incoming.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                    break
                continue
            try:
                payload = json.loads(incoming.data)
            except (TypeError, json.JSONDecodeError) as exc:
                self._log.warning("cctv/%s: invalid JSON ignored: %s", pipeline.page, exc)
                continue
            await self.handle_payload(pipeline, socket, payload)

    async def handle_payload(
        self, pipeline: PagePipeline, socket: web.WebSocketResponse, payload: object
    ) -> None:
        try:
            message = parse_client_message(payload)
        except InvalidClientMessageError as exc:
            self._log.warning("cctv/%s: %s", pipeline.page, exc)
            return
        if message is None:
            return
        await self._dispatch(pipeline, socket, message)

    async def _dispatch(
        self, pipeline: PagePipeline, socket: web.WebSocketResponse, message: ClientMessage
    ) -> None:
        page = pipeline.page
        state = pipeline.clients.get(socket)
        if isinstance(message, AuthorizeMessage):
            guild_id = pipeline.guild_id
            if pipeline.open_editor or guild_id is None:
                return
            user_id = self._tickets.consume(message.ticket, guild_id)
            if user_id is None:
                return
            pipeline.clients.identify(
                socket,
                user_id,
                is_editor=await self._authorize_safely(pipeline, user_id),
            )
            return
        if state is not None and not state.can_view:
            return
        if isinstance(message, _WRITE_MESSAGES):
            # A ticket proves browser identity, not perpetual authorization.
            # Re-check office writes so a guild disable, role removal, or
            # permission change takes effect even on an already-open socket.
            if not pipeline.open_editor and state is not None and state.user_id is not None:
                state.is_editor = await self._authorize_safely(pipeline, state.user_id)
            if state is None or not state.is_editor:
                self._log.info("cctv/%s: unauthorized %s dropped", page, message.type)
                return
        try:
            await pipeline.handle_message(socket, message)
        except Exception:
            self._log.error("cctv/%s: client message failed", page, exc_info=True)

    async def _can_view_safely(self, pipeline: PagePipeline, user_id: int) -> bool:
        try:
            return await pipeline.can_view(user_id)
        except Exception:
            self._log.error(
                "cctv/%s: membership check failed for %d", pipeline.page, user_id, exc_info=True
            )
            return False

    async def _authorize_safely(self, pipeline: PagePipeline, user_id: int) -> bool:
        try:
            return await pipeline.authorize(user_id)
        except Exception:
            self._log.error(
                "cctv/%s: authorization failed for %d",
                pipeline.page,
                user_id,
                exc_info=True,
            )
            return False


__all__ = [
    "AUTHORIZE_TIMEOUT_SECONDS",
    "OFFICE_UNAVAILABLE_CLOSE_CODE",
    "CctvServer",
    "HealthSnapshot",
    "OfficeList",
    "OfficeResolver",
    "OriginSource",
    "PagePipeline",
]
