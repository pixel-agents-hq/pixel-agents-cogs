from __future__ import annotations

import unittest
from typing import Any

from ..application.office_layout_service import OfficeLayoutService, OfficeValidationError
from ..domain.office_ir import Direction, FurnitureKind, GridPosition, GridRect, TileKind
from ..infrastructure.furniture_styles import FurnitureStyleLoader
from ..infrastructure.office_layout_repository import OfficeLayoutRepository
from .conftest import FakePixelAgents

_MANIFEST = {
    "styles": [
        {
            "style": "desk",
            "kind": "desk",
            "label": "Desk",
            "facings": {
                "south": {
                    "catalog_id": "DESK_FRONT",
                    "footprint_width": 3,
                    "footprint_height": 2,
                    "background_tiles": 1,
                },
            },
            "default_facing": "south",
            "can_place_on_walls": False,
            "can_place_on_surfaces": False,
        },
        {
            "style": "wooden_chair",
            "kind": "seating",
            "label": "Wooden Chair",
            "facings": {
                "south": {
                    "catalog_id": "WOODEN_CHAIR_FRONT",
                    "footprint_width": 1,
                    "footprint_height": 1,
                    "background_tiles": 0,
                },
                "north": {
                    "catalog_id": "WOODEN_CHAIR_BACK",
                    "footprint_width": 1,
                    "footprint_height": 1,
                    "background_tiles": 0,
                },
            },
            "default_facing": "south",
            "can_place_on_walls": False,
            "can_place_on_surfaces": False,
        },
        {
            "style": "pc",
            "kind": "electronics",
            "label": "PC",
            "facings": {
                "south": {
                    "catalog_id": "PC_FRONT",
                    "footprint_width": 1,
                    "footprint_height": 1,
                    "background_tiles": 0,
                },
            },
            "default_facing": "south",
            "can_place_on_walls": False,
            "can_place_on_surfaces": True,
        },
        {
            "style": "whiteboard",
            "kind": "wall_fixture",
            "label": "Whiteboard",
            "catalog_id": "WHITEBOARD",
            "footprint_width": 1,
            "footprint_height": 1,
            "background_tiles": 0,
            "can_place_on_walls": True,
            "can_place_on_surfaces": False,
        },
        {
            "style": "hanging_plant",
            "kind": "decor",
            "label": "Hanging Plant",
            "catalog_id": "HANGING_PLANT",
            # Real shape (webview-ui's HANGING_PLANT manifest): a 2-tall
            # wall fixture whose anchor (top-left) sits one row *above*
            # the wall tile it's actually mounted on -- only the bottom
            # row has to be WALL (section 8's real rule, not the anchor).
            "footprint_width": 1,
            "footprint_height": 2,
            "background_tiles": 0,
            "can_place_on_walls": True,
            "can_place_on_surfaces": False,
        },
    ]
}


class FakeSettingsRepository:
    def __init__(self, layout: dict[str, object] | None) -> None:
        self._layout = layout

    async def layout(self) -> dict[str, object] | None:
        return self._layout

    async def set_layout(self, layout: dict[str, object]) -> None:
        self._layout = layout


def _empty_layout(cols: int = 5, rows: int = 5) -> dict[str, object]:
    return {
        "version": 1,
        "cols": cols,
        "rows": rows,
        "tiles": [0] * (cols * rows),
        "furniture": [],
    }


def _service(layout: dict[str, object] | None = None, broadcast: Any = None) -> OfficeLayoutService:
    settings = FakeSettingsRepository(layout if layout is not None else _empty_layout())
    repository = OfficeLayoutRepository(settings)
    loader = FurnitureStyleLoader(FakePixelAgents(furniture_styles=_MANIFEST))
    return OfficeLayoutService(repository, loader, broadcast=broadcast)


async def _paint_floor(service: OfficeLayoutService, area: GridRect, material: int = 1) -> None:
    await service.paint_tiles(area=area, kind=TileKind.FLOOR, material=material)


class TestPlaceFurniture(unittest.IsolatedAsyncioTestCase):
    async def test_place_on_a_floor_tile_succeeds(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 3, 3))

        item = await service.place_furniture(
            kind=FurnitureKind.DESK, style="desk", position=GridPosition(0, 0)
        )

        self.assertEqual(item.kind, FurnitureKind.DESK)
        self.assertEqual(item.facing, Direction.SOUTH)
        found = await service.find_furniture()
        self.assertEqual(found, [item])

    async def test_place_on_a_wall_tile_fails_for_a_floor_only_style(self) -> None:
        service = _service()

        with self.assertRaises(OfficeValidationError):
            # (0,0) is untouched WALL from _empty_layout().
            await service.place_furniture(
                kind=FurnitureKind.DESK, style="desk", position=GridPosition(0, 0)
            )

    async def test_place_unknown_style_fails(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 3, 3))

        with self.assertRaises(OfficeValidationError):
            await service.place_furniture(
                kind=FurnitureKind.DESK, style="not_a_style", position=GridPosition(0, 0)
            )

    async def test_place_mismatched_kind_fails(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 3, 3))

        with self.assertRaises(OfficeValidationError):
            await service.place_furniture(
                kind=FurnitureKind.SEATING, style="desk", position=GridPosition(0, 0)
            )

    async def test_overlapping_furniture_footprint_fails(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 4, 4))
        await service.place_furniture(
            kind=FurnitureKind.DESK, style="desk", position=GridPosition(0, 0)
        )

        # The desk's real footprint (3x2, background_tiles=1) blocks
        # (0,1)-(2,1) -- a second desk anchored one tile over still
        # overlaps that blocked row, even though the anchors differ.
        with self.assertRaises(OfficeValidationError):
            await service.place_furniture(
                kind=FurnitureKind.DESK, style="desk", position=GridPosition(1, 0)
            )

    async def test_furniture_background_tiles_do_not_block_placement(self) -> None:
        # The desk's top row is its `background_tiles` row -- excluded from
        # occupancy (section 6.4), so another item's anchor can legally
        # land there.
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 4, 4))
        await service.place_furniture(
            kind=FurnitureKind.DESK, style="desk", position=GridPosition(0, 0)
        )

        chair = await service.place_furniture(
            kind=FurnitureKind.SEATING, style="wooden_chair", position=GridPosition(0, 0)
        )

        self.assertEqual(chair.position, GridPosition(0, 0))

    async def test_bad_facing_for_style_fails(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 3, 3))

        with self.assertRaises(OfficeValidationError):
            await service.place_furniture(
                kind=FurnitureKind.DESK,
                style="desk",
                position=GridPosition(0, 0),
                facing=Direction.EAST,
            )

    async def test_wall_fixture_requires_a_wall_anchor(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 3, 3))

        with self.assertRaises(OfficeValidationError):
            # (0,0) is FLOOR (just painted), not WALL.
            await service.place_furniture(
                kind=FurnitureKind.WALL_FIXTURE, style="whiteboard", position=GridPosition(0, 0)
            )

    async def test_wall_fixture_on_a_wall_tile_succeeds(self) -> None:
        service = _service()
        # (4,4) was never painted -- still a WALL tile from _empty_layout().

        item = await service.place_furniture(
            kind=FurnitureKind.WALL_FIXTURE, style="whiteboard", position=GridPosition(4, 4)
        )

        self.assertEqual(item.position, GridPosition(4, 4))

    async def test_wall_fixture_with_multi_row_footprint_checks_the_bottom_row_not_the_anchor(
        self,
    ) -> None:
        # Regression test: hanging_plant is 1x2 -- its anchor (top-left)
        # legitimately sits one row *above* the wall tile it's actually
        # mounted on (section 8's real rule, ported from Pixel Agents' own
        # canPlaceFurniture/getWallPlacementRow in editorActions.ts).
        # Checking the anchor tile itself against WALL, as a single-tile
        # wall item would need, rejected every real multi-row wall fixture.
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(2, 2), 1, 1))
        # (2,3) -- the footprint's bottom row -- is left untouched, still
        # WALL from _empty_layout().

        item = await service.place_furniture(
            kind=FurnitureKind.DECOR, style="hanging_plant", position=GridPosition(2, 2)
        )

        self.assertEqual(item.position, GridPosition(2, 2))

    async def test_wall_fixture_fails_when_its_bottom_row_is_not_a_wall_tile(self) -> None:
        service = _service()
        # Both the anchor row and the footprint's bottom row are floor.
        await _paint_floor(service, GridRect(GridPosition(2, 2), 1, 2))

        with self.assertRaises(OfficeValidationError):
            await service.place_furniture(
                kind=FurnitureKind.DECOR, style="hanging_plant", position=GridPosition(2, 2)
            )

    async def test_surface_item_may_stack_onto_a_desk(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 4, 4))
        await service.place_furniture(
            kind=FurnitureKind.DESK, style="desk", position=GridPosition(0, 0)
        )

        # (0,1) is inside the desk's blocked footprint row -- only legal
        # because `pc`'s style has `can_place_on_surfaces=True` and the
        # occupant there is DESK-kind (section 8).
        pc = await service.place_furniture(
            kind=FurnitureKind.ELECTRONICS, style="pc", position=GridPosition(0, 1)
        )

        self.assertEqual(pc.position, GridPosition(0, 1))

    async def test_non_surface_item_may_not_stack_onto_a_desk(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 4, 4))
        await service.place_furniture(
            kind=FurnitureKind.DESK, style="desk", position=GridPosition(0, 0)
        )

        with self.assertRaises(OfficeValidationError):
            await service.place_furniture(
                kind=FurnitureKind.SEATING, style="wooden_chair", position=GridPosition(0, 1)
            )

    async def test_broadcast_is_invoked_on_successful_mutation(self) -> None:
        broadcasts: list[dict[str, object]] = []

        async def broadcast(raw: dict[str, object]) -> None:
            broadcasts.append(raw)

        service = _service(broadcast=broadcast)
        await _paint_floor(service, GridRect(GridPosition(0, 0), 3, 3))
        await service.place_furniture(
            kind=FurnitureKind.DESK, style="desk", position=GridPosition(0, 0)
        )

        self.assertEqual(len(broadcasts), 2)  # paint_tiles, then place_furniture


class TestMoveAndRemoveFurniture(unittest.IsolatedAsyncioTestCase):
    async def test_move_to_free_position_succeeds(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 4, 4))
        item = await service.place_furniture(
            kind=FurnitureKind.DESK, style="desk", position=GridPosition(0, 0)
        )

        moved = await service.move_furniture(furniture_id=item.id, position=GridPosition(1, 2))

        self.assertEqual(moved.position, GridPosition(1, 2))

    async def test_move_outside_the_grid_fails(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 2, 2))
        item = await service.place_furniture(
            kind=FurnitureKind.SEATING, style="wooden_chair", position=GridPosition(0, 0)
        )

        with self.assertRaises(OfficeValidationError):
            await service.move_furniture(furniture_id=item.id, position=GridPosition(99, 99))

    async def test_move_onto_a_wall_tile_fails(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 2, 2))
        item = await service.place_furniture(
            kind=FurnitureKind.SEATING, style="wooden_chair", position=GridPosition(0, 0)
        )

        with self.assertRaises(OfficeValidationError):
            # (4,4) is untouched WALL from _empty_layout().
            await service.move_furniture(furniture_id=item.id, position=GridPosition(4, 4))

    async def test_move_to_a_different_painted_area_succeeds(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 2, 2))
        await _paint_floor(service, GridRect(GridPosition(3, 3), 2, 2))
        item = await service.place_furniture(
            kind=FurnitureKind.SEATING, style="wooden_chair", position=GridPosition(0, 0)
        )

        moved = await service.move_furniture(furniture_id=item.id, position=GridPosition(3, 3))

        self.assertEqual(moved.position, GridPosition(3, 3))

    async def test_remove_deletes_the_item_and_its_seat(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 2, 2))
        item = await service.place_furniture(
            kind=FurnitureKind.SEATING, style="wooden_chair", position=GridPosition(0, 0)
        )
        office = await service.describe()
        # decode() re-derives seats from persisted furniture on every load
        # (docs/architect-semantic-ir-design.md section 6.1 step 3) -- the
        # chair just placed already has one.
        self.assertEqual(len(office.seats), 1)

        await service.remove_furniture(furniture_id=item.id)

        office = await service.describe()
        self.assertEqual(office.furniture, [])
        self.assertEqual(office.seats, [])

    async def test_remove_unknown_furniture_fails(self) -> None:
        service = _service()

        with self.assertRaises(OfficeValidationError):
            await service.remove_furniture(furniture_id="nonexistent")


class TestPaintTiles(unittest.IsolatedAsyncioTestCase):
    async def test_paint_floor_requires_material(self) -> None:
        service = _service()

        with self.assertRaises(OfficeValidationError):
            await service.paint_tiles(area=GridRect(GridPosition(0, 0), 2, 2), kind=TileKind.FLOOR)

    async def test_paint_floor_succeeds(self) -> None:
        service = _service()

        await service.paint_tiles(
            area=GridRect(GridPosition(0, 0), 2, 2),
            kind=TileKind.FLOOR,
            material=3,
            color="cool_blue",
        )

        tiles = await service.describe_tiles(area=GridRect(GridPosition(0, 0), 2, 2))
        self.assertTrue(all(tile.kind is TileKind.FLOOR and tile.material == 3 for tile in tiles))

    async def test_paint_wall_over_furniture_fails(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 3, 3))
        await service.place_furniture(
            kind=FurnitureKind.SEATING, style="wooden_chair", position=GridPosition(0, 0)
        )

        with self.assertRaises(OfficeValidationError):
            await service.paint_tiles(area=GridRect(GridPosition(0, 0), 1, 1), kind=TileKind.WALL)

    async def test_paint_wall_over_floor_succeeds(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 3, 3))

        await service.paint_tiles(area=GridRect(GridPosition(0, 0), 1, 1), kind=TileKind.WALL)

        tile = (await service.describe_tiles(area=GridRect(GridPosition(0, 0), 1, 1)))[0]
        self.assertEqual(tile.kind, TileKind.WALL)

    async def test_paint_floor_preserves_zone_label(self) -> None:
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 3, 3))
        await service.create_zone(
            label="Quiet", color="cool_blue", tiles=GridRect(GridPosition(0, 0), 1, 1)
        )

        await service.paint_tiles(
            area=GridRect(GridPosition(0, 0), 1, 1), kind=TileKind.FLOOR, material=5
        )

        tile = (await service.describe_tiles(area=GridRect(GridPosition(0, 0), 1, 1)))[0]
        self.assertEqual(tile.zone_label, "Quiet")
        self.assertEqual(tile.material, 5)

    async def test_paint_out_of_bounds_fails(self) -> None:
        service = _service()

        with self.assertRaises(OfficeValidationError):
            await service.paint_tiles(
                area=GridRect(GridPosition(0, 0), 99, 99), kind=TileKind.FLOOR, material=1
            )


class TestDescribeTiles(unittest.IsolatedAsyncioTestCase):
    async def test_area_too_large_fails(self) -> None:
        service = _service(_empty_layout(cols=30, rows=30))

        with self.assertRaises(OfficeValidationError):
            await service.describe_tiles(area=GridRect(GridPosition(0, 0), 21, 20))

    async def test_out_of_bounds_fails(self) -> None:
        service = _service()

        with self.assertRaises(OfficeValidationError):
            await service.describe_tiles(area=GridRect(GridPosition(0, 0), 99, 99))

    async def test_returns_one_cell_per_position(self) -> None:
        service = _service()

        tiles = await service.describe_tiles(area=GridRect(GridPosition(0, 0), 2, 3))

        self.assertEqual(len(tiles), 6)


class TestZones(unittest.IsolatedAsyncioTestCase):
    async def test_create_zone_with_unknown_color_fails(self) -> None:
        service = _service()

        with self.assertRaises(OfficeValidationError):
            await service.create_zone(
                label="Quiet", color="not_a_color", tiles=GridRect(GridPosition(0, 0), 2, 2)
            )

    async def test_duplicate_zone_label_fails(self) -> None:
        service = _service()
        await service.create_zone(
            label="Quiet", color="cool_blue", tiles=GridRect(GridPosition(0, 0), 2, 2)
        )

        with self.assertRaises(OfficeValidationError):
            await service.create_zone(
                label="Quiet", color="cool_blue", tiles=GridRect(GridPosition(2, 2), 2, 2)
            )

    async def test_create_zone_tags_exact_membership_on_the_grid(self) -> None:
        service = _service()

        await service.create_zone(
            label="Quiet", color="cool_blue", tiles=GridRect(GridPosition(0, 0), 2, 2)
        )

        tiles = await service.describe_tiles(area=GridRect(GridPosition(0, 0), 2, 2))
        self.assertTrue(all(tile.zone_label == "Quiet" for tile in tiles))

    async def test_resize_zone_moves_membership(self) -> None:
        service = _service()
        zone = await service.create_zone(
            label="Quiet", color="cool_blue", tiles=GridRect(GridPosition(0, 0), 2, 2)
        )

        updated = await service.resize_zone(
            zone_id=zone.id, tiles=GridRect(GridPosition(2, 2), 2, 2)
        )

        self.assertEqual(updated.tiles, GridRect(GridPosition(2, 2), 2, 2))
        old_tiles = await service.describe_tiles(area=GridRect(GridPosition(0, 0), 2, 2))
        new_tiles = await service.describe_tiles(area=GridRect(GridPosition(2, 2), 2, 2))
        self.assertTrue(all(tile.zone_label is None for tile in old_tiles))
        self.assertTrue(all(tile.zone_label == "Quiet" for tile in new_tiles))

    async def test_resize_unknown_zone_fails(self) -> None:
        service = _service()

        with self.assertRaises(OfficeValidationError):
            await service.resize_zone(
                zone_id="nonexistent", tiles=GridRect(GridPosition(0, 0), 2, 2)
            )

    async def test_remove_zone_clears_membership(self) -> None:
        service = _service()
        zone = await service.create_zone(
            label="Quiet", color="cool_blue", tiles=GridRect(GridPosition(0, 0), 2, 2)
        )

        await service.remove_zone(zone_id=zone.id)

        office = await service.describe()
        self.assertEqual(office.zones, [])
        tiles = await service.describe_tiles(area=GridRect(GridPosition(0, 0), 2, 2))
        self.assertTrue(all(tile.zone_label is None for tile in tiles))

    async def test_remove_unknown_zone_fails(self) -> None:
        service = _service()

        with self.assertRaises(OfficeValidationError):
            await service.remove_zone(zone_id="nonexistent")


class TestSeats(unittest.IsolatedAsyncioTestCase):
    async def test_seat_occupant_with_no_occupants_fails(self) -> None:
        # Occupants have no creation path yet (docs/architect-semantic-ir-design.md
        # section 4.1: modeled for future use) -- seat_occupant is exercised
        # here only against the "unknown occupant" validation path.
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 2, 2))
        await service.place_furniture(
            kind=FurnitureKind.SEATING, style="wooden_chair", position=GridPosition(0, 0)
        )

        with self.assertRaises(OfficeValidationError):
            await service.seat_occupant(seat_id="seat:whatever", occupant_id="nobody")

    async def test_vacate_unknown_seat_fails(self) -> None:
        service = _service()

        with self.assertRaises(OfficeValidationError):
            await service.vacate_seat(seat_id="nonexistent")

    async def test_vacate_already_empty_seat_succeeds_and_validates(self) -> None:
        # Regression test: vacate_seat used to persist without calling
        # self._validate(), unlike every other mutation method (section 8
        # step 3's invariant). No occupant-creation path exists yet
        # (section 4.1), so every seat is already empty -- this exercises
        # the success path and confirms validation doesn't spuriously
        # reject a legitimate no-op clear.
        service = _service()
        await _paint_floor(service, GridRect(GridPosition(0, 0), 2, 2))
        await service.place_furniture(
            kind=FurnitureKind.SEATING, style="wooden_chair", position=GridPosition(0, 0)
        )
        office = await service.describe()
        seat_id = office.seats[0].id

        updated = await service.vacate_seat(seat_id=seat_id)

        self.assertIsNone(updated.occupant_id)


class TestReplaceLayout(unittest.IsolatedAsyncioTestCase):
    """`replace_layout` backs the in-browser editor's `saveLayout` message
    (no Discord command or LLM tool calls it) -- unlike every other
    mutation, its input is a whole raw Pixel Agents layout, not an
    incremental IR change."""

    async def test_valid_layout_replaces_the_stored_office_wholesale(self) -> None:
        service = _service()
        raw = {
            "version": 1,
            "cols": 2,
            "rows": 1,
            "tiles": [1, 1],
            "furniture": [],
        }

        office = await service.replace_layout(raw=raw)

        self.assertEqual(office.width, 2)
        self.assertEqual(office.height, 1)
        described = await service.describe()
        self.assertEqual(described.width, 2)
        self.assertEqual(described.height, 1)

    async def test_valid_layout_with_furniture_is_decoded_and_validated(self) -> None:
        service = _service()
        raw = {
            "version": 1,
            "cols": 4,
            "rows": 4,
            "tiles": [1] * 16,
            "furniture": [{"uid": "d-1", "type": "DESK_FRONT", "col": 0, "row": 0}],
        }

        office = await service.replace_layout(raw=raw)

        self.assertEqual(len(office.furniture), 1)
        self.assertEqual(office.furniture[0].style, "desk")

    async def test_structurally_invalid_layout_raises_and_does_not_persist(self) -> None:
        service = _service()
        before = await service.describe()

        with self.assertRaises(KeyError):
            # Missing "cols"/"rows"/"tiles" entirely -- decode() itself
            # raises, which the caller (the WebSocket transport) is
            # responsible for catching, not this service.
            await service.replace_layout(raw={"furniture": []})

        after = await service.describe()
        self.assertEqual(before.width, after.width)
        self.assertEqual(before.height, after.height)

    async def test_layout_that_violates_placement_rules_raises_and_does_not_persist(self) -> None:
        service = _service()
        # A desk anchored directly on a WALL tile (_empty_layout() is
        # all-wall) fails the same section 8 placement rule
        # place_furniture/move_furniture already enforce.
        raw = {
            "version": 1,
            "cols": 4,
            "rows": 4,
            "tiles": [0] * 16,
            "furniture": [{"uid": "d-1", "type": "DESK_FRONT", "col": 0, "row": 0}],
        }

        with self.assertRaises(OfficeValidationError):
            await service.replace_layout(raw=raw)

        after = await service.describe()
        self.assertEqual(after.furniture, [])

    async def test_broadcast_is_invoked_on_successful_replace(self) -> None:
        broadcasts: list[dict[str, object]] = []

        async def broadcast(raw: dict[str, object]) -> None:
            broadcasts.append(raw)

        service = _service(broadcast=broadcast)

        await service.replace_layout(
            raw={"version": 1, "cols": 1, "rows": 1, "tiles": [1], "furniture": []}
        )

        self.assertEqual(len(broadcasts), 1)
        self.assertEqual(broadcasts[0]["cols"], 1)
