"""Short-lived, reusable editor tickets for the Discord dashboard page.

Ported verbatim from floorplan's former `infrastructure/tickets.py` (now
retired there, see docs/cctv-design.md). Only the Discord pipeline uses
this -- the editor pipeline has no ticket/authorization concept at all,
mirroring architect's former dashboard (docs/cctv-design.md §2.7).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

TICKET_TTL_SECONDS = 8 * 60 * 60


@dataclass(frozen=True, slots=True)
class Ticket:
    """A ticket's Discord identity and monotonic expiry deadline."""

    user_id: int
    expires_at: float


class TicketStore:
    """Mint and resolve reusable tickets bound to a Discord user ID."""

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

    @property
    def tickets(self) -> Mapping[str, Ticket]:
        """A read-only view used by diagnostics and focused tests."""

        return MappingProxyType(self._tickets)

    def mint(self, user_id: int) -> str:
        """Mint a ticket and opportunistically remove expired entries."""

        now = self._clock()
        self.cleanup(now=now)
        ticket = self._token_factory()
        if not ticket:
            raise ValueError("ticket factory returned an empty token")
        self._tickets[ticket] = Ticket(user_id=user_id, expires_at=now + self._ttl)
        return ticket

    def resolve(self, ticket: str) -> int | None:
        """Return the bound user while the reusable ticket remains valid."""

        entry = self._tickets.get(ticket)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            del self._tickets[ticket]
            return None
        return entry.user_id

    def cleanup(self, *, now: float | None = None) -> int:
        """Remove expired entries and return how many were discarded."""

        deadline = self._clock() if now is None else now
        expired = [token for token, entry in self._tickets.items() if entry.expires_at <= deadline]
        for token in expired:
            del self._tickets[token]
        return len(expired)


__all__ = ["TICKET_TTL_SECONDS", "Ticket", "TicketStore"]
