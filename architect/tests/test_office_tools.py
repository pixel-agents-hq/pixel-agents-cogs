"""LLM tools for office layout mutation -- docs/architect-semantic-ir-design.md
section 7. Covers schema shape (including the dynamic style/facing enum)
and handler behavior, mirroring test_placeholder_tools.py's shape."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from ..application.office_layout_service import OfficeLayoutService
from ..domain.office_ir import FurnitureKind, GridPosition, GridRect, TileKind
from ..infrastructure.furniture_styles import FurnitureStyleLoader
from ..infrastructure.office_layout_repository import OfficeLayoutRepository
from ..tools.office_tools import (
    DescribeOfficeInput,
    DescribeOfficeTool,
    DescribeTilesInput,
    DescribeTilesTool,
    FindFurnitureAnchorsTool,
    FindFurnitureInput,
    FindFurnitureTool,
    ListFurnitureStylesInput,
    ListFurnitureStylesTool,
    PaintTilesInput,
    PaintTilesTool,
    PlaceFurnitureTool,
    RemoveFurnitureInput,
    RemoveFurnitureTool,
    RemoveZoneInput,
    RemoveZoneTool,
    ResizeZoneInput,
    ResizeZoneTool,
    build_office_tools,
)
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
                }
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
                }
            },
            "default_facing": "south",
            "can_place_on_walls": False,
            "can_place_on_surfaces": False,
        },
        {
            "style": "whiteboard",
            "kind": "wall_fixture",
            "label": "Whiteboard",
            "catalog_id": "WHITEBOARD",
            "footprint_width": 2,
            "footprint_height": 2,
            "background_tiles": 0,
            "can_place_on_walls": True,
            "can_place_on_surfaces": False,
        },
    ]
}


class FakeSettingsRepository:
    def __init__(self) -> None:
        self._layout: dict[str, object] | None = {
            "version": 1,
            "cols": 5,
            "rows": 5,
            "tiles": [1] * 25,
            "furniture": [],
        }

    async def layout(self) -> dict[str, object] | None:
        return self._layout

    async def set_layout(self, layout: dict[str, object]) -> None:
        self._layout = layout


def _service() -> OfficeLayoutService:
    repository = OfficeLayoutRepository(FakeSettingsRepository())
    loader = FurnitureStyleLoader(FakePixelAgents(furniture_styles=_MANIFEST))
    return OfficeLayoutService(repository, loader)


def _loader() -> FurnitureStyleLoader:
    return FurnitureStyleLoader(FakePixelAgents(furniture_styles=_MANIFEST))


class TestBuildOfficeTools(unittest.TestCase):
    def test_returns_all_tools_with_unique_names(self) -> None:
        service = _service()
        loader = _loader()

        tools = build_office_tools(service, loader)

        names = {tool.name for tool in tools}  # type: ignore[attr-defined]
        self.assertEqual(len(names), 14)


class TestDescribeOfficeTool(unittest.IsolatedAsyncioTestCase):
    async def test_reports_empty_office(self) -> None:
        tool = DescribeOfficeTool(_service(), _loader())

        output = await tool.handler(DescribeOfficeInput())

        self.assertEqual(output.width, 5)
        self.assertEqual(output.zones, [])
        self.assertEqual(output.furniture, [])


class TestPlaceFurnitureToolDynamicSchema(unittest.IsolatedAsyncioTestCase):
    async def test_style_enum_reflects_the_live_manifest(self) -> None:
        loader = _loader()
        tool = PlaceFurnitureTool(_service(), loader)

        schema = tool.Input.model_json_schema()

        self.assertEqual(
            set(schema["properties"]["style"]["enum"]), {"desk", "wooden_chair", "whiteboard"}
        )

    async def test_style_enum_changes_when_the_manifest_changes(self) -> None:
        fake = FakePixelAgents(furniture_styles=_MANIFEST, built_commit="a" * 40)
        loader = FurnitureStyleLoader(fake)
        tool = PlaceFurnitureTool(_service(), loader)
        first_schema = tool.Input.model_json_schema()
        self.assertEqual(
            set(first_schema["properties"]["style"]["enum"]),
            {"desk", "wooden_chair", "whiteboard"},
        )

        fake._furniture_styles = {
            "styles": [
                {
                    "style": "whiteboard",
                    "kind": "wall_fixture",
                    "label": "Whiteboard",
                    "catalog_id": "WHITEBOARD",
                    "footprint_width": 1,
                    "footprint_height": 1,
                    "background_tiles": 0,
                    "facings": {},
                    "default_facing": None,
                    "can_place_on_walls": True,
                    "can_place_on_surfaces": False,
                }
            ]
        }
        fake.built_commit = "b" * 40  # invalidate the loader's cache

        second_schema = tool.Input.model_json_schema()
        # A single remaining style renders as a JSON Schema `const`, not a
        # one-item `enum` -- both mean "only this value is valid".
        self.assertEqual(second_schema["properties"]["style"].get("const"), "whiteboard")

    async def test_place_and_find_furniture_round_trip(self) -> None:
        service = _service()
        loader = _loader()
        place_tool = PlaceFurnitureTool(service, loader)

        place_output = await place_tool.handler(
            place_tool.Input.model_validate({"kind": "desk", "style": "desk", "col": 0, "row": 0})
        )
        self.assertEqual(place_output.status, "ok")
        assert place_output.item is not None
        self.assertTrue(place_output.item.occupied_cells)

        find_output = await FindFurnitureTool(service, loader).handler(FindFurnitureInput())
        self.assertEqual(len(find_output.furniture), 1)
        self.assertEqual(find_output.furniture[0].style, "desk")

    async def test_background_cells_are_exposed_for_flush_chair_placement(self) -> None:
        # DESK_FRONT is 3x2 with background_tiles=1 -- the anchor row is
        # the desk's north/back edge, excluded from occupied_cells, so
        # nothing is rejected for landing there. A chair "behind" the desk
        # belongs at that exact coordinate, not one tile further north.
        service = _service()
        loader = _loader()
        place_output = await PlaceFurnitureTool(service, loader).handler(
            PlaceFurnitureTool(service, loader).Input.model_validate(
                {"kind": "desk", "style": "desk", "col": 0, "row": 0}
            )
        )
        assert place_output.item is not None

        self.assertEqual(
            {(cell.col, cell.row) for cell in place_output.item.background_cells},
            {(0, 0), (1, 0), (2, 0)},
        )
        self.assertTrue(
            next(cell for cell in place_output.item.background_cells if cell.col == 0).is_anchor
        )

    async def test_unknown_style_is_rejected_by_the_schema_itself(self) -> None:
        loader = _loader()
        tool = PlaceFurnitureTool(_service(), loader)

        with self.assertRaises(ValidationError):
            tool.Input.model_validate(
                {"kind": "desk", "style": "not_a_real_style", "col": 0, "row": 0}
            )


class TestRemoveFurnitureTool(unittest.IsolatedAsyncioTestCase):
    async def test_removing_unknown_furniture_reports_error_not_exception(self) -> None:
        tool = RemoveFurnitureTool(_service())

        output = await tool.handler(RemoveFurnitureInput(furniture_id="nonexistent"))

        self.assertEqual(output.status, "error")
        self.assertIsNotNone(output.message)


class TestFindFurnitureTool(unittest.IsolatedAsyncioTestCase):
    async def test_find_all_with_no_filter(self) -> None:
        service = _service()
        loader = _loader()
        place_tool = PlaceFurnitureTool(service, loader)
        await place_tool.handler(
            place_tool.Input.model_validate({"kind": "desk", "style": "desk", "col": 0, "row": 0})
        )

        output = await FindFurnitureTool(service, loader).handler(FindFurnitureInput())

        self.assertEqual(output.status, "ok")
        self.assertEqual(len(output.furniture), 1)


class TestListFurnitureStylesTool(unittest.IsolatedAsyncioTestCase):
    async def test_lists_every_style_with_footprints(self) -> None:
        output = await ListFurnitureStylesTool(_loader()).handler(ListFurnitureStylesInput())

        by_style = {style.style: style for style in output.styles}
        self.assertEqual(set(by_style), {"desk", "wooden_chair", "whiteboard"})

        desk = by_style["desk"]
        self.assertFalse(desk.can_place_on_walls)
        self.assertEqual(len(desk.facings), 1)
        self.assertEqual(desk.facings[0].facing, "south")
        self.assertEqual(desk.facings[0].footprint_width, 3)
        self.assertEqual(desk.facings[0].footprint_height, 2)

        # Facing-less wall fixture: a single facing=None entry carrying
        # the style-level footprint, matching a real WHITEBOARD's shape
        # (see docs/architect-semantic-ir-design.md section 6.4).
        whiteboard = by_style["whiteboard"]
        self.assertTrue(whiteboard.can_place_on_walls)
        self.assertEqual(len(whiteboard.facings), 1)
        self.assertIsNone(whiteboard.facings[0].facing)
        self.assertEqual(whiteboard.facings[0].footprint_width, 2)
        self.assertEqual(whiteboard.facings[0].footprint_height, 2)

    async def test_filters_by_kind(self) -> None:
        output = await ListFurnitureStylesTool(_loader()).handler(
            ListFurnitureStylesInput(kind="wall_fixture")
        )

        self.assertEqual([style.style for style in output.styles], ["whiteboard"])


class TestResizeAndRemoveZoneTool(unittest.IsolatedAsyncioTestCase):
    async def test_resize_zone_via_tool(self) -> None:
        service = _service()
        zone = await service.create_zone(
            label="Quiet", color="cool_blue", tiles=GridRect(GridPosition(0, 0), 2, 2)
        )

        output = await ResizeZoneTool(service).handler(
            ResizeZoneInput(zone_id=zone.id, col=2, row=2, width=2, height=2)
        )

        self.assertEqual(output.status, "ok")
        assert output.zone is not None
        self.assertEqual((output.zone.col, output.zone.row), (2, 2))

    async def test_remove_zone_via_tool(self) -> None:
        service = _service()
        zone = await service.create_zone(
            label="Quiet", color="cool_blue", tiles=GridRect(GridPosition(0, 0), 2, 2)
        )

        output = await RemoveZoneTool(service).handler(RemoveZoneInput(zone_id=zone.id))

        self.assertEqual(output.status, "ok")
        self.assertEqual((await service.describe()).zones, [])

    async def test_remove_unknown_zone_reports_error(self) -> None:
        output = await RemoveZoneTool(_service()).handler(RemoveZoneInput(zone_id="nonexistent"))

        self.assertEqual(output.status, "error")


class TestDescribeTilesTool(unittest.IsolatedAsyncioTestCase):
    async def test_describes_a_bounded_region(self) -> None:
        service = _service()
        await service.paint_tiles(
            area=GridRect(GridPosition(0, 0), 2, 2), kind=TileKind.FLOOR, material=2
        )
        tool = DescribeTilesTool(service, _loader())

        output = await tool.handler(DescribeTilesInput(col=0, row=0, width=2, height=2))

        self.assertEqual(output.status, "ok")
        self.assertEqual(len(output.tiles), 4)
        self.assertTrue(all(tile.kind == "floor" and tile.material == 2 for tile in output.tiles))

    async def test_area_too_large_reports_error_not_exception(self) -> None:
        tool = DescribeTilesTool(_service(), _loader())

        output = await tool.handler(DescribeTilesInput(col=0, row=0, width=99, height=99))

        self.assertEqual(output.status, "error")

    async def test_reports_is_empty_when_no_furniture_occupies_the_region(self) -> None:
        service = _service()
        await service.paint_tiles(
            area=GridRect(GridPosition(0, 0), 2, 2), kind=TileKind.FLOOR, material=2
        )
        tool = DescribeTilesTool(service, _loader())

        output = await tool.handler(DescribeTilesInput(col=0, row=0, width=2, height=2))

        self.assertTrue(output.is_empty)
        self.assertEqual(output.blocking_furniture_ids, [])

    async def test_reports_blocking_furniture_ids_when_occupied(self) -> None:
        service = _service()
        await service.paint_tiles(
            area=GridRect(GridPosition(0, 0), 2, 2), kind=TileKind.FLOOR, material=2
        )
        chair = await service.place_furniture(
            kind=FurnitureKind.SEATING, style="wooden_chair", position=GridPosition(0, 0)
        )
        tool = DescribeTilesTool(service, _loader())

        output = await tool.handler(DescribeTilesInput(col=0, row=0, width=2, height=2))

        self.assertFalse(output.is_empty)
        self.assertEqual(output.blocking_furniture_ids, [chair.id])


class TestFindFurnitureAnchorsTool(unittest.IsolatedAsyncioTestCase):
    async def test_finds_anchors_for_a_wall_style(self) -> None:
        service = _service()
        # Row 3 is the only WALL row in an otherwise all-floor 5x5 grid.
        await service.paint_tiles(area=GridRect(GridPosition(0, 3), 5, 1), kind=TileKind.WALL)
        tool = FindFurnitureAnchorsTool(service, _loader())

        output = await tool.handler(
            tool.Input.model_validate(
                {"style": "whiteboard", "col": 0, "row": 2, "width": 5, "height": 2}
            )
        )

        self.assertEqual(output.status, "ok")
        # whiteboard is 2x2: col=4 would need col 5 too, out of the 5-wide
        # grid, so only cols 0-3 qualify -- all anchored at row 2 (bottom
        # row 2+2-1=3, the painted WALL row).
        self.assertEqual(
            {(anchor.col, anchor.row) for anchor in output.anchors},
            {(0, 2), (1, 2), (2, 2), (3, 2)},
        )

    async def test_area_too_large_reports_error_not_exception(self) -> None:
        tool = FindFurnitureAnchorsTool(_service(), _loader())

        output = await tool.handler(
            tool.Input.model_validate(
                {"style": "whiteboard", "col": 0, "row": 0, "width": 99, "height": 99}
            )
        )

        self.assertEqual(output.status, "error")


class TestPaintTilesTool(unittest.IsolatedAsyncioTestCase):
    async def test_paint_floor_via_tool(self) -> None:
        service = _service()
        tool = PaintTilesTool(service)

        output = await tool.handler(
            PaintTilesInput(col=0, row=0, width=1, height=1, kind="floor", material=5)
        )

        self.assertEqual(output.status, "ok")
        tiles = await service.describe_tiles(area=GridRect(GridPosition(0, 0), 1, 1))
        self.assertEqual(tiles[0].kind, TileKind.FLOOR)
        self.assertEqual(tiles[0].material, 5)

    async def test_paint_floor_without_material_reports_error(self) -> None:
        tool = PaintTilesTool(_service())

        output = await tool.handler(PaintTilesInput(col=0, row=0, width=1, height=1, kind="floor"))

        self.assertEqual(output.status, "error")

    async def test_paint_wall_via_tool(self) -> None:
        service = _service()
        tool = PaintTilesTool(service)

        output = await tool.handler(PaintTilesInput(col=0, row=0, width=1, height=1, kind="wall"))

        self.assertEqual(output.status, "ok")
        tiles = await service.describe_tiles(area=GridRect(GridPosition(0, 0), 1, 1))
        self.assertEqual(tiles[0].kind, TileKind.WALL)
