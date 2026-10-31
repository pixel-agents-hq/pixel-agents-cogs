"""Atomic persistence, watch, and notification for office aggregates."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Protocol, TypeVar

from ..domain import (
    OfficeRef,
    OfficeState,
    OfficeStateChanged,
    OfficeStateKind,
    RawLayout,
    SeatRecords,
    copy_office_state,
)
from .event_bus_service import DEFAULT_SUBSCRIBER_TIMEOUT, EventBusService

# Kept as its own name (rather than importing DEFAULT_SUBSCRIBER_TIMEOUT
# directly at each call site below) since corridor/tests/test_office_state_service.py
# patches this exact module attribute.
OFFICE_STATE_SUBSCRIBER_TIMEOUT = DEFAULT_SUBSCRIBER_TIMEOUT
OfficeStateHandler = Callable[[OfficeStateChanged], Awaitable[None]]
MutationResult = TypeVar("MutationResult")


class OfficeStateStorage(Protocol):
    async def state(self, ref: OfficeRef) -> OfficeState | None: ...
    async def save(self, state: OfficeState) -> None: ...


class OfficeStateNotInitializedError(RuntimeError):
    pass


class OfficeStateService:
    """A per-aggregate lock makes each aggregate's subscribe+snapshot and
    field writes atomic, without serializing independent aggregates (the
    editor office, and each guild's Discord office) against each other."""

    def __init__(self, storage: OfficeStateStorage, events: EventBusService) -> None:
        self._storage = storage
        self._events = events
        self._locks: dict[OfficeRef, asyncio.Lock] = {}

    def _lock(self, ref: OfficeRef) -> asyncio.Lock:
        lock = self._locks.get(ref)
        if lock is None:
            lock = self._locks[ref] = asyncio.Lock()
        return lock

    async def state(self, ref: OfficeRef) -> OfficeState | None:
        async with self._lock(ref):
            state = await self._storage.state(ref)
            return copy_office_state(state) if state is not None else None

    async def initialize(self, ref: OfficeRef, layout: RawLayout) -> OfficeState:
        changed: OfficeState | None = None
        async with self._lock(ref):
            current = await self._storage.state(ref)
            if current is None:
                current = OfficeState(
                    kind=ref.kind,
                    layout=deepcopy(layout),
                    seats={},
                    revision=1,
                    guild_id=ref.guild_id,
                )
                await self._storage.save(current)
                changed = current
            result = copy_office_state(current)
        if changed is not None:
            await self._publish(changed)
        return result

    async def set_layout(self, ref: OfficeRef, layout: RawLayout) -> OfficeState:
        async with self._lock(ref):
            current = await self._required(ref)
            updated = OfficeState(
                kind=ref.kind,
                guild_id=ref.guild_id,
                layout=deepcopy(layout),
                seats=deepcopy(current.seats),
                revision=current.revision + 1,
            )
            await self._storage.save(updated)
            result = copy_office_state(updated)
        await self._publish(updated)
        return result

    async def set_seats(self, ref: OfficeRef, seats: SeatRecords) -> OfficeState:
        async with self._lock(ref):
            current = await self._required(ref)
            updated = OfficeState(
                kind=ref.kind,
                guild_id=ref.guild_id,
                layout=deepcopy(current.layout),
                seats=deepcopy(seats),
                revision=current.revision + 1,
            )
            await self._storage.save(updated)
            result = copy_office_state(updated)
        await self._publish(updated)
        return result

    async def mutate_seats(
        self,
        ref: OfficeRef,
        mutation: Callable[[SeatRecords], MutationResult],
    ) -> tuple[OfficeState, MutationResult]:
        async with self._lock(ref):
            current = await self._required(ref)
            seats = deepcopy(current.seats)
            mutation_result = mutation(seats)
            updated = OfficeState(
                kind=ref.kind,
                guild_id=ref.guild_id,
                layout=deepcopy(current.layout),
                seats=seats,
                revision=current.revision + 1,
            )
            await self._storage.save(updated)
            result = copy_office_state(updated)
        await self._publish(updated)
        return result, mutation_result

    async def watch(
        self,
        kind: OfficeStateKind,
        handler: OfficeStateHandler,
        *,
        owner: str,
    ) -> OfficeState | None:
        """Subscribe `handler` to every aggregate of `kind`. For the editor
        office -- the only one of its kind -- the current snapshot is
        captured under its lock with the subscription, so no write can land
        unobserved in between. Discord offices are one per guild, so none is
        returned: a subscriber subscribes first, then reads each guild's
        office it needs (`state`), and a write landing in between arrives
        as an event with a higher revision."""

        async def filtered(event: OfficeStateChanged) -> None:
            if event.state.kind == kind:
                await handler(OfficeStateChanged(state=copy_office_state(event.state)))

        if kind != OfficeStateKind.EDITOR:
            self._events.subscribe(OfficeStateChanged, filtered, owner=owner)
            return None
        ref = OfficeRef.editor()
        async with self._lock(ref):
            self._events.subscribe(OfficeStateChanged, filtered, owner=owner)
            state = await self._storage.state(ref)
            return copy_office_state(state) if state is not None else None

    async def _required(self, ref: OfficeRef) -> OfficeState:
        state = await self._storage.state(ref)
        if state is None:
            raise OfficeStateNotInitializedError(
                f"{ref.describe()} office state has not been initialized"
            )
        return state

    async def _publish(self, state: OfficeState) -> None:
        await self._events.publish(
            OfficeStateChanged(state=copy_office_state(state)),
            subscriber_timeout=OFFICE_STATE_SUBSCRIBER_TIMEOUT,
        )


__all__ = [
    "OFFICE_STATE_SUBSCRIBER_TIMEOUT",
    "OfficeStateHandler",
    "OfficeStateNotInitializedError",
    "OfficeStateService",
    "OfficeStateStorage",
]
