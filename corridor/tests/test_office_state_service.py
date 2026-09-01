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

    async def test_a_slow_subscriber_does_not_block_a_concurrent_reader(self) -> None:
        """docs/cctv-design.md §2.5's delivery rules: persist, release the
        lock, *then* deliver. If the lock were instead held across
        delivery, a concurrent reader would have to wait out the slow
        subscriber's full delivery -- this proves it does not. Uses a
        plain read (not a second write) so the assertion isolates lock
        availability from the separate question of how long that second
        call's *own* publish would take -- it would hit the very same
        registered subscriber."""

        service = OfficeStateService(RedOfficeStateRepository.create(cog=object()))
        subscriber_started = asyncio.Event()
        release_subscriber = asyncio.Event()

        async def slow(event: OfficeStateChanged) -> None:
            subscriber_started.set()
            await release_subscriber.wait()

        await service.watch("discord", slow, owner="Slow")

        first = asyncio.create_task(service.set_layout("discord", {"cols": 1}))
        await asyncio.wait_for(subscriber_started.wait(), timeout=1.0)

        # The per-kind lock must already be free at this point -- a
        # concurrent read must complete promptly even while `first`'s slow
        # subscriber is still being awaited.
        state = await asyncio.wait_for(service.read("discord"), timeout=1.0)
        self.assertEqual(state.layout, {"cols": 1})

        release_subscriber.set()
        await asyncio.wait_for(first, timeout=1.0)

    async def test_a_subscriber_that_reads_its_own_kind_does_not_deadlock(self) -> None:
        """A subscriber calling back into `read()` (or any other
        operation) for the *same* kind it was just notified about must
        not deadlock against the writer that is about to release the
        lock it's publishing under -- only reachable at all because
        delivery now happens after the lock is released."""

        service = OfficeStateService(RedOfficeStateRepository.create(cog=object()))
        seen: list[int] = []

        async def reentrant(event: OfficeStateChanged) -> None:
            state = await service.read("discord")
            seen.append(state.revision)

        await service.watch("discord", reentrant, owner="Reentrant")

        await asyncio.wait_for(service.set_layout("discord", {"cols": 1}), timeout=1.0)

        self.assertEqual(seen, [1])


class TestSetLayoutIfEmpty(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.service = OfficeStateService(RedOfficeStateRepository.create(cog=object()))

    async def test_seeds_a_blank_aggregate(self) -> None:
        updated = await self.service.set_layout_if_empty("discord", {"cols": 5})

        self.assertEqual(updated.layout, {"cols": 5})
        self.assertEqual(updated.revision, 1)

    async def test_never_overwrites_an_already_seeded_layout(self) -> None:
        await self.service.set_layout("discord", {"cols": 1})

        updated = await self.service.set_layout_if_empty("discord", {"cols": 99})

        self.assertEqual(updated.layout, {"cols": 1})
        self.assertEqual(updated.revision, 1)

    async def test_no_op_seed_does_not_publish(self) -> None:
        await self.service.set_layout("discord", {"cols": 1})
        received: list[OfficeStateChanged] = []
        await self.service.watch("discord", _recorder(received), owner="Watcher")

        await self.service.set_layout_if_empty("discord", {"cols": 99})

        self.assertEqual(received, [])

    async def test_two_concurrent_seed_calls_never_lose_either_ones_lock_hold(self) -> None:
        """Both calls go through the same per-kind lock, so they can never
        truly race each other -- whichever runs first seeds it, the
        second sees it's no longer blank and no-ops. This is what makes
        set_layout_if_empty a genuine improvement over a caller composing
        its own read-then-conditionally-set: two callers hitting *this*
        method concurrently can never both write, unlike two callers each
        doing their own read() then set_office_layout()."""

        first, second = await asyncio.gather(
            self.service.set_layout_if_empty("discord", {"cols": 1}),
            self.service.set_layout_if_empty("discord", {"cols": 2}),
        )

        # Exactly one of the two payloads won -- never a mix, and never
        # both applied (revision would be 2, not 1).
        self.assertIn(first.layout, ({"cols": 1}, {"cols": 2}))
        self.assertEqual(first.layout, second.layout)
        self.assertEqual(first.revision, 1)


if __name__ == "__main__":
    unittest.main()
