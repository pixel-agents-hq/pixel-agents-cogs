"""Locked, atomic office-state mutation plus a five-second-bounded
`OfficeStateChanged` delivery loop.

Deliberately NOT built on `EventBusService`: the existing six-event
`Agent*` delivery path must stay byte-for-byte unchanged (see
docs/cctv-design.md §2.2/§2.5), and this service's differing
timeout/cancellation semantics would otherwise have to be bolted onto
that shared class for every event type, not just office-state. Two
independent dispatch mechanisms, sharing nothing but the general shape
(synchronous, awaited, per-subscriber isolated).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TypeVar

from ..domain.office_state import OfficeState, OfficeStateChanged, OfficeStateKind
from ..infrastructure.office_state_repository import RedOfficeStateRepository

log = logging.getLogger("red.corridor")

MutationResult = TypeVar("MutationResult")

# Scoped only to office-state delivery -- EventBusService's own Agent*
# delivery gets no timeout at all, unchanged, per docs/cctv-design.md §2.5.
PUBLISH_TIMEOUT_SECONDS = 5.0

_KINDS: tuple[OfficeStateKind, ...] = ("discord", "editor")


class OfficeStateService:
    """One `asyncio.Lock` per kind, held across both mutation (a real
    Config-I/O read-modify-write) and `watch`'s "register handler + read
    current snapshot" -- this is the concrete meaning of "atomic
    watch-and-snapshot" for office state: without the lock, a mutation
    could land in the real `await` gap between registering a subscriber
    and reading its starting snapshot, and that subscriber would never
    see it (docs/cctv-design.md §2.2/§2.4, and the confirmed cold-start
    gap in docs/cctv-design.md §1.4 this closes)."""

    def __init__(
        self, repository: RedOfficeStateRepository, *, logger: logging.Logger | None = None
    ) -> None:
        self._repository = repository
        self._log = logger or log
        self._locks: dict[OfficeStateKind, asyncio.Lock] = {kind: asyncio.Lock() for kind in _KINDS}
        self._subscribers: dict[
            OfficeStateKind, list[tuple[str, Callable[[OfficeStateChanged], Awaitable[None]]]]
        ] = defaultdict(list)

    async def read(self, kind: OfficeStateKind) -> OfficeState:
        async with self._locks[kind]:
            return await self._repository.get_or_create(kind)

    async def watch(
        self,
        kind: OfficeStateKind,
        handler: Callable[[OfficeStateChanged], Awaitable[None]],
        *,
        owner: str,
    ) -> OfficeState:
        """Atomically register `handler` for `kind`'s `OfficeStateChanged`
        delivery and return its current snapshot. `owner` identifies the
        subscribing cog; `unwatch_owner` drops every handler it
        registered, across every kind, in one call -- same convention as
        `EventBusService.subscribe`/`unsubscribe_owner`."""

        async with self._locks[kind]:
            self._subscribers[kind].append((owner, handler))
            return await self._repository.get_or_create(kind)

    def unwatch_owner(self, owner: str) -> None:
        for handlers in self._subscribers.values():
            handlers[:] = [(o, h) for o, h in handlers if o != owner]

    async def set_layout(self, kind: OfficeStateKind, layout: dict[str, object]) -> OfficeState:
        async with self._locks[kind]:
            updated = await self._repository.set_layout(kind, layout)
        await self._publish(kind, updated)
        return updated

    async def mutate_seats(
        self,
        kind: OfficeStateKind,
        mutation: Callable[[dict[str, dict[str, object]]], MutationResult],
    ) -> tuple[OfficeState, MutationResult]:
        async with self._locks[kind]:
            updated, result = await self._repository.mutate_seats(kind, mutation)
        await self._publish(kind, updated)
        return updated, result

    async def set_layout_if_empty(
        self, kind: OfficeStateKind, layout: dict[str, object]
    ) -> OfficeState:
        """Atomically seed `kind`'s `layout` only if it is still blank --
        the check and the write happen under the same lock hold, unlike a
        caller doing its own `read()` then conditionally calling
        `set_layout()`, which has a real `await` gap between them: a
        concurrent, genuine write could land in that gap and then be
        clobbered by this stale seed. Returns the current aggregate
        unchanged (no publish) if something had already seeded it by the
        time this acquired the lock."""

        async with self._locks[kind]:
            current = await self._repository.get_or_create(kind)
            if current.layout:
                return current
            updated = await self._repository.set_layout(kind, layout)
        await self._publish(kind, updated)
        return updated

    async def _publish(self, kind: OfficeStateKind, state: OfficeState) -> None:
        """Synchronous, awaited dispatch with per-subscriber isolation,
        like `EventBusService.publish` -- but each subscriber is
        additionally bounded to `PUBLISH_TIMEOUT_SECONDS`: a stuck
        subscriber is cancelled and logged, never blocks the writer (this
        runs after persistence already succeeded, see
        docs/cctv-design.md §1.5/§2.5) or any other subscriber. Callers
        invoke this only after releasing `kind`'s lock (see
        docs/cctv-design.md §2.5's delivery rules: persist, release,
        *then* deliver) -- holding the lock across delivery would let one
        slow subscriber block every other reader/writer of `kind` for up
        to `PUBLISH_TIMEOUT_SECONDS`, and would deadlock a subscriber that
        itself reads or writes `kind` from inside its own handler."""

        event = OfficeStateChanged(state=state)
        for owner, handler in list(self._subscribers.get(kind, ())):
            try:
                await asyncio.wait_for(handler(event), timeout=PUBLISH_TIMEOUT_SECONDS)
            except TimeoutError:
                self._log.error(
                    "corridor: %s's office-state handler for kind=%r exceeded %.0fs -- cancelled",
                    owner,
                    kind,
                    PUBLISH_TIMEOUT_SECONDS,
                )
            except Exception:
                self._log.exception(
                    "corridor: %s's office-state handler for kind=%r raised -- dropped,"
                    " not propagated to the writer",
                    owner,
                    kind,
                )


__all__ = ["PUBLISH_TIMEOUT_SECONDS", "OfficeStateService"]
