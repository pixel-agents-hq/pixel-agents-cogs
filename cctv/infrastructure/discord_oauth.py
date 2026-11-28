"""Discord OAuth2 calls for the office Activity: exchanging the
authorization code the Embedded App SDK hands the Activity, and resolving an
access token to the user it belongs to.

Junk can't block a real user's sign-in. Every Activity user reaches the
listener through Discord's proxy, from one shared address, and nothing in a
request names the user before Discord vouches for them, so nothing here is
counted or blocked per caller, and no well-formed request is refused before
Discord has been asked. Instead a code or token Discord refused is
remembered (by SHA-256), so sending it again costs no call; at most
`MAX_CONCURRENT_CALLS` calls run at once (others wait their turn); and when
Discord itself answers 429 the route backs off for its `Retry-After`, the
only time a request is refused untried.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import cast

import aiohttp

DISCORD_API = "https://discord.com/api/v10"
# How long a token's user is remembered, so a signed-in Activity's socket
# reconnects don't call Discord again.
TOKEN_CACHE_SECONDS = 1800.0
TOKEN_CACHE_SIZE = 1024
# How long a code or token Discord refused is remembered.
REFUSAL_CACHE_SECONDS = 600.0
REFUSAL_CACHE_SIZE = 4096
MAX_CONCURRENT_CALLS = 8
REQUEST_TIMEOUT_SECONDS = 10.0
# A Discord 429 without a usable Retry-After backs off this long; a stated
# one is honoured up to the cap.
DEFAULT_RETRY_AFTER_SECONDS = 5.0
MAX_RETRY_AFTER_SECONDS = 300.0


class OAuthError(Exception):
    """The call to Discord failed: Discord unreachable or answering oddly."""


class Refused(OAuthError):
    """Discord refused the code or token (or it was refused before)."""


class RateLimited(OAuthError):
    """Discord asked for a back-off on this route; not tried."""


def _retry_after(response: aiohttp.ClientResponse, body: object) -> float:
    raw: object = response.headers.get("Retry-After")
    if raw is None and isinstance(body, dict):
        raw = body.get("retry_after")
    try:
        seconds = float(cast("str | float", raw))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS
    return min(max(seconds, 1.0), MAX_RETRY_AFTER_SECONDS)


class DiscordOAuth:
    def __init__(
        self,
        *,
        api_base: str = DISCORD_API,
        max_concurrent: int = MAX_CONCURRENT_CALLS,
        clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
    ) -> None:
        self._api = api_base.rstrip("/")
        self._clock = clock
        self._calls = asyncio.Semaphore(max_concurrent)
        self._backoff: dict[str, float] = {}
        self._users: OrderedDict[str, tuple[int, float]] = OrderedDict()
        self._refused: OrderedDict[str, float] = OrderedDict()
        self._session: aiohttp.ClientSession | None = None
        self._log = logger or logging.getLogger(f"red.pixel-agents-cogs.{__name__}")

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
            )
        return self._session

    async def close(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            await session.close()
        self._users.clear()
        self._refused.clear()

    def forget_user(self, user_id: int) -> int:
        """Drop every remembered token of `user_id`."""

        keys = [key for key, (cached, _) in self._users.items() if cached == user_id]
        for key in keys:
            del self._users[key]
        return len(keys)

    @staticmethod
    def _key(kind: str, value: str) -> str:
        return hashlib.sha256(f"{kind}:{value}".encode()).hexdigest()

    def _refused_before(self, key: str) -> bool:
        expires = self._refused.get(key)
        if expires is None:
            return False
        if expires <= self._clock():
            del self._refused[key]
            return False
        return True

    def _refuse(self, key: str) -> Refused:
        self._refused[key] = self._clock() + REFUSAL_CACHE_SECONDS
        self._refused.move_to_end(key)
        while len(self._refused) > REFUSAL_CACHE_SIZE:
            self._refused.popitem(last=False)
        return Refused("Discord refused it")

    async def _call(
        self,
        route: str,
        method: str,
        path: str,
        *,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, object]:
        """One call to Discord: `(status, JSON body or None)`."""

        async with self._calls:
            if self._backoff.get(route, 0.0) > self._clock():
                raise RateLimited(f"Discord asked to back off {route}")
            try:
                async with self._client().request(
                    method, f"{self._api}{path}", data=data, headers=headers
                ) as response:
                    status = response.status
                    body = await response.json(content_type=None) if status in (200, 429) else None
                    if status == 429:
                        retry = _retry_after(response, body)
                        self._backoff[route] = self._clock() + retry
                        self._log.warning(
                            "cctv/activity: Discord asked to wait %.0fs (%s)", retry, route
                        )
                        raise RateLimited(f"Discord asked to back off {route}")
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                raise OAuthError(f"the call to Discord failed: {type(exc).__name__}") from exc
        return status, body

    async def exchange_code(self, client_id: int, client_secret: str, code: str) -> str:
        """The access token for an authorization code from the SDK's
        `authorize()`. The refresh token is dropped: the Activity signs in
        again on its next launch."""

        key = self._key("code", code)
        if self._refused_before(key):
            raise Refused("this code was refused before")
        form = {
            "client_id": str(client_id),
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
        }
        status, body = await self._call("token", "POST", "/oauth2/token", data=form)
        token = body.get("access_token") if isinstance(body, dict) else None
        if status == 200 and isinstance(token, str) and token:
            return token
        if 400 <= status < 500:
            raise self._refuse(key)
        raise OAuthError(f"the token exchange failed ({status})")

    async def user_id(self, access_token: str, client_id: int) -> int:
        """The user `access_token` signs in, checked with Discord. Only a
        token issued to `client_id` (this bot's application) is accepted, so
        a token from another application can't stand in for its user."""

        key = self._key("token", access_token)
        cached = self._users.get(key)
        if cached is not None and cached[1] > self._clock():
            self._users.move_to_end(key)
            return cached[0]
        if self._refused_before(key):
            raise Refused("this token was refused before")
        headers = {"Authorization": f"Bearer {access_token}"}
        status, body = await self._call("me", "GET", "/oauth2/@me", headers=headers)
        if status >= 500:
            raise OAuthError(f"the token check failed ({status})")
        user_id = _token_user(body, client_id) if status == 200 else None
        if user_id is None:
            raise self._refuse(key)
        self._users[key] = (user_id, self._clock() + TOKEN_CACHE_SECONDS)
        while len(self._users) > TOKEN_CACHE_SIZE:
            self._users.popitem(last=False)
        return user_id


def _snowflake(raw: object) -> int | None:
    text = str(raw) if isinstance(raw, (str, int)) and not isinstance(raw, bool) else ""
    return int(text) if text.isdecimal() and 0 < len(text) <= 20 else None


def _token_user(body: object, client_id: int) -> int | None:
    """The user id of a `GET /oauth2/@me` reply for a token of `client_id`."""

    if not isinstance(body, dict):
        return None
    application = body.get("application")
    user = body.get("user")
    if not isinstance(application, dict) or not isinstance(user, dict):
        return None
    if _snowflake(cast(object, application.get("id"))) != client_id:
        return None
    return _snowflake(cast(object, user.get("id")))


__all__ = [
    "DISCORD_API",
    "MAX_CONCURRENT_CALLS",
    "REFUSAL_CACHE_SECONDS",
    "TOKEN_CACHE_SECONDS",
    "DiscordOAuth",
    "OAuthError",
    "RateLimited",
    "Refused",
]
