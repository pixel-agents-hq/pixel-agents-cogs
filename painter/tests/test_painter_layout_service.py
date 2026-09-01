"""PainterLayoutService: painter's color-only mutation surface. Verifies
both what it does (recolor floor/wall/furniture, describe current colors)
and what it structurally cannot do (no kind/material parameter exists
anywhere in this module -- see docs/painter-design.md §7.4)."""

from __future__ import annotations

import types
import unittest
from collections.abc import Mapping

from corridor.domain import OfficeState, OfficeStateKind
from pixelagents.domain import FurnitureKind, GridPosition, GridRect
from pixelagents.infrastructure.furniture_styles import FurnitureStyleLoader

from ..application.painter_layout_service import PainterLayoutService, PainterValidationError
from ..infrastructure.office_layout_repository import OfficeLayoutRepository

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
                    "footprint_width": 1,
                    "footprint_height": 1,
                    "background_tiles": 0,
                }
            },
            "default_facing": "south",
        },
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


class FakeSettingsRepository:
    def __init__(self, layout: dict[str, object]) -> None:
        self._state = OfficeState(OfficeStateKind.EDITOR, layout, {}, 1)

    async def layout(self) -> dict[str, object] | None:
        return self._state.layout

    async def office_state(self, kind: OfficeStateKind) -> OfficeState:
        assert kind is OfficeStateKind.EDITOR
        return self._state

    async def set_office_layout(
        self, kind: OfficeStateKind, layout: Mapping[str, object]
    ) -> OfficeState:
        assert kind is OfficeStateKind.EDITOR
        self._state = OfficeState(kind, dict(layout), self._state.seats, self._state.revision + 1)
        return self._state


class FakePixelAgents:
    def __init__(self, furniture_styles: dict[str, object]) -> None:
        self._furniture_styles = furniture_styles

    def webview_bundle_status(self) -> object:
        return types.SimpleNamespace(ready=True, built_commit="a" * 40)

    def furniture_style_manifest(self) -> dict[str, object]:
        return self._furniture_styles


def _layout() -> dict[str, object]:
    # 4x1: floor(warm), wall(no color), void, floor(off-palette).
    return {
        "version": 1,
        "cols": 4,
        "rows": 1,
        "tiles": [1, 0, 255, 2],
        "tileColors": [
            {"h": 35, "s": 30, "b": 15, "c": 0},
            None,
            None,
            {"h": 214, "s": 30, "b": -100, "c": -55},
        ],
        "furniture": [
            {"uid": "f-1", "type": "DESK_FRONT", "col": 0, "row": 0},
            {"uid": "f-2", "type": "WOODEN_CHAIR_FRONT", "col": 1, "row": 0},
            {
                "uid": "f-3",
                "type": "WOODEN_CHAIR_FRONT",
                "col": 2,
                "row": 0,
                "color": {"h": 0, "s": 60, "b": 10, "c": 0},
            },
        ],
    }


def _service(settings: FakeSettingsRepository) -> PainterLayoutService:
    repository = OfficeLayoutRepository(lambda: settings)
    loader = FurnitureStyleLoader(FakePixelAgents(_MANIFEST))
    return PainterLayoutService(repository, loader)


class TestDescribeTileColors(unittest.IsolatedAsyncioTestCase):
    async def test_reports_kind_and_color_per_cell(self) -> None:
        service = _service(FakeSettingsRepository(_layout()))

        cells = await service.describe_tile_colors(area=GridRect(GridPosition(0, 0), 4, 1))

        self.assertEqual(cells[0].kind.value, "floor")
        self.assertEqual(cells[0].color, "warm_beige")
        self.assertEqual(cells[1].kind.value, "wall")
        self.assertIsNone(cells[1].color)
        self.assertEqual(cells[2].kind.value, "void")
        self.assertEqual(cells[3].kind.value, "floor")
        self.assertEqual(cells[3].color, "cool_blue")

    async def test_out_of_bounds_area_raises(self) -> None:
        service = _service(FakeSettingsRepository(_layout()))

        with self.assertRaises(PainterValidationError):
            await service.describe_tile_colors(area=GridRect(GridPosition(0, 0), 10, 10))


class TestDescribeFurnitureColors(unittest.IsolatedAsyncioTestCase):
    async def test_reports_every_item_with_no_filter(self) -> None:
        service = _service(FakeSettingsRepository(_layout()))

        items = await service.describe_furniture_colors()

        self.assertEqual({item.id for item in items}, {"f-1", "f-2", "f-3"})

    async def test_filters_by_kind_and_style(self) -> None:
        service = _service(FakeSettingsRepository(_layout()))

        items = await service.describe_furniture_colors(
            kind=FurnitureKind.SEATING, style="wooden_chair"
        )

        self.assertEqual({item.id for item in items}, {"f-2", "f-3"})


_BLUE = {"h": 220, "s": 80, "b": 0, "c": 0}
_GREEN = {"h": 120, "s": 60, "b": 10, "c": 0}
_YELLOW = {"h": 50, "s": 90, "b": 5, "c": 0}
_PURPLE = {"h": 280, "s": 70, "b": -10, "c": 0}
_GRAY = {"h": 0, "s": 0, "b": 0, "c": 0}


class TestRecolorTiles(unittest.IsolatedAsyncioTestCase):
    async def test_recolors_a_floor_cell_keeping_its_material(self) -> None:
        settings = FakeSettingsRepository(_layout())
        service = _service(settings)

        await service.recolor_tiles(area=GridRect(GridPosition(0, 0), 1, 1), color=_BLUE)

        cells = await service.describe_tile_colors(area=GridRect(GridPosition(0, 0), 1, 1))
        self.assertEqual(cells[0].raw_color, (220, 80, 0, 0))
        persisted = await settings.layout()
        assert persisted is not None
        self.assertEqual(persisted["tiles"][0], 1)  # material unchanged
        self.assertEqual(persisted["tileColors"][0], {"h": 220, "s": 80, "b": 0, "c": 0})

    async def test_recolors_a_wall_cell(self) -> None:
        settings = FakeSettingsRepository(_layout())
        service = _service(settings)

        await service.recolor_tiles(area=GridRect(GridPosition(1, 0), 1, 1), color=_GREEN)

        cells = await service.describe_tile_colors(area=GridRect(GridPosition(1, 0), 1, 1))
        self.assertEqual(cells[0].kind.value, "wall")
        self.assertEqual(cells[0].raw_color, (120, 60, 10, 0))
        persisted = await settings.layout()
        assert persisted is not None
        self.assertEqual(persisted["tiles"][1], 0)  # still a wall, not converted

    async def test_recolors_a_mixed_floor_and_wall_region_preserving_each_kind(self) -> None:
        settings = FakeSettingsRepository(_layout())
        service = _service(settings)

        await service.recolor_tiles(area=GridRect(GridPosition(0, 0), 2, 1), color=_YELLOW)

        cells = await service.describe_tile_colors(area=GridRect(GridPosition(0, 0), 2, 1))
        self.assertEqual([c.kind.value for c in cells], ["floor", "wall"])
        self.assertEqual([c.raw_color for c in cells], [(50, 90, 5, 0), (50, 90, 5, 0)])

    async def test_an_arbitrary_color_survives_a_reload_exactly(self) -> None:
        """The whole point of storing `raw_color` instead of a semantic
        name: an arbitrary painter-chosen color (not one of architect's
        dozen fixed palette entries) must round-trip losslessly, not snap
        to the nearest named color on the next load."""

        off_palette = {"h": 199, "s": 12, "b": -37, "c": 63}
        settings = FakeSettingsRepository(_layout())
        service = _service(settings)

        await service.recolor_tiles(area=GridRect(GridPosition(0, 0), 1, 1), color=off_palette)

        cells = await service.describe_tile_colors(area=GridRect(GridPosition(0, 0), 1, 1))
        self.assertEqual(cells[0].raw_color, (199, 12, -37, 63))

    async def test_refuses_to_recolor_void(self) -> None:
        service = _service(FakeSettingsRepository(_layout()))

        with self.assertRaises(PainterValidationError):
            await service.recolor_tiles(area=GridRect(GridPosition(2, 0), 1, 1), color=_BLUE)

    async def test_rejects_an_out_of_range_hue(self) -> None:
        service = _service(FakeSettingsRepository(_layout()))

        with self.assertRaises(PainterValidationError):
            await service.recolor_tiles(
                area=GridRect(GridPosition(0, 0), 1, 1),
                color={"h": 999, "s": 50, "b": 0, "c": 0},
            )

    async def test_rejects_an_out_of_bounds_area(self) -> None:
        service = _service(FakeSettingsRepository(_layout()))

        with self.assertRaises(PainterValidationError):
            await service.recolor_tiles(area=GridRect(GridPosition(0, 0), 99, 1), color=_BLUE)


class TestRecolorFurniture(unittest.IsolatedAsyncioTestCase):
    async def test_recolors_a_single_item_by_id(self) -> None:
        settings = FakeSettingsRepository(_layout())
        service = _service(settings)

        updated = await service.recolor_furniture(furniture_id="f-2", color=_PURPLE)

        self.assertEqual(updated.raw_color, (280, 70, -10, 0))
        items = await service.describe_furniture_colors()
        recolored = next(item for item in items if item.id == "f-2")
        self.assertEqual(recolored.raw_color, (280, 70, -10, 0))
        untouched = next(item for item in items if item.id == "f-3")
        self.assertIsNotNone(untouched.color)  # f-3's own original color survives

    async def test_unknown_furniture_id_raises(self) -> None:
        service = _service(FakeSettingsRepository(_layout()))

        with self.assertRaises(PainterValidationError):
            await service.recolor_furniture(furniture_id="does-not-exist", color=_BLUE)

    async def test_out_of_range_saturation_raises(self) -> None:
        service = _service(FakeSettingsRepository(_layout()))

        with self.assertRaises(PainterValidationError):
            await service.recolor_furniture(
                furniture_id="f-2", color={"h": 200, "s": 500, "b": 0, "c": 0}
            )


class TestRecolorFurnitureByStyle(unittest.IsolatedAsyncioTestCase):
    async def test_recolors_every_matching_item(self) -> None:
        service = _service(FakeSettingsRepository(_layout()))

        count = await service.recolor_furniture_by_style(
            kind=FurnitureKind.SEATING, style="wooden_chair", color=_GRAY
        )

        self.assertEqual(count, 2)
        items = await service.describe_furniture_colors(
            kind=FurnitureKind.SEATING, style="wooden_chair"
        )
        self.assertTrue(all(item.raw_color == (0, 0, 0, 0) for item in items))

    async def test_no_matches_returns_zero_not_an_error(self) -> None:
        service = _service(FakeSettingsRepository(_layout()))

        count = await service.recolor_furniture_by_style(
            kind=FurnitureKind.STORAGE, style="bookshelf", color=_BLUE
        )

        self.assertEqual(count, 0)

    async def test_out_of_range_contrast_raises(self) -> None:
        service = _service(FakeSettingsRepository(_layout()))

        with self.assertRaises(PainterValidationError):
            await service.recolor_furniture_by_style(
                kind=FurnitureKind.SEATING,
                style="wooden_chair",
                color={"h": 200, "s": 50, "b": 0, "c": -500},
            )
