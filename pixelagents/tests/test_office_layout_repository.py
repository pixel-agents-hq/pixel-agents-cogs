from __future__ import annotations

import unittest
from copy import deepcopy
from typing import Any

from corridor.domain import OfficeState, OfficeStateKind

from ..domain import Office
from ..infrastructure.furniture_styles import FurnitureStyleManifest
from ..infrastructure.office_layout_repository import OfficeLayoutRepository


def _flat_layout(cols: int = 3, rows: int = 3) -> dict[str, object]:
    return {
        "version": 1,
        "cols": cols,
        "rows": rows,
        "tiles": [1] * (cols * rows),
        "furniture": [],
    }


class _FakePixelAgents:
    """Minimal `SupportsOfficeState` fake -- both architect's and
    painter's own `test_office_layout_repository.py` exercise the same
    class through their own re-export shims; this is the canonical copy
    now that the implementation lives here."""

    def __init__(self, editor_layout: dict[str, Any]) -> None:
        self._state = OfficeState(
            kind=OfficeStateKind.EDITOR, layout=editor_layout, seats={}, revision=1
        )

    async def office_state(self, kind: OfficeStateKind) -> OfficeState:
        assert kind == OfficeStateKind.EDITOR
        return deepcopy(self._state)

    async def set_office_layout(self, kind: OfficeStateKind, layout: dict[str, Any]) -> OfficeState:
        assert kind == OfficeStateKind.EDITOR
        self._state = OfficeState(
            kind=kind,
            layout=deepcopy(layout),
            seats=self._state.seats,
            revision=self._state.revision + 1,
        )
        return deepcopy(self._state)


class TestOfficeLayoutRepository(unittest.IsolatedAsyncioTestCase):
    async def test_load_reads_the_editor_aggregate(self) -> None:
        pixelagents = _FakePixelAgents(_flat_layout(4, 5))
        repository = OfficeLayoutRepository(lambda: pixelagents)

        office = await repository.load(FurnitureStyleManifest.from_raw({"styles": []}))

        self.assertIsInstance(office, Office)
        self.assertEqual(office.width, 4)
        self.assertEqual(office.height, 5)

    async def test_save_writes_back_the_encoded_layout(self) -> None:
        pixelagents = _FakePixelAgents(_flat_layout())
        repository = OfficeLayoutRepository(lambda: pixelagents)
        styles = FurnitureStyleManifest.from_raw({"styles": []})
        office = await repository.load(styles)

        await repository.save(office, styles)

        saved = await pixelagents.office_state(OfficeStateKind.EDITOR)
        self.assertEqual(saved.layout["cols"], 3)
        self.assertEqual(saved.revision, 2)


if __name__ == "__main__":
    unittest.main()
