"""Painter's own OfficeLayoutRepository reads/writes the same "editor"
aggregate architect's does, through pixelagents' `OfficeStateFacade` --
see docs/painter-design.md part A and docs/cctv-design.md."""

from __future__ import annotations

import unittest

from pixelagents.domain import Office
from pixelagents.infrastructure.furniture_styles import FurnitureStyleManifest

from ..infrastructure.office_layout_repository import (
    OfficeLayoutNotSeededError,
    OfficeLayoutRepository,
)
from .conftest import FakeCorridor, FakePixelAgents


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
        pixelagents = FakePixelAgents(corridor=FakeCorridor(), default_layout=None)
        repository = OfficeLayoutRepository(pixelagents.office_state)

        with self.assertRaises(OfficeLayoutNotSeededError):
            await repository.load(FurnitureStyleManifest.from_raw({"styles": []}))

    async def test_load_decodes_the_stored_layout(self) -> None:
        corridor = FakeCorridor()
        await corridor.set_office_layout("editor", _flat_layout(4, 5))
        pixelagents = FakePixelAgents(corridor=corridor, default_layout=None)
        repository = OfficeLayoutRepository(pixelagents.office_state)

        office = await repository.load(FurnitureStyleManifest.from_raw({"styles": []}))

        self.assertIsInstance(office, Office)
        self.assertEqual(office.width, 4)
        self.assertEqual(office.height, 5)

    async def test_save_encodes_and_persists(self) -> None:
        corridor = FakeCorridor()
        await corridor.set_office_layout("editor", _flat_layout(3, 3))
        pixelagents = FakePixelAgents(corridor=corridor, default_layout=None)
        repository = OfficeLayoutRepository(pixelagents.office_state)
        styles = FurnitureStyleManifest.from_raw({"styles": []})
        office = await repository.load(styles)

        await repository.save(office, styles)

        state = await corridor.read_office_state("editor")
        self.assertEqual(state.layout["cols"], 3)
