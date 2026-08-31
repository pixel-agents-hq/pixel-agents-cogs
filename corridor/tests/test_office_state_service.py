from __future__ import annotations

import asyncio
import unittest

from ..application import OfficeStateNotInitializedError, OfficeStateService
from ..application.event_bus_service import EventBusService
from ..domain import OfficeState, OfficeStateChanged, OfficeStateKind, copy_office_state


class _MemoryStorage:
    def __init__(self) -> None:
        self.states: dict[OfficeStateKind, OfficeState] = {}

    async def state(self, kind: OfficeStateKind) -> OfficeState | None:
        state = self.states.get(kind)
        return copy_office_state(state) if state is not None else None

    async def save(self, state: OfficeState) -> None:
        self.states[state.kind] = copy_office_state(state)


class _BlockingStorage(_MemoryStorage):
    def __init__(self) -> None:
        super().__init__()
        self.read_started = asyncio.Event()
        self.release_read = asyncio.Event()
        self.block_next_read = False

    async def state(self, kind: OfficeStateKind) -> OfficeState | None:
        if self.block_next_read:
            self.block_next_read = False
            self.read_started.set()
            await self.release_read.wait()
        return await super().state(kind)


class TestOfficeStateService(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.storage = _MemoryStorage()
        self.events = EventBusService()
        self.service = OfficeStateService(self.storage, self.events)

    async def test_state_must_be_initialized_before_writes(self) -> None:
        with self.assertRaises(OfficeStateNotInitializedError):
            await self.service.set_layout(OfficeStateKind.DISCORD, {"version": 1})

    async def test_initialize_is_idempotent(self) -> None:
        first = await self.service.initialize(OfficeStateKind.EDITOR, {"version": 1})
        second = await self.service.initialize(OfficeStateKind.EDITOR, {"version": 2})

        self.assertEqual(first.revision, 1)
        self.assertEqual(second.layout, {"version": 1})
        self.assertEqual(second.seats, {})

    async def test_field_updates_preserve_the_other_field_and_increment_revision(self) -> None:
        await self.service.initialize(OfficeStateKind.DISCORD, {"version": 1})
        with_seats = await self.service.set_seats(OfficeStateKind.DISCORD, {"-1": {"palette": 2}})
        with_layout = await self.service.set_layout(
            OfficeStateKind.DISCORD, {"version": 1, "cols": 2}
        )

        self.assertEqual(with_seats.revision, 2)
        self.assertEqual(with_layout.revision, 3)
        self.assertEqual(with_layout.seats, {"-1": {"palette": 2}})
        self.assertEqual(with_layout.layout["cols"], 2)

    async def test_events_carry_complete_defensive_snapshots(self) -> None:
        received: list[OfficeStateChanged] = []

        async def record(event: OfficeStateChanged) -> None:
            received.append(event)

        await self.service.watch(OfficeStateKind.EDITOR, record, owner="watcher")
        await self.service.initialize(OfficeStateKind.EDITOR, {"version": 1})
        await self.service.set_seats(OfficeStateKind.EDITOR, {"agent": {"seatId": "x"}})

        self.assertEqual([event.state.revision for event in received], [1, 2])
        self.assertEqual(received[-1].state.layout, {"version": 1})
        self.assertEqual(received[-1].state.seats, {"agent": {"seatId": "x"}})

    async def test_watch_has_no_write_gap(self) -> None:
        storage = _BlockingStorage()
        events = EventBusService()
        service = OfficeStateService(storage, events)
        await service.initialize(OfficeStateKind.DISCORD, {"version": 1})
        received: list[int] = []

        async def record(event: OfficeStateChanged) -> None:
            received.append(event.state.revision)

        storage.block_next_read = True
        watch = asyncio.create_task(service.watch(OfficeStateKind.DISCORD, record, owner="watcher"))
        await storage.read_started.wait()
        write = asyncio.create_task(service.set_layout(OfficeStateKind.DISCORD, {"version": 2}))
        await asyncio.sleep(0)
        self.assertFalse(write.done())

        storage.release_read.set()
        snapshot = await watch
        await write

        assert snapshot is not None
        self.assertEqual(snapshot.revision, 1)
        self.assertEqual(received, [2])


if __name__ == "__main__":
    unittest.main()
