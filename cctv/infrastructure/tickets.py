"""Short-lived, single-use Dashboard identity tickets for guild offices.

A ticket proves which Dashboard user opened a guild's office page: the
Dashboard session (a login cookie the browser never hands to the
WebSocket) mints one through `/third-party/cctv/session?guild=<id>`, and
the page's socket presents it in-band (`{"type": "authorize"}`), never in a
URL. It is bound to the one guild it was minted for, lives a minute, and is
consumed the first time it is presented, so a leaked ticket is worth at
most one connection to one office for its owner.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

TICKET_TTL_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class Ticket:
    user_id: int
    guild_id: int
    expires_at: float


class TicketStore:
    def __init__(
        self,
        *,
        ttl: float = TICKET_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if ttl <= 0:
            raise ValueError("ticket TTL must be positive")
        self._ttl = ttl
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._tickets: dict[str, Ticket] = {}

    def mint(self, user_id: int, guild_id: int) -> str:
        now = self._clock()
        self.cleanup(now)
        token = self._token_factory()
        if not token:
            raise ValueError("ticket factory returned an empty token")
        self._tickets[token] = Ticket(user_id, guild_id, now + self._ttl)
        return token

    def consume(self, token: str, guild_id: int) -> int | None:
        """The user a ticket was minted for, if it is valid for `guild_id`.
        Any presented ticket is spent, whether or not it was valid here."""

        ticket = self._tickets.pop(token, None) if token else None
        if ticket is None or ticket.expires_at <= self._clock():
            return None
        return ticket.user_id if ticket.guild_id == guild_id else None

    def cleanup(self, now: float | None = None) -> int:
        deadline = self._clock() if now is None else now
        expired = [token for token, value in self._tickets.items() if value.expires_at <= deadline]
        for token in expired:
            del self._tickets[token]
        return len(expired)


__all__ = ["TICKET_TTL_SECONDS", "Ticket", "TicketStore"]
