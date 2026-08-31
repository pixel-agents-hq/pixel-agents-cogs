"""OfficeStateFacade is testable without a real corridor Cog -- a small
in-memory FakeOfficeStateBackend mirrors corridor's own five-method
surface (docs/cctv-design.md §2.6), the same "construct the application
service directly with a fake" convention test_application_office.py's
MemorySeats already uses."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from typing import TypeVar

from corridor.domain import OfficeState, OfficeStateChanged, OfficeStateKind

from ..application.office_state import (
    InvalidDiscordLayoutError,
    OfficeLayoutNotSeededError,
    OfficeStateFacade,
)
from ..infrastructure.furniture_styles import FurnitureStyleManifest

MutationResult = TypeVar("MutationResult")

_VALID_DISCORD_LAYOUT = {
    "version": 1,
    "cols": 2,
    "rows": 1,
    "tiles": [1, 1],
    "furniture": [],
}

_EDITOR_LAYOUT_RAW = {
    "cols": 2,
    "rows": 1,
    "tiles": [1, 1],
    "furniture": [],
}

_EMPTY_STYLES = FurnitureStyleManifest([])


class FakeOfficeStateBackend:
    """In-memory stand-in for corridor's own office-state surface --
    mirrors RedOfficeStateRepository's semantics (blank-on-first-touch,
    revision increments, sibling-field preservation) without any Config
    dependency, plus real (unbounded, unlocked) watcher delivery since
    these tests never race a mutation against a watch call -- that
    concurrency behavior belongs to corridor/tests/test_office_state_service.py,
    not here."""

    def __init__(self) -> None:
        self._states: dict[OfficeStateKind, OfficeState] = {}
        self._watchers: dict[OfficeStateKind, list[Callable]] = {"discord": [], "editor": []}

    def _blank(self, kind: OfficeStateKind) -> OfficeState:
        return OfficeState(kind=kind, layout={}, seats={}, revision=0)

    async def read_office_state(self, kind: OfficeStateKind) -> OfficeState:
        return self._states.setdefault(kind, self._blank(kind))

    async def set_office_layout(
        self, kind: OfficeStateKind, layout: dict[str, object]
    ) -> OfficeState:
        current = self._states.setdefault(kind, self._blank(kind))
        updated = OfficeState(
            kind=kind, layout=layout, seats=current.seats, revision=current.revision + 1
        )
        self._states[kind] = updated
        for handler in self._watchers[kind]:
            await handler(OfficeStateChanged(state=updated))
        return updated

    async def mutate_office_seats(
        self, kind: OfficeStateKind, mutation: Callable[[dict], MutationResult]
    ) -> tuple[OfficeState, MutationResult]:
        current = self._states.setdefault(kind, self._blank(kind))
        seats = dict(current.seats)
        result = mutation(seats)
        updated = OfficeState(
            kind=kind, layout=current.layout, seats=seats, revision=current.revision + 1
        )
        self._states[kind] = updated
        for handler in self._watchers[kind]:
            await handler(OfficeStateChanged(state=updated))
        return updated, result

    async def watch_office_state(self, kind, handler, *, owner: str) -> OfficeState:
        self._watchers[kind].append(handler)
        return self._states.setdefault(kind, self._blank(kind))

    def unwatch_office_state_owner(self, owner: str) -> None:
        pass  # not exercised by these tests -- owner tracking is corridor's job


class TestLazySeeding(unittest.IsolatedAsyncioTestCase):
    async def test_a_blank_aggregate_is_seeded_from_the_bundled_default_on_read(self) -> None:
        facade = OfficeStateFacade(
            FakeOfficeStateBackend(), default_layout=lambda: dict(_VALID_DISCORD_LAYOUT)
        )

        state = await facade.read("discord")

        self.assertEqual(state.layout, _VALID_DISCORD_LAYOUT)
        self.assertEqual(state.revision, 1)

    async def test_an_already_seeded_aggregate_is_not_reseeded(self) -> None:
        backend = FakeOfficeStateBackend()
        facade = OfficeStateFacade(backend, default_layout=lambda: dict(_VALID_DISCORD_LAYOUT))
        await facade.set_discord_layout({**_VALID_DISCORD_LAYOUT, "cols": 3, "tiles": [1, 1, 1]})

        state = await facade.read("discord")

        self.assertEqual(state.layout["cols"], 3)

    async def test_no_bundled_default_available_leaves_the_aggregate_blank(self) -> None:
        facade = OfficeStateFacade(FakeOfficeStateBackend(), default_layout=lambda: None)

        state = await facade.read("discord")

        self.assertEqual(state.layout, {})

    async def test_watch_also_seeds_a_blank_aggregate(self) -> None:
        facade = OfficeStateFacade(
            FakeOfficeStateBackend(), default_layout=lambda: dict(_VALID_DISCORD_LAYOUT)
        )

        async def handler(event: OfficeStateChanged) -> None:
            pass

        snapshot = await facade.watch("discord", handler, owner="Cctv")

        self.assertEqual(snapshot.layout, _VALID_DISCORD_LAYOUT)


class TestKindsAreIndependent(unittest.IsolatedAsyncioTestCase):
    async def test_seeding_one_kind_does_not_seed_the_other(self) -> None:
        facade = OfficeStateFacade(
            FakeOfficeStateBackend(), default_layout=lambda: dict(_VALID_DISCORD_LAYOUT)
        )

        await facade.read("discord")
        editor_state = await facade._backend.read_office_state("editor")

        self.assertEqual(editor_state.layout, {})


class TestDiscordWireSchemaValidation(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.facade = OfficeStateFacade(FakeOfficeStateBackend(), default_layout=lambda: None)

    async def test_a_valid_layout_is_accepted(self) -> None:
        state = await self.facade.set_discord_layout(_VALID_DISCORD_LAYOUT)

        self.assertEqual(state.layout, _VALID_DISCORD_LAYOUT)

    async def test_missing_version_is_rejected(self) -> None:
        with self.assertRaises(InvalidDiscordLayoutError):
            await self.facade.set_discord_layout({**_VALID_DISCORD_LAYOUT, "version": 2})

    async def test_tiles_length_mismatch_is_rejected(self) -> None:
        with self.assertRaises(InvalidDiscordLayoutError):
            await self.facade.set_discord_layout({**_VALID_DISCORD_LAYOUT, "tiles": [1]})

    async def test_non_dict_is_rejected(self) -> None:
        with self.assertRaises(InvalidDiscordLayoutError):
            await self.facade.set_discord_layout("not a dict")  # type: ignore[arg-type]


class TestEditorSemanticIrRoundTrip(unittest.IsolatedAsyncioTestCase):
    async def test_set_then_load_round_trips_through_the_codec(self) -> None:
        facade = OfficeStateFacade(FakeOfficeStateBackend(), default_layout=lambda: None)
        from ..infrastructure.pixel_agents_adapter import decode

        office = decode(_EDITOR_LAYOUT_RAW, _EMPTY_STYLES)

        await facade.set_editor_layout(office, _EMPTY_STYLES)
        reloaded = await facade.load_editor_office(_EMPTY_STYLES)

        self.assertEqual(reloaded.width, office.width)
        self.assertEqual(reloaded.height, office.height)

    async def test_loading_an_unseeded_editor_aggregate_raises(self) -> None:
        facade = OfficeStateFacade(FakeOfficeStateBackend(), default_layout=lambda: None)

        with self.assertRaises(OfficeLayoutNotSeededError):
            await facade.load_editor_office(_EMPTY_STYLES)


class TestSeatShapeParityAcrossBothKinds(unittest.IsolatedAsyncioTestCase):
    """The one place a later Discord-only seat field could silently break
    editor-page avatars without a shared test catching it."""

    async def test_equivalent_patches_produce_byte_identical_records_on_both_kinds(self) -> None:
        facade = OfficeStateFacade(FakeOfficeStateBackend(), default_layout=lambda: None)
        patch = {"palette": 2, "hueShift": 90, "seatId": "desk-1"}

        discord_state = await facade.mutate_seats("discord", "-1", patch)
        editor_state = await facade.mutate_seats("editor", "-1", patch)

        self.assertEqual(discord_state.seats, editor_state.seats)
        self.assertEqual(
            discord_state.seats["-1"], {"palette": 2, "hueShift": 90, "seatId": "desk-1"}
        )

    async def test_out_of_range_palette_is_dropped_for_both_kinds(self) -> None:
        facade = OfficeStateFacade(FakeOfficeStateBackend(), default_layout=lambda: None)
        patch = {"palette": 999}

        discord_state = await facade.mutate_seats("discord", "-1", patch, palette_count=6)
        editor_state = await facade.mutate_seats("editor", "-1", patch, palette_count=6)

        self.assertEqual(discord_state.seats["-1"], {})
        self.assertEqual(editor_state.seats["-1"], {})


if __name__ == "__main__":
    unittest.main()
