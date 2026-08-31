"""OfficeStateService is testable without discord/a2a stubs -- it only
needs RedOfficeStateRepository's Config-backed storage, same shape as
test_office_state_repository.py."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from ..application import OfficeStateService
from ..domain import OfficeStateChanged
from ..infrastructure import RedOfficeStateRepository


def _recorder(sink: list) -> object:
    async def handler(event: OfficeStateChanged) -> None:
        sink.append(event)

    return handler


class TestReadAndWatch(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.service = OfficeStateService(RedOfficeStateRepository.create(cog=object()))

    async def test_read_creates_a_blank_aggregate_on_first_touch(self) -> None:
        state = await self.service.read("discord")

        self.assertEqual(state.layout, {})
        self.assertEqual(state.revision, 0)

    async def test_watch_returns_the_current_snapshot(self) -> None:
        await self.service.set_layout("editor", {"cols": 2})

        snapshot = await self.service.watch("editor", _recorder([]), owner="Cctv")

        self.assertEqual(snapshot.layout, {"cols": 2})
        self.assertEqual(snapshot.revision, 1)

    async def test_watching_one_kind_does_not_see_another_kinds_state(self) -> None:
        await self.service.set_layout("discord", {"cols": 9})

        snapshot = await self.service.watch("editor", _recorder([]), owner="Cctv")

        self.assertEqual(snapshot.layout, {})


class TestMutationPublishesToWatchers(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.service = OfficeStateService(RedOfficeStateRepository.create(cog=object()))

    async def test_set_layout_delivers_the_complete_new_state(self) -> None:
        received: list[OfficeStateChanged] = []
        await self.service.watch("discord", _recorder(received), owner="Cctv")

        await self.service.set_layout("discord", {"cols": 4})

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].state.layout, {"cols": 4})
        self.assertEqual(received[0].state.revision, 1)

    async def test_mutate_seats_delivers_the_complete_new_state_including_layout(self) -> None:
        await self.service.set_layout("editor", {"cols": 4})
        received: list[OfficeStateChanged] = []
        await self.service.watch("editor", _recorder(received), owner="Cctv")

        await self.service.mutate_seats(
            "editor", lambda seats: seats.setdefault("-1", {"palette": 1})
        )

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].state.layout, {"cols": 4})
        self.assertEqual(received[0].state.seats, {"-1": {"palette": 1}})

    async def test_a_watcher_on_a_different_kind_is_not_notified(self) -> None:
        received: list[OfficeStateChanged] = []
        await self.service.watch("editor", _recorder(received), owner="Cctv")

        await self.service.set_layout("discord", {"cols": 1})

        self.assertEqual(received, [])

    async def test_no_watchers_is_a_noop(self) -> None:
        await self.service.set_layout("discord", {"cols": 1})  # must not raise


class TestWatchThenMutateNoLostUpdates(unittest.IsolatedAsyncioTestCase):
    """The adversarial interleaving case: a mutation starting concurrently
    with a watch call must never leave the watcher's snapshot AND its
    first delivered event both stale relative to each other."""

    async def test_concurrent_watch_and_mutation_never_misses_an_update(self) -> None:
        service = OfficeStateService(RedOfficeStateRepository.create(cog=object()))
        received: list[OfficeStateChanged] = []

        async def watch_late() -> None:
            snapshot = await service.watch("editor", _recorder(received), owner="Cctv")
            watch_late.snapshot = snapshot  # type: ignore[attr-defined]

        async def mutate() -> None:
            await service.set_layout("editor", {"cols": 7})

        await asyncio.gather(watch_late(), mutate())

        snapshot = watch_late.snapshot  # type: ignore[attr-defined]
        # Whichever ran first under the per-kind lock: either the watcher
        # already saw revision 1 in its own snapshot (mutation-first), or
        # it saw revision 0 and then received exactly one delivered event
        # bringing it to revision 1 (watch-first) -- never neither.
        if snapshot.revision == 0:
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0].state.revision, 1)
        else:
            self.assertEqual(snapshot.revision, 1)


class TestUnwatchOwner(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.service = OfficeStateService(RedOfficeStateRepository.create(cog=object()))

    async def test_unwatch_owner_drops_only_that_owners_handlers(self) -> None:
        a: list[OfficeStateChanged] = []
        b: list[OfficeStateChanged] = []
        await self.service.watch("discord", _recorder(a), owner="A")
        await self.service.watch("discord", _recorder(b), owner="B")

        self.service.unwatch_owner("A")
        await self.service.set_layout("discord", {"cols": 1})

        self.assertEqual(a, [])
        self.assertEqual(len(b), 1)

    async def test_unwatch_owner_drops_across_every_kind(self) -> None:
        discord_received: list[OfficeStateChanged] = []
        editor_received: list[OfficeStateChanged] = []
        await self.service.watch("discord", _recorder(discord_received), owner="A")
        await self.service.watch("editor", _recorder(editor_received), owner="A")

        self.service.unwatch_owner("A")
        await self.service.set_layout("discord", {"cols": 1})
        await self.service.set_layout("editor", {"cols": 1})

        self.assertEqual(discord_received, [])
        self.assertEqual(editor_received, [])

    async def test_unwatch_owner_for_unknown_owner_is_a_noop(self) -> None:
        self.service.unwatch_owner("nobody")  # must not raise


class TestPublishTimeout(unittest.IsolatedAsyncioTestCase):
    @patch("corridor.application.office_state_service.PUBLISH_TIMEOUT_SECONDS", 0.01)
    async def test_a_stuck_subscriber_is_cancelled_and_does_not_block_the_writer(self) -> None:
        service = OfficeStateService(RedOfficeStateRepository.create(cog=object()))

        async def stuck(event: OfficeStateChanged) -> None:
            await asyncio.sleep(10)

        healthy_received: list[OfficeStateChanged] = []
        await service.watch("discord", stuck, owner="Stuck")
        await service.watch("discord", _recorder(healthy_received), owner="Healthy")

        # Must return promptly (not hang for ~10s) and must not raise --
        # persistence already succeeded before this publish loop runs.
        updated = await asyncio.wait_for(service.set_layout("discord", {"cols": 1}), timeout=2.0)

        self.assertEqual(updated.layout, {"cols": 1})
        self.assertEqual(len(healthy_received), 1)


if __name__ == "__main__":
    unittest.main()
