from __future__ import annotations

import asyncio
import unittest
from collections.abc import Awaitable, Callable
from unittest.mock import patch

from ..application import OfficeStateNotInitializedError, OfficeStateService
from ..application.event_bus_service import EventBusService
from ..domain import (
    OfficeRef,
    OfficeState,
    OfficeStateChanged,
    OfficeStateKind,
    copy_office_state,
)

DISCORD = OfficeRef.discord(111)
EDITOR = OfficeRef.editor()


def _revision_recorder(
    target: list[int],
) -> Callable[[OfficeStateChanged], Awaitable[None]]:
    async def record(event: OfficeStateChanged) -> None:
        target.append(event.state.revision)

    return record


class _MemoryStorage:
    def __init__(self) -> None:
        self.states: dict[OfficeRef, OfficeState] = {}

    async def state(self, ref: OfficeRef) -> OfficeState | None:
        state = self.states.get(ref)
        return copy_office_state(state) if state is not None else None

    async def save(self, state: OfficeState) -> None:
        self.states[state.ref] = copy_office_state(state)


class _BlockingStorage(_MemoryStorage):
    def __init__(self) -> None:
        super().__init__()
        self.read_started = asyncio.Event()
        self.release_read = asyncio.Event()
        self.block_next_read = False

    async def state(self, ref: OfficeRef) -> OfficeState | None:
        if self.block_next_read:
            self.block_next_read = False
            self.read_started.set()
            await self.release_read.wait()
        return await super().state(ref)


class TestOfficeStateService(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.storage = _MemoryStorage()
        self.events = EventBusService()
        self.service = OfficeStateService(self.storage, self.events)

    async def test_state_must_be_initialized_before_writes(self) -> None:
        with self.assertRaises(OfficeStateNotInitializedError):
            await self.service.set_layout(DISCORD, {"version": 1})

    async def test_initialize_is_idempotent(self) -> None:
        first = await self.service.initialize(EDITOR, {"version": 1})
        second = await self.service.initialize(EDITOR, {"version": 2})

        self.assertEqual(first.revision, 1)
        self.assertEqual(second.layout, {"version": 1})
        self.assertEqual(second.seats, {})

    async def test_concurrent_initialization_is_atomic_for_every_aggregate(self) -> None:
        for ref in (DISCORD, EDITOR):
            with self.subTest(ref=ref):
                storage = _MemoryStorage()
                service = OfficeStateService(storage, EventBusService())
                states = await asyncio.gather(
                    service.initialize(ref, {"candidate": 1}),
                    service.initialize(ref, {"candidate": 2}),
                )

                self.assertEqual(states[0], states[1])
                self.assertEqual(states[0].revision, 1)
                self.assertEqual(states[0].seats, {})

    async def test_field_updates_preserve_the_other_field_and_increment_revision(self) -> None:
        await self.service.initialize(DISCORD, {"version": 1})
        with_seats = await self.service.set_seats(DISCORD, {"-1": {"palette": 2}})
        with_layout = await self.service.set_layout(DISCORD, {"version": 1, "cols": 2})

        self.assertEqual(with_seats.revision, 2)
        self.assertEqual(with_layout.revision, 3)
        self.assertEqual(with_layout.seats, {"-1": {"palette": 2}})
        self.assertEqual(with_layout.layout["cols"], 2)

    async def test_events_carry_complete_defensive_snapshots(self) -> None:
        received: list[OfficeStateChanged] = []

        async def record(event: OfficeStateChanged) -> None:
            received.append(event)

        await self.service.watch(OfficeStateKind.EDITOR, record, owner="watcher")
        await self.service.initialize(EDITOR, {"version": 1})
        await self.service.set_seats(EDITOR, {"agent": {"seatId": "x"}})

        self.assertEqual([event.state.revision for event in received], [1, 2])
        self.assertEqual(received[-1].state.layout, {"version": 1})
        self.assertEqual(received[-1].state.seats, {"agent": {"seatId": "x"}})

    async def test_seat_mutation_is_persisted_with_its_result(self) -> None:
        await self.service.initialize(DISCORD, {"version": 1})

        def assign(seats: dict[str, dict[str, object]]) -> str:
            seats["-1"] = {"palette": 4}
            return "assigned"

        state, result = await self.service.mutate_seats(DISCORD, assign)

        self.assertEqual(result, "assigned")
        self.assertEqual(state.seats, {"-1": {"palette": 4}})
        self.assertEqual(state.revision, 2)

    async def test_watch_has_no_write_gap_for_the_editor(self) -> None:
        storage = _BlockingStorage()
        events = EventBusService()
        service = OfficeStateService(storage, events)
        await service.initialize(EDITOR, {"version": 1})
        received: list[int] = []

        storage.block_next_read = True
        watch = asyncio.create_task(
            service.watch(OfficeStateKind.EDITOR, _revision_recorder(received), owner="watcher")
        )
        await storage.read_started.wait()
        write = asyncio.create_task(service.set_layout(EDITOR, {"version": 2}))
        await asyncio.sleep(0)
        self.assertFalse(write.done())

        storage.release_read.set()
        snapshot = await watch
        await write

        assert snapshot is not None
        self.assertEqual(snapshot.revision, 1)
        self.assertEqual(received, [2])

    async def test_a_discord_watch_covers_every_guild_and_returns_no_snapshot(self) -> None:
        other = OfficeRef.discord(222)
        await self.service.initialize(DISCORD, {"version": 1})
        received: list[OfficeRef] = []

        async def record(event: OfficeStateChanged) -> None:
            received.append(event.state.ref)

        snapshot = await self.service.watch(OfficeStateKind.DISCORD, record, owner="watcher")
        await self.service.initialize(other, {"version": 1})
        await self.service.set_layout(DISCORD, {"version": 2})
        await self.service.set_layout(other, {"version": 3})

        self.assertIsNone(snapshot)
        self.assertEqual(received, [other, DISCORD, other])

    async def test_guild_offices_are_independent(self) -> None:
        other = OfficeRef.discord(222)
        await self.service.initialize(DISCORD, {"version": 1})
        await self.service.initialize(other, {"version": 9})
        await self.service.set_seats(DISCORD, {"-1": {"seatId": "a"}})

        mine = await self.service.state(DISCORD)
        theirs = await self.service.state(other)
        assert mine is not None and theirs is not None
        self.assertEqual((mine.revision, mine.guild_id), (2, 111))
        self.assertEqual((theirs.revision, theirs.seats, theirs.layout), (1, {}, {"version": 9}))

    async def test_office_timeout_cancels_slow_handler_and_keeps_persisted_write(self) -> None:
        await self.service.initialize(EDITOR, {"version": 1})
        cancelled = asyncio.Event()
        received: list[int] = []

        async def slow(event: OfficeStateChanged) -> None:
            del event
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def healthy(event: OfficeStateChanged) -> None:
            received.append(event.state.revision)

        await self.service.watch(OfficeStateKind.EDITOR, slow, owner="slow")
        await self.service.watch(OfficeStateKind.EDITOR, healthy, owner="healthy")

        with patch(
            "corridor.application.office_state_service.OFFICE_STATE_SUBSCRIBER_TIMEOUT",
            0.01,
        ):
            result = await self.service.set_layout(EDITOR, {"version": 2})

        persisted = await self.service.state(EDITOR)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(received, [2])
        self.assertEqual(result.revision, 2)
        self.assertEqual(persisted, result)


if __name__ == "__main__":
    unittest.main()
