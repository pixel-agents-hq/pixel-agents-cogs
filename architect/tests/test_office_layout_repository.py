from __future__ import annotations

import unittest

from ..domain.office_ir import Office
from ..infrastructure.furniture_styles import FurnitureStyleManifest
from ..infrastructure.office_layout_repository import (
    OfficeLayoutNotSeededError,
    OfficeLayoutRepository,
)


class FakeSettingsRepository:
    def __init__(self, layout: dict[str, object] | None = None) -> None:
        self._layout = layout

    async def layout(self) -> dict[str, object] | None:
        return self._layout

    async def set_layout(self, layout: dict[str, object]) -> None:
        self._layout = layout


def _flat_layout(cols: int = 3, rows: int = 3) -> dict[str, object]:
    return {
        "version": 1,
        "cols": cols,
        "rows": rows,
        "tiles": [1] * (cols * rows),
        "furniture": [],
    }


class TestOfficeLayoutRepository(unittest.IsolatedAsyncioTestCase):
    async def test_load_raises_when_nothing_seeded_yet(self) -> None:
        repository = OfficeLayoutRepository(FakeSettingsRepository(layout=None))

        with self.assertRaises(OfficeLayoutNotSeededError):
            await repository.load(FurnitureStyleManifest.from_raw({"styles": []}))

    async def test_load_decodes_the_stored_layout(self) -> None:
        repository = OfficeLayoutRepository(FakeSettingsRepository(layout=_flat_layout(4, 5)))

        office = await repository.load(FurnitureStyleManifest.from_raw({"styles": []}))

        self.assertIsInstance(office, Office)
        self.assertEqual(office.width, 4)
        self.assertEqual(office.height, 5)

    async def test_save_encodes_and_persists_and_returns_the_raw_json(self) -> None:
        settings = FakeSettingsRepository(layout=_flat_layout(3, 3))
        repository = OfficeLayoutRepository(settings)
        styles = FurnitureStyleManifest.from_raw({"styles": []})
        office = await repository.load(styles)

        raw = await repository.save(office, styles)

        self.assertEqual(raw["cols"], 3)
        self.assertEqual(await settings.layout(), raw)
