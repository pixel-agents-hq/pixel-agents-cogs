from __future__ import annotations

import unittest
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any, TypeVar
from unittest.mock import patch

from corridor.domain import OfficeState, OfficeStateChanged, OfficeStateKind, SeatRecords

from ..application import (
    OfficeStateFacade,
    OfficeStateUnavailableError,
    OfficeStateValidationError,
)
from ..infrastructure.furniture_styles import FurnitureStyleManifest

MutationResult = TypeVar("MutationResult")


def _layout(*, cols: int = 1) -> dict[str, Any]:
    return {
        "version": 1,
        "cols": cols,
        "rows": 1,
        "tiles": [1] * cols,
        "furniture": [],
    }


class _FakeCorridor:
    def __init__(self) -> None:
        self.states: dict[OfficeStateKind, OfficeState] = {}
        self.watchers: dict[
            OfficeStateKind, list[Callable[[OfficeStateChanged], Awaitable[None]]]
        ] = {kind: [] for kind in OfficeStateKind}

    async def office_state(self, kind: OfficeStateKind) -> OfficeState | None:
        return deepcopy(self.states.get(kind))

    async def initialize_office_state(
        self, kind: OfficeStateKind, layout: dict[str, Any]
    ) -> OfficeState:
        if kind not in self.states:
            self.states[kind] = OfficeState(kind, deepcopy(layout), {}, 1)
            await self._publish(self.states[kind])
        return deepcopy(self.states[kind])

    async def set_office_layout(self, kind: OfficeStateKind, layout: dict[str, Any]) -> OfficeState:
        current = self.states[kind]
        updated = OfficeState(kind, deepcopy(layout), deepcopy(current.seats), current.revision + 1)
        self.states[kind] = updated
        await self._publish(updated)
        return deepcopy(updated)

    async def set_office_seats(self, kind: OfficeStateKind, seats: SeatRecords) -> OfficeState:
        current = self.states[kind]
        updated = OfficeState(kind, deepcopy(current.layout), deepcopy(seats), current.revision + 1)
        self.states[kind] = updated
        await self._publish(updated)
        return deepcopy(updated)

    async def mutate_office_seats(
        self,
        kind: OfficeStateKind,
        mutation: Callable[[SeatRecords], MutationResult],
    ) -> tuple[OfficeState, MutationResult]:
        current = self.states[kind]
        seats = deepcopy(current.seats)
        result = mutation(seats)
        state = await self.set_office_seats(kind, seats)
        return state, result

    async def watch_office_state(
        self,
        kind: OfficeStateKind,
        handler: Callable[[OfficeStateChanged], Awaitable[None]],
        *,
        owner: str,
    ) -> OfficeState | None:
        self.watchers[kind].append(handler)
        return deepcopy(self.states.get(kind))

    async def _publish(self, state: OfficeState) -> None:
        for handler in self.watchers[state.kind]:
            await handler(OfficeStateChanged(deepcopy(state)))


class TestOfficeStateFacade(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.corridor = _FakeCorridor()
        self.default = _layout()
        self.facade = OfficeStateFacade(
            self.corridor,
            lambda: deepcopy(self.default),
            lambda: FurnitureStyleManifest([]),
        )

    async def test_both_aggregates_lazily_initialize_from_the_same_default(self) -> None:
        discord = await self.facade.state(OfficeStateKind.DISCORD)
        editor = await self.facade.state(OfficeStateKind.EDITOR)

        self.assertEqual(discord.layout, self.default)
        self.assertEqual(editor.layout, self.default)
        self.assertEqual(discord.seats, {})
        self.assertEqual(editor.seats, {})
        self.assertEqual(discord.revision, 1)
        self.assertEqual(editor.revision, 1)

    async def test_layout_write_preserves_seats(self) -> None:
        await self.facade.state(OfficeStateKind.DISCORD)
        await self.facade.merge_seat_patch(
            OfficeStateKind.DISCORD,
            "-1",
            {"palette": 2, "seatId": "chair:1"},
        )

        state = await self.facade.set_layout(OfficeStateKind.DISCORD, _layout(cols=2))

        self.assertEqual(state.layout["cols"], 2)
        self.assertEqual(state.seats["-1"], {"palette": 2, "seatId": "chair:1"})
        self.assertEqual(state.revision, 3)

    async def test_invalid_persisted_state_is_reported_without_reset(self) -> None:
        invalid = OfficeState(
            OfficeStateKind.DISCORD,
            {"version": 99},
            {},
            4,
        )
        self.corridor.states[OfficeStateKind.DISCORD] = invalid

        with self.assertRaises(OfficeStateValidationError):
            await self.facade.state(OfficeStateKind.DISCORD)

        self.assertEqual(self.corridor.states[OfficeStateKind.DISCORD], invalid)

    async def test_editor_validation_uses_semantic_codec(self) -> None:
        with patch("pixelagents.application.office_state.decode") as decode_layout:
            decode_layout.return_value = object()
            with patch("pixelagents.application.office_state.encode") as encode_layout:
                self.facade.validate_layout(OfficeStateKind.EDITOR, _layout())

        decode_layout.assert_called_once()
        encode_layout.assert_called_once()

    async def test_discord_validation_does_not_use_semantic_codec(self) -> None:
        with patch("pixelagents.application.office_state.decode") as decode_layout:
            self.facade.validate_layout(OfficeStateKind.DISCORD, _layout())

        decode_layout.assert_not_called()

    async def test_seat_patch_ignores_invalid_fields_and_preserves_valid_fields(self) -> None:
        state = await self.facade.merge_seat_patch(
            OfficeStateKind.EDITOR,
            "agent",
            {"palette": 999, "hueShift": 90, "seatId": "chair:2"},
        )

        self.assertEqual(state.seats["agent"], {"hueShift": 90, "seatId": "chair:2"})

    async def test_watch_subscribes_before_lazy_initialization(self) -> None:
        received: list[int] = []

        async def record(event: OfficeStateChanged) -> None:
            received.append(event.state.revision)

        snapshot = await self.facade.watch(OfficeStateKind.DISCORD, record, owner="cctv")

        self.assertEqual(snapshot.revision, 1)
        self.assertEqual(received, [1])

    async def test_missing_default_is_a_degraded_error(self) -> None:
        facade = OfficeStateFacade(
            self.corridor,
            lambda: None,
            lambda: FurnitureStyleManifest([]),
        )

        with self.assertRaises(OfficeStateUnavailableError):
            await facade.state(OfficeStateKind.DISCORD)

    async def test_invalid_known_seat_field_is_rejected(self) -> None:
        await self.facade.state(OfficeStateKind.DISCORD)

        with self.assertRaises(OfficeStateValidationError):
            await self.facade.set_seats(
                OfficeStateKind.DISCORD,
                {"-1": {"hueShift": 361}},
            )


if __name__ == "__main__":
    unittest.main()
