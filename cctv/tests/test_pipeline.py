from __future__ import annotations

import json
import unittest
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any, TypeVar

from corridor.domain import OfficeState, OfficeStateChanged, OfficeStateKind, SeatRecords

from ..application import CctvPipeline

MutationResult = TypeVar("MutationResult")


def _layout(cols: int = 1) -> dict[str, Any]:
    return {
        "version": 1,
        "cols": cols,
        "rows": 1,
        "tiles": [1] * cols,
        "furniture": [],
    }


class _Socket:
    def __init__(self) -> None:
        self.closed = False
        self.messages: list[dict[str, Any]] = []

    async def send_str(self, payload: str) -> None:
        self.messages.append(json.loads(payload))

    async def close(self) -> None:
        self.closed = True


class _PixelAgents:
    def __init__(self, state: OfficeState) -> None:
        self.state = state

    async def office_state(self, kind: OfficeStateKind) -> OfficeState:
        assert kind == self.state.kind
        return deepcopy(self.state)

    async def set_office_layout(
        self, kind: OfficeStateKind, layout: Mapping[str, object]
    ) -> OfficeState:
        self.state = OfficeState(
            kind,
            deepcopy(dict(layout)),
            deepcopy(self.state.seats),
            self.state.revision + 1,
        )
        return deepcopy(self.state)

    async def mutate_office_seats(
        self,
        kind: OfficeStateKind,
        mutation: Callable[[SeatRecords], MutationResult],
    ) -> tuple[OfficeState, MutationResult]:
        seats = deepcopy(self.state.seats)
        result = mutation(seats)
        self.state = OfficeState(
            kind,
            deepcopy(self.state.layout),
            seats,
            self.state.revision + 1,
        )
        return deepcopy(self.state), result


class _SeatRepository:
    def __init__(self, pixelagents: _PixelAgents) -> None:
        self.pixelagents = pixelagents

    async def seats(self) -> SeatRecords:
        return deepcopy(self.pixelagents.state.seats)

    async def mutate_seats(
        self, mutation: Callable[[SeatRecords], MutationResult]
    ) -> MutationResult:
        _, result = await self.pixelagents.mutate_office_seats(
            self.pixelagents.state.kind, mutation
        )
        return result


class TestCctvPipeline(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        state = OfficeState(OfficeStateKind.DISCORD, _layout(), {}, 1)
        self.pixelagents = _PixelAgents(state)
        self.pipeline = CctvPipeline(
            "discord",
            OfficeStateKind.DISCORD,
            self.pixelagents,
            _SeatRepository(self.pixelagents),
            {},
            lambda user_id: _true(),
            open_editor=False,
        )
        self.socket = _Socket()
        self.pipeline.clients.add(self.socket, is_editor=True)  # type: ignore[arg-type]

    async def test_bootstrap_orders_existing_agents_before_layout(self) -> None:
        await self.pipeline.seed_state(self.pixelagents.state)

        await self.pipeline.bootstrap(self.socket)  # type: ignore[arg-type]

        types = [message["type"] for message in self.socket.messages]
        self.assertLess(types.index("existingAgents"), types.index("layoutLoaded"))
        self.assertEqual(self.pipeline.revision, 1)

    async def test_only_newer_state_events_are_applied(self) -> None:
        await self.pipeline.seed_state(self.pixelagents.state)
        newer = OfficeState(OfficeStateKind.DISCORD, _layout(2), {}, 2)

        await self.pipeline.state_changed(OfficeStateChanged(newer))
        await self.pipeline.state_changed(OfficeStateChanged(newer))

        self.assertEqual(self.pipeline.revision, 2)
        self.assertEqual(
            [message["type"] for message in self.socket.messages],
            ["layoutLoaded", "existingAgents"],
        )

    async def test_seat_write_is_one_aggregate_mutation(self) -> None:
        from ..contracts import parse_client_message

        message = parse_client_message(
            {
                "type": "saveAgentSeats",
                "seats": {"-1": {"palette": 2, "seatId": "chair:1"}},
            }
        )
        assert message is not None

        await self.pipeline.handle_message(self.socket, message)  # type: ignore[arg-type]

        self.assertEqual(
            self.pixelagents.state.seats,
            {"-1": {"palette": 2, "seatId": "chair:1"}},
        )
        self.assertEqual(self.pixelagents.state.revision, 2)

    async def test_layout_save_is_written_and_broadcast_immediately(self) -> None:
        from ..contracts import parse_client_message

        message = parse_client_message({"type": "saveLayout", "layout": _layout(2)})
        assert message is not None

        await self.pipeline.handle_message(self.socket, message)  # type: ignore[arg-type]

        self.assertEqual(self.pixelagents.state.layout["cols"], 2)
        self.assertEqual(self.pixelagents.state.revision, 2)

    async def test_a_seat_only_revision_does_not_echo_layoutLoaded(self) -> None:
        """A revision bump from an unrelated seat mutation (agent spawned,
        palette assigned, ...) must not re-broadcast layoutLoaded -- an
        editor client mid-edit has no other way to distinguish it from a
        genuine external layout change, and the dirty-guard it relies on
        client-side has a real gap right after Save. Only existingAgents
        should go out for a seats-only change."""

        await self.pipeline.seed_state(self.pixelagents.state)
        seats_only = OfficeState(
            OfficeStateKind.DISCORD, self.pixelagents.state.layout, {"1": {"palette": 0}}, 2
        )

        await self.pipeline.state_changed(OfficeStateChanged(seats_only))

        self.assertEqual(
            [message["type"] for message in self.socket.messages],
            ["existingAgents"],
        )

    async def test_a_genuine_layout_change_still_broadcasts_layoutLoaded(self) -> None:
        await self.pipeline.seed_state(self.pixelagents.state)
        layout_changed = OfficeState(OfficeStateKind.DISCORD, _layout(2), {}, 2)

        await self.pipeline.state_changed(OfficeStateChanged(layout_changed))

        self.assertIn("layoutLoaded", [message["type"] for message in self.socket.messages])


async def _true() -> bool:
    return True


if __name__ == "__main__":
    unittest.main()
