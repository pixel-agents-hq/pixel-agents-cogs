"""ASGI middleware for a listener only the bot's own cogs may use.

Corridor's A2A listener and suggestionbox's MCP listener both wrap their
app in `BearerTokenGuard`: every HTTP request must carry `Authorization:
Bearer <token>`, a random token the owning cog mints on each load and
hands only to in-process callers. Anything else that can reach the port
(another process on the host, or more when the bind host is not loopback)
gets a 401. Request bodies over `max_bytes` are refused too.
"""

from __future__ import annotations

import hmac
import secrets

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def new_token() -> str:
    """A fresh listener token."""

    return secrets.token_urlsafe(32)


def bearer_headers(token: str) -> dict[str, str]:
    """The header a caller sends with every request to a guarded listener."""

    return {"Authorization": f"Bearer {token}"}


class _RequestTooLarge(Exception):
    pass


class BearerTokenGuard:
    """Refuse any HTTP request without the bearer token (401) or with a
    body over `max_bytes` (413, or the connection is dropped once a body
    without a `Content-Length` grows past it). Other ASGI scopes
    (lifespan) pass through."""

    def __init__(self, app: ASGIApp, *, token: str, max_bytes: int) -> None:
        self._app = app
        self._expected = f"Bearer {token}".encode()
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or ())
        if not hmac.compare_digest(headers.get(b"authorization", b""), self._expected):
            await PlainTextResponse("unauthorized", status_code=401)(scope, receive, send)
            return
        length = headers.get(b"content-length", b"")
        if length.isdigit() and int(length) > self._max_bytes:
            await PlainTextResponse("request too large", status_code=413)(scope, receive, send)
            return
        received = 0

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    raise _RequestTooLarge
            return message

        await self._app(scope, bounded_receive, send)


__all__ = ["BearerTokenGuard", "bearer_headers", "new_token"]
