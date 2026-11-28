"""Discord OAuth2 calls for the office Activity: exchanging the
authorization code the Embedded App SDK hands the Activity, and resolving an
access token to the user it belongs to."""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import cast

import aiohttp

DISCORD_API = "https://discord.com/api/v10"
# How long a token's user is remembered, so each socket's ticket request
# doesn't call Discord again.
TOKEN_CACHE_SECONDS = 300.0
TOKEN_CACHE_SIZE = 1024
REQUEST_TIMEOUT_SECONDS = 10.0
# Calls to Discord per minute from the Activity endpoints, across all users,
# so they can't be used to spend the bot application's OAuth rate limit.
CALLS_PER_MINUTE = 120


class OAuthError(Exception):
    """Discord refused the code or token, or couldn't be reached."""


class RateLimited(OAuthError):
    """Too many calls to Discord from the Activity endpoints."""


class SlidingWindow:
    def __init__(
        self, limit: int, per: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._per = per
        self._clock = clock
        self._calls: deque[float] = deque()

    def allow(self) -> bool:
        now = self._clock()
        while self._calls and self._calls[0] <= now - self._per:
            self._calls.popleft()
        if len(self._calls) >= self._limit:
            return False
        self._calls.append(now)
        return True


class DiscordOAuth:
    def __init__(
        self,
        *,
        api_base: str = DISCORD_API,
        calls_per_minute: int = CALLS_PER_MINUTE,
        clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
    ) -> None:
        self._api = api_base.rstrip("/")
        self._clock = clock
        self._window = SlidingWindow(calls_per_minute, 60.0, clock=clock)
        self._users: OrderedDict[str, tuple[int, float]] = OrderedDict()
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

    def forget_user(self, user_id: int) -> int:
        """Drop every remembered token of `user_id`."""

        keys = [key for key, (cached, _) in self._users.items() if cached == user_id]
        for key in keys:
            del self._users[key]
        return len(keys)

    def _spend(self) -> None:
        if not self._window.allow():
            raise RateLimited("too many Activity sign-ins right now")

    async def exchange_code(self, client_id: int, client_secret: str, code: str) -> str:
        """The access token for an authorization code from the SDK's
        `authorize()`. The refresh token is dropped: the Activity signs in
        again on its next launch."""

        self._spend()
        form = {
            "client_id": str(client_id),
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
        }
        try:
            async with self._client().post(f"{self._api}/oauth2/token", data=form) as response:
                body = await response.json(content_type=None) if response.status == 200 else None
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise OAuthError(f"the token exchange failed: {type(exc).__name__}") from exc
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise OAuthError("Discord refused the authorization code")
        return token

    async def user_id(self, access_token: str, client_id: int) -> int:
        """The user `access_token` signs in, checked with Discord. Only a
        token issued to `client_id` (this bot's application) is accepted, so
        a token from another application can't stand in for its user."""

        key = hashlib.sha256(access_token.encode()).hexdigest()
        cached = self._users.get(key)
        now = self._clock()
        if cached is not None and cached[1] > now:
            self._users.move_to_end(key)
            return cached[0]
        self._spend()
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            async with self._client().get(f"{self._api}/oauth2/@me", headers=headers) as response:
                body = await response.json(content_type=None) if response.status == 200 else None
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise OAuthError(f"the token check failed: {type(exc).__name__}") from exc
        user_id = _token_user(body, client_id)
        if user_id is None:
            raise OAuthError("Discord didn't accept the access token")
        self._users[key] = (user_id, now + TOKEN_CACHE_SECONDS)
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
    "CALLS_PER_MINUTE",
    "DISCORD_API",
    "DiscordOAuth",
    "OAuthError",
    "RateLimited",
    "SlidingWindow",
    "TOKEN_CACHE_SECONDS",
]
