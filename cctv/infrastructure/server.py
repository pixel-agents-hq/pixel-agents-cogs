"""One aiohttp listener routing two independent CCTV pipelines."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from typing import Protocol

from aiohttp import WSMsgType, web

from ..contracts import (
    AuthorizeMessage,
    ClientMessage,
    ImportLayoutMessage,
    InvalidClientMessageError,
    SaveAgentSeatsMessage,
    SaveLayoutMessage,
    parse_client_message,
)
from .client_hub import ClientHub
from .tickets import TicketStore

HealthSnapshot = Callable[[], Mapping[str, object]]
_WRITE_MESSAGES = (SaveLayoutMessage, SaveAgentSeatsMessage, ImportLayoutMessage)


class PagePipeline(Protocol):
    page: str
    clients: ClientHub
    open_editor: bool

    async def authorize(self, user_id: int) -> bool: ...
    async def handle_message(
        self, socket: web.WebSocketResponse, message: ClientMessage
    ) -> None: ...


class CctvServer:
    def __init__(
        self,
        discord: PagePipeline,
        editor: PagePipeline,
        tickets: TicketStore,
        health: HealthSnapshot,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._pipelines = {"discord": discord, "editor": editor}
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
        app.router.add_get("/cctv/discord/ws", self.handle_discord)
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
            "cctv: listening on %s:%d (/cctv/discord/ws, /cctv/editor/ws)",
            host,
            port,
        )
        return True

    async def stop(self) -> None:
        for pipeline in self._pipelines.values():
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

    async def handle_discord(self, request: web.Request) -> web.WebSocketResponse:
        return await self._handle_ws("discord", request)

    async def handle_editor(self, request: web.Request) -> web.WebSocketResponse:
        return await self._handle_ws("editor", request)

    async def _handle_ws(self, page: str, request: web.Request) -> web.WebSocketResponse:
        pipeline = self._pipelines[page]
        socket = web.WebSocketResponse(heartbeat=30.0, max_msg_size=0)
        await socket.prepare(request)
        user_id: int | None = None
        is_editor = pipeline.open_editor
        if page == "discord":
            token = request.query.get("ticket", "")
            user_id = self._tickets.resolve(token) if token else None
            is_editor = (
                await self._authorize_safely(pipeline, user_id) if user_id is not None else False
            )
        pipeline.clients.add(socket, user_id=user_id, is_editor=is_editor)
        self._log.info(
            "cctv/%s: client connected (%d total)",
            page,
            pipeline.clients.client_count,
        )
        try:
            async for incoming in socket:
                if incoming.type != WSMsgType.TEXT:
                    if incoming.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                        break
                    continue
                try:
                    payload = json.loads(incoming.data)
                except (TypeError, json.JSONDecodeError) as exc:
                    self._log.warning("cctv/%s: invalid JSON ignored: %s", page, exc)
                    continue
                await self.handle_payload(page, socket, payload)
        finally:
            pipeline.clients.remove(socket)
        return socket

    async def handle_payload(
        self, page: str, socket: web.WebSocketResponse, payload: object
    ) -> None:
        pipeline = self._pipelines[page]
        try:
            message = parse_client_message(payload)
        except InvalidClientMessageError as exc:
            self._log.warning("cctv/%s: %s", page, exc)
            return
        if message is None:
            return
        if isinstance(message, AuthorizeMessage):
            if page != "discord":
                return
            user_id = self._tickets.resolve(message.ticket)
            if user_id is None:
                return
            pipeline.clients.identify(
                socket,
                user_id,
                is_editor=await self._authorize_safely(pipeline, user_id),
            )
            return
        state = pipeline.clients.get(socket)
        if isinstance(message, _WRITE_MESSAGES) and not (state is not None and state.is_editor):
            self._log.info("cctv/%s: unauthorized %s dropped", page, message.type)
            return
        try:
            await pipeline.handle_message(socket, message)
        except Exception:
            self._log.error("cctv/%s: client message failed", page, exc_info=True)

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


__all__ = ["CctvServer", "HealthSnapshot", "PagePipeline"]
