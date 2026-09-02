"""Atomic persistence, watch, and notification for office aggregates."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Protocol, TypeVar

from ..domain import (
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
    async def state(self, kind: OfficeStateKind) -> OfficeState | None: ...
    async def save(self, state: OfficeState) -> None: ...


class OfficeStateNotInitializedError(RuntimeError):
    pass


class OfficeStateService:
    """A per-kind lock makes each kind's subscribe+snapshot and field
    writes atomic, without serializing the discord and editor aggregates
    against each other -- they're logically independent stores."""

    def __init__(self, storage: OfficeStateStorage, events: EventBusService) -> None:
        self._storage = storage
        self._events = events
        self._locks: dict[OfficeStateKind, asyncio.Lock] = {
            kind: asyncio.Lock() for kind in OfficeStateKind
        }

    async def state(self, kind: OfficeStateKind) -> OfficeState | None:
        async with self._locks[kind]:
            state = await self._storage.state(kind)
            return copy_office_state(state) if state is not None else None

    async def initialize(self, kind: OfficeStateKind, layout: RawLayout) -> OfficeState:
        changed: OfficeState | None = None
        async with self._locks[kind]:
            current = await self._storage.state(kind)
            if current is None:
                current = OfficeState(
                    kind=kind,
                    layout=deepcopy(layout),
                    seats={},
                    revision=1,
                )
                await self._storage.save(current)
                changed = current
            result = copy_office_state(current)
        if changed is not None:
            await self._publish(changed)
        return result

    async def set_layout(self, kind: OfficeStateKind, layout: RawLayout) -> OfficeState:
        async with self._locks[kind]:
            current = await self._required(kind)
            updated = OfficeState(
                kind=kind,
                layout=deepcopy(layout),
                seats=deepcopy(current.seats),
                revision=current.revision + 1,
            )
            await self._storage.save(updated)
            result = copy_office_state(updated)
        await self._publish(updated)
        return result

    async def set_seats(self, kind: OfficeStateKind, seats: SeatRecords) -> OfficeState:
        async with self._locks[kind]:
            current = await self._required(kind)
            updated = OfficeState(
                kind=kind,
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
        kind: OfficeStateKind,
        mutation: Callable[[SeatRecords], MutationResult],
    ) -> tuple[OfficeState, MutationResult]:
        async with self._locks[kind]:
            current = await self._required(kind)
            seats = deepcopy(current.seats)
            mutation_result = mutation(seats)
            updated = OfficeState(
                kind=kind,
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
        async def filtered(event: OfficeStateChanged) -> None:
            if event.state.kind == kind:
                await handler(OfficeStateChanged(state=copy_office_state(event.state)))

        async with self._locks[kind]:
            self._events.subscribe(OfficeStateChanged, filtered, owner=owner)
            state = await self._storage.state(kind)
            return copy_office_state(state) if state is not None else None

    async def _required(self, kind: OfficeStateKind) -> OfficeState:
        state = await self._storage.state(kind)
        if state is None:
            raise OfficeStateNotInitializedError(
                f"{kind.value} office state has not been initialized"
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
