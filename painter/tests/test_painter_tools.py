"""LLM tool wrappers around PainterLayoutService -- schema shape, region
resolution (width/height vs end_col/end_row, mirroring architect's own
paint_tiles convention), and PainterValidationError -> Output.status="error"
translation."""

from __future__ import annotations

import types
import unittest

from pixelagents.domain import GridPosition, GridRect
from pixelagents.infrastructure.furniture_styles import FurnitureStyleLoader
from pixelagents.infrastructure.pixel_agents_adapter import decode, encode

from ..application.painter_layout_service import PainterLayoutService
from ..infrastructure.office_layout_repository import OfficeLayoutRepository
from ..tools.painter_tools import (
    ColorSpec,
    DescribeFurnitureColorsInput,
    DescribeFurnitureColorsTool,
    DescribeTileColorsInput,
    DescribeTileColorsTool,
    RecolorFurnitureByStyleInput,
    RecolorFurnitureByStyleTool,
    RecolorFurnitureInput,
    RecolorFurnitureTool,
    RecolorTilesInput,
    RecolorTilesTool,
    build_painter_tools,
)

_BLUE = ColorSpec(hue=220, saturation=80)
_BEIGE = ColorSpec(hue=35, saturation=30, brightness=15)

_MANIFEST = {
    "styles": [
        {
            "style": "wooden_chair",
            "kind": "seating",
            "label": "Wooden Chair",
            "can_place_on_walls": False,
            "can_place_on_surfaces": False,
            "facings": {
                "south": {
                    "catalog_id": "WOODEN_CHAIR_FRONT",
                    "footprint_width": 1,
                    "footprint_height": 1,
                    "background_tiles": 0,
                }
            },
            "default_facing": "south",
        },
    ]
}


class FakeOfficeState:
    """A minimal stand-in for pixelagents' `OfficeStateFacade`, satisfying
    `OfficeLayoutRepository`'s `SupportsEditorOffice` protocol directly --
    this file only needs the "editor" aggregate's decode/encode round
    trip, not the full facade machinery (lazy seeding, corridor plumbing)
    `painter/tests/conftest.py`'s own `FakePixelAgents` wires up."""

    def __init__(self, layout: dict[str, object]) -> None:
        self._layout = layout

    async def load_editor_office(self, styles: object) -> object:
        return decode(self._layout, styles)  # type: ignore[arg-type]

    async def set_editor_layout(self, office: object, styles: object) -> None:
        self._layout = encode(office, styles)  # type: ignore[arg-type]


class FakePixelAgents:
    def __init__(self, furniture_styles: dict[str, object]) -> None:
        self._furniture_styles = furniture_styles

    def webview_bundle_status(self) -> object:
        return types.SimpleNamespace(ready=True, built_commit="a" * 40)

    def furniture_style_manifest(self) -> dict[str, object]:
        return self._furniture_styles


def _layout() -> dict[str, object]:
    return {
        "version": 1,
        "cols": 5,
        "rows": 1,
        "tiles": [1, 1, 1, 1, 1],
        "furniture": [
            {"uid": "f-1", "type": "WOODEN_CHAIR_FRONT", "col": 0, "row": 0},
        ],
    }


def _service() -> PainterLayoutService:
    office_state = FakeOfficeState(_layout())
    repository = OfficeLayoutRepository(lambda: office_state)
    loader = FurnitureStyleLoader(FakePixelAgents(_MANIFEST))
    return PainterLayoutService(repository, loader)


class TestRecolorTilesRegionResolution(unittest.IsolatedAsyncioTestCase):
    async def test_width_height_and_end_col_end_row_paint_the_identical_region(self) -> None:
        service = _service()
        by_width = RecolorTilesTool(service)
        by_corner = RecolorTilesTool(service)

        out_a = await by_width.handler(
            RecolorTilesInput(col=1, row=0, width=3, height=1, color=_BLUE)
        )
        out_b = await by_corner.handler(
            RecolorTilesInput(col=1, row=0, end_col=3, end_row=0, color=_BEIGE)
        )

        self.assertEqual(out_a.status, "ok")  # type: ignore[attr-defined]
        self.assertEqual(out_b.status, "ok")  # type: ignore[attr-defined]
        cells = await service.describe_tile_colors(area=GridRect(GridPosition(1, 0), 3, 1))
        self.assertEqual({c.raw_color for c in cells}, {(35, 30, 15, 0)})

    async def test_rejects_both_width_height_and_end_col_end_row(self) -> None:
        tool = RecolorTilesTool(_service())

        output = await tool.handler(
            RecolorTilesInput(col=0, row=0, width=1, height=1, end_col=0, end_row=0, color=_BLUE)
        )

        self.assertEqual(output.status, "error")  # type: ignore[attr-defined]

    async def test_rejects_neither_width_height_nor_end_col_end_row(self) -> None:
        tool = RecolorTilesTool(_service())

        output = await tool.handler(RecolorTilesInput(col=0, row=0, color=_BLUE))

        self.assertEqual(output.status, "error")  # type: ignore[attr-defined]

    async def test_rejects_end_col_before_col(self) -> None:
        tool = RecolorTilesTool(_service())

        output = await tool.handler(
            RecolorTilesInput(col=3, row=0, end_col=1, end_row=0, color=_BLUE)
        )

        self.assertEqual(output.status, "error")  # type: ignore[attr-defined]
        self.assertIn("end_col", output.message)  # type: ignore[attr-defined]

    async def test_hex_color_is_accepted(self) -> None:
        tool = RecolorTilesTool(_service())

        output = await tool.handler(
            RecolorTilesInput(col=0, row=0, width=1, height=1, color=ColorSpec(hex="#3b5a7a"))
        )

        self.assertEqual(output.status, "ok")  # type: ignore[attr-defined]

    async def test_rejects_both_hex_and_hue_saturation(self) -> None:
        tool = RecolorTilesTool(_service())

        output = await tool.handler(
            RecolorTilesInput(
                col=0,
                row=0,
                width=1,
                height=1,
                color=ColorSpec(hex="#3b5a7a", hue=200, saturation=50),
            )
        )

        self.assertEqual(output.status, "error")  # type: ignore[attr-defined]

    async def test_rejects_neither_hex_nor_hue_saturation(self) -> None:
        tool = RecolorTilesTool(_service())

        output = await tool.handler(
            RecolorTilesInput(col=0, row=0, width=1, height=1, color=ColorSpec())
        )

        self.assertEqual(output.status, "error")  # type: ignore[attr-defined]

    async def test_rejects_a_malformed_hex_string(self) -> None:
        tool = RecolorTilesTool(_service())

        output = await tool.handler(
            RecolorTilesInput(col=0, row=0, width=1, height=1, color=ColorSpec(hex="not-a-color"))
        )

        self.assertEqual(output.status, "error")  # type: ignore[attr-defined]
        self.assertIn("not-a-color", output.message or "")  # type: ignore[attr-defined]

    async def test_service_validation_error_becomes_an_error_output(self) -> None:
        tool = RecolorTilesTool(_service())

        output = await tool.handler(
            RecolorTilesInput(col=0, row=0, width=99, height=1, color=_BLUE)
        )

        self.assertEqual(output.status, "error")  # type: ignore[attr-defined]
        self.assertIn("outside", output.message or "")  # type: ignore[attr-defined]


class TestRecolorFurnitureTool(unittest.IsolatedAsyncioTestCase):
    async def test_recolors_a_known_item(self) -> None:
        tool = RecolorFurnitureTool(_service())

        output = await tool.handler(RecolorFurnitureInput(furniture_id="f-1", color=_BLUE))

        self.assertEqual(output.status, "ok")  # type: ignore[attr-defined]

    async def test_unknown_id_reports_an_error(self) -> None:
        tool = RecolorFurnitureTool(_service())

        output = await tool.handler(RecolorFurnitureInput(furniture_id="nope", color=_BLUE))

        self.assertEqual(output.status, "error")  # type: ignore[attr-defined]


class TestRecolorFurnitureByStyleTool(unittest.IsolatedAsyncioTestCase):
    async def test_reports_how_many_were_recolored(self) -> None:
        tool = RecolorFurnitureByStyleTool(_service())

        output = await tool.handler(
            RecolorFurnitureByStyleInput(kind="seating", style="wooden_chair", color=_BLUE)
        )

        self.assertEqual(output.status, "ok")  # type: ignore[attr-defined]
        self.assertEqual(output.recolored_count, 1)  # type: ignore[attr-defined]

    async def test_zero_matches_is_ok_not_an_error(self) -> None:
        tool = RecolorFurnitureByStyleTool(_service())

        output = await tool.handler(
            RecolorFurnitureByStyleInput(kind="desk", style="standing_desk", color=_BLUE)
        )

        self.assertEqual(output.status, "ok")  # type: ignore[attr-defined]
        self.assertEqual(output.recolored_count, 0)  # type: ignore[attr-defined]


class TestDescribeTools(unittest.IsolatedAsyncioTestCase):
    async def test_describe_tile_colors_reports_the_region(self) -> None:
        tool = DescribeTileColorsTool(_service())

        output = await tool.handler(DescribeTileColorsInput(col=0, row=0, width=2, height=1))

        self.assertEqual(output.status, "ok")  # type: ignore[attr-defined]
        self.assertEqual(len(output.tiles), 2)  # type: ignore[attr-defined]

    async def test_describe_tile_colors_reports_hex_and_hsb_not_a_bare_name(self) -> None:
        service = _service()
        await RecolorTilesTool(service).handler(
            RecolorTilesInput(col=0, row=0, width=1, height=1, color=_BLUE)
        )
        tool = DescribeTileColorsTool(service)

        output = await tool.handler(DescribeTileColorsInput(col=0, row=0, width=1, height=1))

        color = output.tiles[0].color  # type: ignore[attr-defined]
        assert color is not None
        self.assertEqual(color.hue, 220)
        self.assertEqual(color.saturation, 80)
        self.assertTrue(color.hex.startswith("#"))
        self.assertTrue(color.closest_named_color)

    async def test_describe_furniture_colors_reports_matches(self) -> None:
        tool = DescribeFurnitureColorsTool(_service())

        output = await tool.handler(DescribeFurnitureColorsInput())

        self.assertEqual(output.status, "ok")  # type: ignore[attr-defined]
        self.assertEqual([f.id for f in output.furniture], ["f-1"])  # type: ignore[attr-defined]


class TestBuildPainterTools(unittest.TestCase):
    def test_returns_all_five_tools(self) -> None:
        tools = build_painter_tools(_service())

        self.assertEqual(
            [tool.name for tool in tools],
            [
                "describe_tile_colors",
                "describe_furniture_colors",
                "recolor_tiles",
                "recolor_furniture",
                "recolor_furniture_by_style",
            ],
        )
