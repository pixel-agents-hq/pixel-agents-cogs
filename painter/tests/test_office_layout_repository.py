from __future__ import annotations

import unittest

from corridor.domain import OfficeState, OfficeStateKind
from pixelagents.domain import Office
from pixelagents.infrastructure.furniture_styles import FurnitureStyleManifest

from ..infrastructure.office_layout_repository import OfficeLayoutRepository
from .conftest import FakePixelAgents


def _flat_layout(cols: int = 3, rows: int = 3) -> dict[str, object]:
    return {
        "version": 1,
        "cols": cols,
        "rows": rows,
        "tiles": [1] * (cols * rows),
        "furniture": [],
    }


class TestOfficeLayoutRepository(unittest.IsolatedAsyncioTestCase):
    async def test_load_reads_the_editor_aggregate(self) -> None:
        pixelagents = FakePixelAgents(editor_layout=_flat_layout(4, 5))
        repository = OfficeLayoutRepository(lambda: pixelagents)

        office = await repository.load(FurnitureStyleManifest.from_raw({"styles": []}))

        self.assertIsInstance(office, Office)
        self.assertEqual(office.width, 4)
        self.assertEqual(office.height, 5)

    async def test_save_preserves_editor_avatar_seats(self) -> None:
        pixelagents = FakePixelAgents(editor_layout=_flat_layout())
        pixelagents._states[OfficeStateKind.EDITOR] = OfficeState(
            kind=OfficeStateKind.EDITOR,
            layout=_flat_layout(),
            seats={"painter": {"seatId": "desk-2"}},
            revision=1,
        )
        repository = OfficeLayoutRepository(lambda: pixelagents)
        styles = FurnitureStyleManifest.from_raw({"styles": []})
        office = await repository.load(styles)

        await repository.save(office, styles)

        saved = await pixelagents.office_state(OfficeStateKind.EDITOR)
        self.assertEqual(saved.layout["cols"], 3)
        self.assertEqual(saved.seats, {"painter": {"seatId": "desk-2"}})
        self.assertEqual(saved.revision, 2)


if __name__ == "__main__":
    unittest.main()
