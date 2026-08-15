"""Identity-aware office WebSocket client registry and message delivery."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from aiohttp import web

Authorize = Callable[[int], Awaitable[bool]]


@dataclass(slots=True)
class ClientState:
    """Runtime identity and editor authorization for one connected socket."""

    socket: web.WebSocketResponse
    user_id: int | None = None
    is_editor: bool = False


class ClientHub:
    """Own connected clients and isolate delivery failures per socket."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self.clients: dict[web.WebSocketResponse, ClientState] = {}
        self._log = logger or logging.getLogger(__name__)

    @property
    def client_count(self) -> int:
        return len(self.clients)

    @property
    def editor_count(self) -> int:
        return sum(state.is_editor for state in self.clients.values())

    def add(
        self,
        socket: web.WebSocketResponse,
        *,
        user_id: int | None = None,
        is_editor: bool = False,
    ) -> ClientState:
        state = ClientState(socket=socket, user_id=user_id, is_editor=is_editor)
        self.clients[socket] = state
        return state

    def remove(self, socket: web.WebSocketResponse) -> ClientState | None:
        return self.clients.pop(socket, None)

    def get(self, socket: web.WebSocketResponse) -> ClientState | None:
        return self.clients.get(socket)

    def identify(self, socket: web.WebSocketResponse, user_id: int, *, is_editor: bool) -> None:
        state = self.clients.get(socket)
        if state is None:
            self.add(socket, user_id=user_id, is_editor=is_editor)
            return
        state.user_id = user_id
        state.is_editor = is_editor

    async def send_to(self, socket: web.WebSocketResponse, message: Mapping[str, object]) -> bool:
        """Serialize and send a message to one socket without leaking failures."""

        try:
            payload = json.dumps(message)
        except (TypeError, ValueError) as exc:
            self._log.error(
                "pixelagents: refusing to send unserializable %s: %s", message.get("type"), exc
            )
            return False
        if socket.closed:
            self.remove(socket)
            return False
        try:
            await socket.send_str(payload)
        except Exception as exc:
            self._log.debug("pixelagents: send error: %s", exc)
            self.remove(socket)
            return False
        return True

    async def broadcast(
        self,
        message: Mapping[str, object],
        *,
        exclude: web.WebSocketResponse | None = None,
    ) -> int:
        """Serialize once and broadcast, pruning closed or failed clients."""

        if not self.clients:
            return 0
        try:
            payload = json.dumps(message)
        except (TypeError, ValueError) as exc:
            self._log.error(
                "pixelagents: refusing to broadcast unserializable %s: %s",
                message.get("type"),
                exc,
            )
            return 0

        sent = 0
        for socket in list(self.clients):
            if socket is exclude:
                continue
            if socket.closed:
                self.remove(socket)
                continue
            try:
                await socket.send_str(payload)
            except Exception as exc:
                self._log.debug("pixelagents: broadcast error: %s", exc)
                self.remove(socket)
            else:
                sent += 1
        return sent

    async def close_all(self) -> None:
        """Close every socket, isolating errors, then empty the registry."""

        for socket in list(self.clients):
            try:
                await socket.close()
            except Exception as exc:
                self._log.debug("pixelagents: client close error: %s", exc)
        self.clients.clear()

    async def reauthorize(self, authorize: Authorize) -> None:
        """Refresh editor state for identified clients; viewers stay anonymous."""

        for state in list(self.clients.values()):
            if state.user_id is None:
                continue
            try:
                state.is_editor = await authorize(state.user_id)
            except Exception as exc:
                # A failed authorization refresh must fail closed.
                state.is_editor = False
                self._log.error(
                    "pixelagents: could not reauthorize office user %d: %s",
                    state.user_id,
                    exc,
                )
