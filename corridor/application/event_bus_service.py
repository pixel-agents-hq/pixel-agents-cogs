"""In-process pub/sub for corridor's Discord-vocabulary agent events.

Depends on nothing but stdlib -- no discord.py, no redbot -- the same
pattern permission_service.py/reply_service.py already follow. See
docs/corridor-pubsub-design.md for the full design rationale.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

log = logging.getLogger("red.corridor")

_EventT = TypeVar("_EventT")


class EventBusService:
    """Delivery is synchronous, awaited dispatch with per-subscriber error
    isolation -- mirrors floorplan/infrastructure/client_hub.py's ClientHub
    isolating one broadcast failure per socket, so a raising subscriber
    never breaks a publisher's own turn. Scoped to one bot process, not one
    guild -- guild scoping lives on each event's AgentRef, not here."""

    def __init__(self) -> None:
        self._subscribers: dict[type, list[tuple[str, Callable[[Any], Awaitable[None]]]]] = (
            defaultdict(list)
        )

    async def publish(self, event: object, *, subscriber_timeout: float | None = None) -> None:
        """Await each subscriber registered for `type(event)`, in
        registration order, each isolated in its own try/except -- a
        raising handler is logged and dropped, never propagated here."""

        for owner, handler in list(self._subscribers.get(type(event), ())):
            try:
                if subscriber_timeout is None:
                    await handler(event)
                else:
                    await asyncio.wait_for(handler(event), timeout=subscriber_timeout)
            except TimeoutError:
                log.error(
                    "corridor: %s's handler for %s exceeded %.1fs -- cancelled and dropped",
                    owner,
                    type(event).__name__,
                    subscriber_timeout,
                )
            except Exception:
                log.exception(
                    "corridor: %s's handler for %s raised -- dropped, not propagated"
                    " to the publisher",
                    owner,
                    type(event).__name__,
                )

    def subscribe(
        self,
        event_type: type[_EventT],
        handler: Callable[[_EventT], Awaitable[None]],
        *,
        owner: str,
    ) -> None:
        """Register `handler` for exactly `event_type` -- dispatch is by
        concrete class, never a subclass/string match. `owner` identifies
        the subscribing cog; `unsubscribe_owner` drops every handler it
        registered, across every event type, in one call."""

        self._subscribers[event_type].append((owner, handler))

    def unsubscribe_owner(self, owner: str) -> None:
        """The subscriber's own responsibility, called from its own
        cog_unload -- the reverse direction of
        register_dependent/unregister_dependent (corridor does not
        track/cascade a subscriber's lifecycle the way it does the
        opposite direction for a dependent cog)."""

        for handlers in self._subscribers.values():
            handlers[:] = [(o, h) for o, h in handlers if o != owner]
