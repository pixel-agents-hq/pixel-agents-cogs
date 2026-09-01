"""Architect and Painter mutate one revisioned editor aggregate."""

from __future__ import annotations

import unittest

from architect.application.office_layout_service import OfficeLayoutService
from architect.infrastructure.office_layout_repository import (
    OfficeLayoutRepository as ArchitectLayoutRepository,
)
from corridor.domain import OfficeState, OfficeStateKind
from painter.application.painter_layout_service import PainterLayoutService
from painter.infrastructure.office_layout_repository import (
    OfficeLayoutRepository as PainterLayoutRepository,
)
from painter.tests.conftest import FakePixelAgents
from pixelagents.domain import GridPosition, GridRect, TileKind
from pixelagents.infrastructure.furniture_styles import FurnitureStyleLoader


class TestSharedEditorAggregate(unittest.IsolatedAsyncioTestCase):
    async def test_structural_then_color_write_share_revisions_and_preserve_seats(self) -> None:
        pixelagents = FakePixelAgents(
            furniture_styles={"styles": []},
            editor_layout={
                "version": 1,
                "cols": 2,
                "rows": 1,
                "tiles": [1, 1],
                "furniture": [],
            },
        )
        initial = await pixelagents.office_state(OfficeStateKind.EDITOR)
        pixelagents._states[OfficeStateKind.EDITOR] = OfficeState(
            kind=initial.kind,
            layout=initial.layout,
            seats={"architect": {"seatId": "desk-1"}},
            revision=initial.revision,
        )
        styles = FurnitureStyleLoader(pixelagents)
        architect = OfficeLayoutService(ArchitectLayoutRepository(lambda: pixelagents), styles)
        painter = PainterLayoutService(PainterLayoutRepository(lambda: pixelagents), styles)

        await architect.paint_tiles(
            area=GridRect(GridPosition(0, 0), 1, 1),
            kind=TileKind.FLOOR,
            material=3,
        )
        await painter.recolor_tiles(
            area=GridRect(GridPosition(0, 0), 1, 1),
            color={"h": 220, "s": 80, "b": 0, "c": 0},
        )

        state = await pixelagents.office_state(OfficeStateKind.EDITOR)
        self.assertEqual(state.revision, 3)
        self.assertEqual(state.layout["tiles"], [3, 1])
        self.assertEqual(
            state.layout["tileColors"][0],
            {"h": 220, "s": 80, "b": 0, "c": 0},
        )
        self.assertEqual(state.seats, {"architect": {"seatId": "desk-1"}})


if __name__ == "__main__":
    unittest.main()
