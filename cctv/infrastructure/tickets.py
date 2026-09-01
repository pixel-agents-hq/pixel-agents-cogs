"""Short-lived Dashboard identity tickets for the Discord page."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

TICKET_TTL_SECONDS = 8 * 60 * 60


@dataclass(frozen=True, slots=True)
class Ticket:
    user_id: int
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

    def mint(self, user_id: int) -> str:
        now = self._clock()
        self.cleanup(now)
        token = self._token_factory()
        if not token:
            raise ValueError("ticket factory returned an empty token")
        self._tickets[token] = Ticket(user_id, now + self._ttl)
        return token

    def resolve(self, token: str) -> int | None:
        ticket = self._tickets.get(token)
        if ticket is None:
            return None
        if ticket.expires_at <= self._clock():
            del self._tickets[token]
            return None
        return ticket.user_id

    def cleanup(self, now: float | None = None) -> int:
        deadline = self._clock() if now is None else now
        expired = [token for token, value in self._tickets.items() if value.expires_at <= deadline]
        for token in expired:
            del self._tickets[token]
        return len(expired)


__all__ = ["TICKET_TTL_SECONDS", "Ticket", "TicketStore"]
