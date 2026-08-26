"""Connected-socket registry and message delivery for architect's own
office WebSocket.

A deliberate parallel copy of `floorplan/infrastructure/client_hub.py`'s
`ClientHub`, pared down to bare connection tracking -- no user identity, no
editor-authorization state, since nothing reaching this socket is
authorized to mutate the layout yet (edits arrive only through future
Discord commands/tools; see docs/architect-design.md). If an in-browser
editor is ever added here, floorplan's own `ClientState`/identify() shape
is the reference to extend towards, not reinvent from scratch.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

from aiohttp import web


class ClientHub:
    """Own connected clients and isolate delivery failures per socket."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self.clients: set[web.WebSocketResponse] = set()
        self._log = logger or logging.getLogger(__name__)

    @property
    def client_count(self) -> int:
        return len(self.clients)

    def add(self, socket: web.WebSocketResponse) -> None:
        self.clients.add(socket)

    def remove(self, socket: web.WebSocketResponse) -> None:
        self.clients.discard(socket)

    async def send_to(self, socket: web.WebSocketResponse, message: Mapping[str, object]) -> bool:
        """Serialize and send a message to one socket without leaking failures."""

        try:
            payload = json.dumps(message)
        except (TypeError, ValueError) as exc:
            self._log.error(
                "architect: refusing to send unserializable %s: %s", message.get("type"), exc
            )
            return False
        if socket.closed:
            self.remove(socket)
            return False
        try:
            await socket.send_str(payload)
        except Exception as exc:
            self._log.debug("architect: send error: %s", exc)
            self.remove(socket)
            return False
        return True

    async def broadcast(self, message: Mapping[str, object]) -> int:
        """Serialize once and broadcast, pruning closed or failed clients."""

        if not self.clients:
            return 0
        try:
            payload = json.dumps(message)
        except (TypeError, ValueError) as exc:
            self._log.error(
                "architect: refusing to broadcast unserializable %s: %s",
                message.get("type"),
                exc,
            )
            return 0

        sent = 0
        for socket in list(self.clients):
            if socket.closed:
                self.remove(socket)
                continue
            try:
                await socket.send_str(payload)
            except Exception as exc:
                self._log.debug("architect: broadcast error: %s", exc)
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
                self._log.debug("architect: client close error: %s", exc)
        self.clients.clear()


__all__ = ["ClientHub"]
