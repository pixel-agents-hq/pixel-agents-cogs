"""Discord OAuth2 calls for the office Activity: exchanging the
authorization code the Embedded App SDK hands the Activity, and remembering
which user each token this listener signed in belongs to.

Only a sign-in (`api/token`) ever calls Discord: the code exchange, then one
`GET /oauth2/@me` with the fresh token. A ticket request (`api/ticket`) is
answered from the sign-ins remembered here and never calls Discord, so a
bearer this listener didn't hand out costs nothing and can't make Discord
answer 401.

Junk can't block a real user's sign-in, and can't get the bot's address
restricted by Discord:
- nothing is counted or blocked per caller (every Activity user reaches the
  listener through Discord's proxy, from one shared address);
- a code Discord refused is remembered (by SHA-256), so a repeat costs no
  call, and Discord answers a junk code 400, which its invalid-request limit
  doesn't count;
- at most `MAX_CONCURRENT_CALLS` calls run at once (others wait);
- a Discord 429 backs its route off for `Retry-After`;
- as a last guard, once Discord has answered 401, 403 or 429
  `INVALID_ANSWERS_LIMIT` times in ten minutes (a wrong client secret,
  say), no further call is made until that eases -- far under the limit at
  which Discord restricts an address, which would also cut off the bot.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

import aiohttp

DISCORD_API = "https://discord.com/api/v10"
# A signed-in Activity's tickets keep working while it asks for one at least
# this often (every socket reconnect does), up to the token's own expiry.
SESSION_IDLE_SECONDS = 2 * 3600.0
# Discord states each token's lifetime (`expires_in`); without one, this.
DEFAULT_TOKEN_SECONDS = 24 * 3600.0
MAX_TOKEN_SECONDS = 7 * 24 * 3600.0
SESSION_CACHE_SIZE = 4096
# How long a code Discord refused is remembered.
REFUSAL_CACHE_SECONDS = 600.0
REFUSAL_CACHE_SIZE = 4096
MAX_CONCURRENT_CALLS = 8
REQUEST_TIMEOUT_SECONDS = 10.0
# A Discord 429 without a usable Retry-After backs off this long; a stated
# one is honoured up to the cap.
DEFAULT_RETRY_AFTER_SECONDS = 5.0
MAX_RETRY_AFTER_SECONDS = 300.0
# Discord's 401, 403 and 429 answers allowed in INVALID_ANSWERS_WINDOW before
# no further call is made. Discord restricts an address at about 10,000.
INVALID_ANSWERS_LIMIT = 200
INVALID_ANSWERS_WINDOW = 600.0
_INVALID_STATUSES = frozenset({401, 403, 429})
# A token endpoint answering 401/403 means the client id or secret is wrong:
# stop asking for this long.
MISCONFIGURED_BACKOFF_SECONDS = 60.0


class OAuthError(Exception):
    """The call to Discord failed: Discord unreachable, misconfigured, or
    answering oddly."""


class Refused(OAuthError):
    """Discord refused the code (or it was refused before)."""


class RateLimited(OAuthError):
    """Not tried: Discord asked for a back-off, or too many of its answers
    were refusals lately."""


@dataclass(slots=True)
class _Session:
    user_id: int
    idle_until: float
    expires_at: float


def _retry_after(response: aiohttp.ClientResponse, body: object) -> float:
    raw: object = response.headers.get("Retry-After")
    if raw is None and isinstance(body, dict):
        raw = body.get("retry_after")
    try:
        seconds = float(cast("str | float", raw))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS
    return min(max(seconds, 1.0), MAX_RETRY_AFTER_SECONDS)


def _lifetime(body: object) -> float:
    raw = body.get("expires_in") if isinstance(body, dict) else None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return min(float(raw), MAX_TOKEN_SECONDS)
    return DEFAULT_TOKEN_SECONDS


class DiscordOAuth:
    def __init__(
        self,
        *,
        api_base: str = DISCORD_API,
        max_concurrent: int = MAX_CONCURRENT_CALLS,
        invalid_answers_limit: int = INVALID_ANSWERS_LIMIT,
        clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
    ) -> None:
        self._api = api_base.rstrip("/")
        self._clock = clock
        self._calls = asyncio.Semaphore(max_concurrent)
        self._backoff: dict[str, float] = {}
        self._invalid: deque[float] = deque()
        self._invalid_limit = invalid_answers_limit
        self._sessions: OrderedDict[str, _Session] = OrderedDict()
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
        self._sessions.clear()
        self._refused.clear()

    def forget_user(self, user_id: int) -> int:
        """Drop every sign-in of `user_id`."""

        keys = [key for key, entry in self._sessions.items() if entry.user_id == user_id]
        for key in keys:
            del self._sessions[key]
        return len(keys)

    @staticmethod
    def _key(kind: str, value: str) -> str:
        return hashlib.sha256(f"{kind}:{value}".encode()).hexdigest()

    def signed_in_user(self, access_token: str) -> int | None:
        """The user a token this listener signed in belongs to, or None for
        any other token (never asks Discord). Each use keeps the sign-in
        alive for `SESSION_IDLE_SECONDS` more, up to the token's expiry."""

        key = self._key("token", access_token)
        entry = self._sessions.get(key)
        if entry is None:
            return None
        now = self._clock()
        if entry.idle_until <= now or entry.expires_at <= now:
            del self._sessions[key]
            return None
        entry.idle_until = min(now + SESSION_IDLE_SECONDS, entry.expires_at)
        self._sessions.move_to_end(key)
        return entry.user_id

    def _remember(self, access_token: str, user_id: int, lifetime: float) -> None:
        now = self._clock()
        expires_at = now + lifetime
        self._sessions[self._key("token", access_token)] = _Session(
            user_id, min(now + SESSION_IDLE_SECONDS, expires_at), expires_at
        )
        while len(self._sessions) > SESSION_CACHE_SIZE:
            self._sessions.popitem(last=False)

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

    def _too_many_refusals(self) -> bool:
        now = self._clock()
        while self._invalid and self._invalid[0] <= now - INVALID_ANSWERS_WINDOW:
            self._invalid.popleft()
        return len(self._invalid) >= self._invalid_limit

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
            if self._too_many_refusals():
                raise RateLimited("too many refusals from Discord lately")
            try:
                async with self._client().request(
                    method, f"{self._api}{path}", data=data, headers=headers
                ) as response:
                    status = response.status
                    body = await response.json(content_type=None) if status in (200, 429) else None
                    if status in _INVALID_STATUSES:
                        self._invalid.append(self._clock())
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

    async def sign_in(self, client_id: int, client_secret: str, code: str) -> tuple[str, int]:
        """Exchange an authorization code from the SDK's `authorize()` for an
        access token, learn its user, and remember the sign-in:
        `(access token, user id)`. The refresh token is dropped: the
        Activity signs in again on its next launch."""

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
        if status in (401, 403):
            # The application's own credentials were refused: every sign-in
            # would be, so stop asking for a while.
            self._backoff["token"] = self._clock() + MISCONFIGURED_BACKOFF_SECONDS
            self._log.error("cctv/activity: Discord refused the client id or secret")
            raise OAuthError("Discord refused the client id or secret")
        if 400 <= status < 500:
            raise self._refuse(key)
        if status != 200 or not isinstance(token, str) or not token:
            raise OAuthError(f"the token exchange failed ({status})")
        lifetime = _lifetime(body)
        headers = {"Authorization": f"Bearer {token}"}
        status, me = await self._call("me", "GET", "/oauth2/@me", headers=headers)
        user_id = _token_user(me, client_id) if status == 200 else None
        if user_id is None:
            raise OAuthError(f"the new token's check failed ({status})")
        self._remember(token, user_id, lifetime)
        return token, user_id


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
    "INVALID_ANSWERS_LIMIT",
    "MAX_CONCURRENT_CALLS",
    "REFUSAL_CACHE_SECONDS",
    "SESSION_IDLE_SECONDS",
    "DiscordOAuth",
    "OAuthError",
    "RateLimited",
    "Refused",
]
