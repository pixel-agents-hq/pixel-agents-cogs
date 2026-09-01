"""Identity-aware client registry for one CCTV page."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from aiohttp import web

Authorize = Callable[[int], Awaitable[bool]]


@dataclass(slots=True)
class ClientState:
    socket: web.WebSocketResponse
    user_id: int | None = None
    is_editor: bool = False


class ClientHub:
    def __init__(self, page: str, *, logger: logging.Logger | None = None) -> None:
        self.page = page
        self.clients: dict[web.WebSocketResponse, ClientState] = {}
        self._log = logger or logging.getLogger(__name__)

    @property
    def client_count(self) -> int:
        return len(self.clients)

    @property
    def editor_count(self) -> int:
        return sum(client.is_editor for client in self.clients.values())

    def add(
        self,
        socket: web.WebSocketResponse,
        *,
        user_id: int | None = None,
        is_editor: bool = False,
    ) -> ClientState:
        state = ClientState(socket, user_id, is_editor)
        self.clients[socket] = state
        return state

    def remove(self, socket: web.WebSocketResponse) -> None:
        self.clients.pop(socket, None)

    def get(self, socket: web.WebSocketResponse) -> ClientState | None:
        return self.clients.get(socket)

    def identify(self, socket: web.WebSocketResponse, user_id: int, *, is_editor: bool) -> None:
        state = self.clients.get(socket)
        if state is None:
            self.add(socket, user_id=user_id, is_editor=is_editor)
        else:
            state.user_id = user_id
            state.is_editor = is_editor

    async def send_to(self, socket: web.WebSocketResponse, message: Mapping[str, object]) -> bool:
        try:
            payload = json.dumps(message)
        except (TypeError, ValueError) as exc:
            self._log.error("cctv/%s: unserializable message: %s", self.page, exc)
            return False
        if socket.closed:
            self.remove(socket)
            return False
        try:
            await socket.send_str(payload)
        except Exception as exc:
            self._log.debug("cctv/%s: socket send failed: %s", self.page, exc)
            self.remove(socket)
            return False
        return True

    async def broadcast(
        self,
        message: Mapping[str, object],
        *,
        exclude: web.WebSocketResponse | None = None,
    ) -> int:
        if not self.clients:
            return 0
        sent = 0
        for socket in list(self.clients):
            if socket is exclude:
                continue
            sent += await self.send_to(socket, message)
        return sent

    async def reauthorize(self, authorize: Authorize) -> None:
        for state in list(self.clients.values()):
            if state.user_id is None:
                continue
            try:
                state.is_editor = await authorize(state.user_id)
            except Exception as exc:
                state.is_editor = False
                self._log.error(
                    "cctv/%s: authorization refresh failed for %d: %s",
                    self.page,
                    state.user_id,
                    exc,
                )

    async def close_all(self) -> None:
        for socket in list(self.clients):
            try:
                await socket.close()
            except Exception as exc:
                self._log.debug("cctv/%s: socket close failed: %s", self.page, exc)
        self.clients.clear()


__all__ = ["Authorize", "ClientHub", "ClientState"]
