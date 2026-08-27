"""End-to-end proof of docs/architect-semantic-ir-design.md's central claim
-- lossless read/write through the *whole* stack a real caller uses
(`OfficeLayoutService` -> `OfficeLayoutRepository` -> the adapter), not
just `decode()`/`encode()` in isolation (already covered in depth by
test_pixel_agents_adapter.py's own `TestRoundTrip`). This exercises a
genuinely irregular, hand-authored-style layout -- per-tile pattern
variation, a real multi-tile desk, a wall painted next to it, a resized
zone -- through a sequence of service mutations, and checks that nothing
any mutation didn't touch drifts across a save + reload."""

from __future__ import annotations

import unittest

from ..application.office_layout_service import OfficeLayoutService
from ..domain.office_ir import FurnitureKind, GridPosition, GridRect, TileKind
from ..infrastructure.furniture_styles import FurnitureStyleLoader
from ..infrastructure.office_layout_repository import OfficeLayoutRepository
from .conftest import FakePixelAgents

_MANIFEST = {
    "styles": [
        {
            "style": "desk",
            "kind": "desk",
            "label": "Desk",
            "can_place_on_walls": False,
            "can_place_on_surfaces": False,
            "facings": {
                "south": {
                    "catalog_id": "DESK_FRONT",
                    "footprint_width": 3,
                    "footprint_height": 2,
                    "background_tiles": 1,
                }
            },
            "default_facing": "south",
        },
        {
            "style": "whiteboard",
            "kind": "wall_fixture",
            "label": "Whiteboard",
            "can_place_on_walls": True,
            "can_place_on_surfaces": False,
            "facings": {},
            "default_facing": None,
            "catalog_id": "WHITEBOARD",
            "footprint_width": 1,
            "footprint_height": 1,
            "background_tiles": 0,
        },
    ]
}


def _irregular_layout(cols: int = 10, rows: int = 8) -> dict[str, object]:
    """Not uniform anywhere: a checkerboard of two floor patterns inside
    an otherwise-wall grid, plus a hand-placed void pocket -- the kind of
    layout v1's "always emit pattern 1" `encode()` could never reproduce."""

    tiles: list[int] = []
    for row in range(rows):
        for col in range(cols):
            if 1 <= col <= 6 and 1 <= row <= 5:
                tiles.append(1 if (col + row) % 2 == 0 else 2)
            else:
                tiles.append(0)
    # A deliberate void pocket -- must survive untouched through every
    # mutation below.
    tiles[_index(cols, 7, 6)] = 255
    return {
        "version": 1,
        "cols": cols,
        "rows": rows,
        "tiles": tiles,
        "furniture": [],
    }


def _index(cols: int, col: int, row: int) -> int:
    return row * cols + col


class FakeSettingsRepository:
    def __init__(self, layout: dict[str, object]) -> None:
        self._layout: dict[str, object] | None = layout

    async def layout(self) -> dict[str, object] | None:
        return self._layout

    async def set_layout(self, layout: dict[str, object]) -> None:
        self._layout = layout


def _service(settings: FakeSettingsRepository) -> OfficeLayoutService:
    repository = OfficeLayoutRepository(settings)
    loader = FurnitureStyleLoader(FakePixelAgents(furniture_styles=_MANIFEST))
    return OfficeLayoutService(repository, loader)


class TestEndToEndLosslessRoundTrip(unittest.IsolatedAsyncioTestCase):
    async def test_untouched_checkerboard_and_void_pocket_survive_every_mutation(self) -> None:
        settings = FakeSettingsRepository(_irregular_layout())
        service = _service(settings)

        office = await service.describe()
        checkerboard_before = [
            office.grid.at(GridPosition(col, row)).material
            for row in range(1, 6)
            for col in range(1, 7)
        ]
        self.assertEqual(office.grid.at(GridPosition(7, 6)).kind, TileKind.VOID)

        await service.paint_tiles(
            area=GridRect(GridPosition(1, 1), 3, 3), kind=TileKind.FLOOR, material=3
        )
        await service.place_furniture(
            kind=FurnitureKind.DESK,
            style="desk",
            position=GridPosition(1, 1),
        )
        # (2,3) is inside the repainted 3x3 area but outside the desk's
        # footprint ((1,2)-(3,2)) -- free to paint to wall.
        await service.paint_tiles(area=GridRect(GridPosition(2, 3), 1, 1), kind=TileKind.WALL)
        zone = await service.create_zone(
            label="Focus", color="cool_blue", tiles=GridRect(GridPosition(5, 3), 2, 2)
        )
        await service.resize_zone(zone_id=zone.id, tiles=GridRect(GridPosition(5, 3), 1, 3))

        # Everything outside the areas explicitly touched above must be
        # byte-for-byte identical to what decode() originally produced --
        # this is the actual "lossless" claim: mutating one part of the
        # office must never perturb an unrelated part. The 3x3 rect
        # (cols 1-3, rows 1-3) was fully repainted to material=3, and
        # (2,3) inside it was further painted to wall.
        def _touched(col: int, row: int) -> bool:
            return 1 <= col <= 3 and 1 <= row <= 3

        positions = [(row, col) for row in range(1, 6) for col in range(1, 7)]
        final = await service.describe()
        checkerboard_after = [
            final.grid.at(GridPosition(col, row)).material
            for row, col in positions
            if not _touched(col, row)
        ]
        checkerboard_before_filtered = [
            value
            for (row, col), value in zip(positions, checkerboard_before, strict=True)
            if not _touched(col, row)
        ]
        self.assertEqual(checkerboard_after, checkerboard_before_filtered)
        self.assertEqual(final.grid.at(GridPosition(7, 6)).kind, TileKind.VOID)

    async def test_reload_after_every_mutation_reproduces_the_same_office(self) -> None:
        settings = FakeSettingsRepository(_irregular_layout())
        service = _service(settings)

        await service.paint_tiles(
            area=GridRect(GridPosition(1, 1), 3, 3),
            kind=TileKind.FLOOR,
            material=4,
            color="cool_blue",
        )
        item = await service.place_furniture(
            kind=FurnitureKind.DESK, style="desk", position=GridPosition(1, 1)
        )
        zone = await service.create_zone(
            label="Focus", color="warm_beige", tiles=GridRect(GridPosition(5, 3), 2, 2)
        )

        before = await service.describe()
        # Force a second, independent load through the repository -- this
        # is the actual round trip: encode() -> persisted JSON -> decode()
        # again, exercising the id<->uid map.
        after = await service.describe()

        self.assertEqual(before.grid.cells, after.grid.cells)
        self.assertEqual([z.id for z in before.zones], [z.id for z in after.zones])
        self.assertEqual([f.id for f in before.furniture], [f.id for f in after.furniture])
        self.assertEqual(item.id, after.furniture[0].id)
        self.assertEqual(zone.id, after.zones[0].id)

    async def test_paint_floor_preserves_zone_tag_on_repaint(self) -> None:
        settings = FakeSettingsRepository(_irregular_layout())
        service = _service(settings)
        await service.paint_tiles(
            area=GridRect(GridPosition(1, 1), 3, 3), kind=TileKind.FLOOR, material=3
        )
        await service.create_zone(
            label="Focus", color="cool_blue", tiles=GridRect(GridPosition(1, 1), 2, 2)
        )

        await service.paint_tiles(
            area=GridRect(GridPosition(1, 1), 1, 1), kind=TileKind.FLOOR, material=7
        )

        tile = (await service.describe_tiles(area=GridRect(GridPosition(1, 1), 1, 1)))[0]
        self.assertEqual(tile.material, 7)
        self.assertEqual(tile.zone_label, "Focus")
