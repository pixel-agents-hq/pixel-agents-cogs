from __future__ import annotations

import unittest

from corridor.domain import OfficeStateKind
from pixelagents.infrastructure.furniture_styles import FurnitureStyleManifest

from ..domain import Office
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

    async def test_save_updates_only_the_editor_layout_field(self) -> None:
        pixelagents = FakePixelAgents(editor_layout=_flat_layout())
        before = await pixelagents.office_state(OfficeStateKind.EDITOR)
        pixelagents._states[OfficeStateKind.EDITOR] = type(before)(
            kind=before.kind,
            layout=before.layout,
            seats={"architect": {"seatId": "desk-1"}},
            revision=before.revision,
        )
        repository = OfficeLayoutRepository(lambda: pixelagents)
        styles = FurnitureStyleManifest.from_raw({"styles": []})
        office = await repository.load(styles)

        await repository.save(office, styles)

        saved = await pixelagents.office_state(OfficeStateKind.EDITOR)
        self.assertEqual(saved.layout["cols"], 3)
        self.assertEqual(saved.seats, {"architect": {"seatId": "desk-1"}})
        self.assertEqual(saved.revision, 2)


if __name__ == "__main__":
    unittest.main()
