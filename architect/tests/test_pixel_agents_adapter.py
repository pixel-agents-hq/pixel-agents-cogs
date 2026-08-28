"""Tests for the Pixel Agents JSON <-> semantic IR adapter.
docs/architect-semantic-ir-design.md sections 6.1/6.2 (v2)."""

from __future__ import annotations

from dataclasses import replace

from ..domain.office_ir import (
    Direction,
    FurnitureItem,
    FurnitureKind,
    Grid,
    GridPosition,
    TileKind,
)
from ..infrastructure.color_names import hsb_for
from ..infrastructure.furniture_styles import FurnitureStyleManifest
from ..infrastructure.pixel_agents_adapter import decode, encode

_MANIFEST_RAW = {
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
                },
                "north": {
                    "catalog_id": "WOODEN_CHAIR_BACK",
                    "footprint_width": 1,
                    "footprint_height": 1,
                    "background_tiles": 0,
                },
                "east": {
                    "catalog_id": "WOODEN_CHAIR_SIDE",
                    "footprint_width": 1,
                    "footprint_height": 1,
                    "background_tiles": 0,
                },
                "west": {
                    "catalog_id": "WOODEN_CHAIR_SIDE:left",
                    "footprint_width": 1,
                    "footprint_height": 1,
                    "background_tiles": 0,
                },
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


def _styles() -> FurnitureStyleManifest:
    return FurnitureStyleManifest.from_raw(_MANIFEST_RAW)


def _flat_layout(cols: int, rows: int, fill: int = 1) -> dict[str, object]:
    return {
        "version": 1,
        "cols": cols,
        "rows": rows,
        "tiles": [fill] * (cols * rows),
        "tileColors": [{"h": 35, "s": 30, "b": 15, "c": 0}] * (cols * rows),
        "furniture": [],
    }


class TestDecodeGrid:
    def test_grid_dimensions(self) -> None:
        office = decode(_flat_layout(4, 3), _styles())
        assert office.width == 4
        assert office.height == 3
        assert office.grid.width == 4
        assert office.grid.height == 3

    def test_every_cell_is_direct_not_inferred(self) -> None:
        raw = {
            "version": 1,
            "cols": 2,
            "rows": 1,
            "tiles": [0, 5],
            "tileColors": [None, {"h": 35, "s": 30, "b": 15, "c": 0}],
            "furniture": [],
        }

        office = decode(raw, _styles())

        assert office.grid.at(GridPosition(0, 0)).kind is TileKind.WALL
        cell = office.grid.at(GridPosition(1, 0))
        assert cell.kind is TileKind.FLOOR
        assert cell.material == 5
        assert cell.color == "warm_beige"

    def test_tile_color_not_matching_any_palette_entry_keeps_its_exact_raw_value(self) -> None:
        # Deliberately not any of color_names._PALETTE's canonical values --
        # nearest_name() still has to pick *something* for the semantic
        # name, but raw_color must retain exactly what was decoded.
        raw = {
            "version": 1,
            "cols": 1,
            "rows": 1,
            "tiles": [5],
            "tileColors": [{"h": 123, "s": 17, "b": -8, "c": 42}],
            "furniture": [],
        }

        office = decode(raw, _styles())

        cell = office.grid.at(GridPosition(0, 0))
        assert cell.color == "forest_green"  # nearest match, not exact
        assert cell.raw_color == (123, 17, -8, 42)


class TestDecodeFurniture:
    def test_known_asset_becomes_furniture_item_with_kind_style_facing(self) -> None:
        raw = _flat_layout(5, 5)
        raw["furniture"] = [{"uid": "f-1", "type": "WOODEN_CHAIR_BACK", "col": 2, "row": 2}]

        office = decode(raw, _styles())

        assert len(office.furniture) == 1
        item = office.furniture[0]
        assert item.id == "f-1"
        assert item.kind is FurnitureKind.SEATING
        assert item.style == "wooden_chair"
        assert item.facing is Direction.NORTH
        assert item.position == GridPosition(2, 2)

    def test_unrecognized_asset_becomes_passthrough_not_dropped(self) -> None:
        raw = _flat_layout(5, 5)
        raw["furniture"] = [{"uid": "f-1", "type": "SOME_FUTURE_ASSET", "col": 1, "row": 1}]

        office = decode(raw, _styles())

        assert office.furniture == []
        assert office.passthrough["foreign_furniture"] == raw["furniture"]

    def test_furniture_color_maps_to_nearest_semantic_name(self) -> None:
        raw = _flat_layout(5, 5)
        raw["furniture"] = [
            {
                "uid": "f-1",
                "type": "DESK_FRONT",
                "col": 1,
                "row": 1,
                "color": {"h": 0, "s": 60, "b": 10, "c": 0},
            }
        ]

        office = decode(raw, _styles())

        assert office.furniture[0].color == "bright_red"

    def test_furniture_color_not_matching_any_palette_entry_keeps_its_exact_raw_value(self) -> None:
        raw = _flat_layout(5, 5)
        raw["furniture"] = [
            {
                "uid": "f-1",
                "type": "DESK_FRONT",
                "col": 1,
                "row": 1,
                "color": {"h": 123, "s": 17, "b": -8, "c": 42},
            }
        ]

        office = decode(raw, _styles())

        item = office.furniture[0]
        assert item.color == "forest_green"  # nearest match, not exact
        assert item.raw_color == (123, 17, -8, 42)


class TestDecodeSeats:
    def test_seat_facing_prefers_chair_orientation(self) -> None:
        raw = _flat_layout(5, 5)
        raw["furniture"] = [{"uid": "c-1", "type": "WOODEN_CHAIR_BACK", "col": 2, "row": 2}]

        office = decode(raw, _styles())

        assert len(office.seats) == 1
        assert office.seats[0].facing is Direction.NORTH
        assert office.seats[0].occupies_furniture_id == "c-1"

    def test_seat_facing_falls_back_to_adjacent_desk(self) -> None:
        manifest = FurnitureStyleManifest.from_raw(
            {
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
                        "style": "stool",
                        "kind": "seating",
                        "label": "Stool",
                        "can_place_on_walls": False,
                        "can_place_on_surfaces": False,
                        "facings": {},
                        "default_facing": None,
                        "catalog_id": "STOOL",
                        "footprint_width": 1,
                        "footprint_height": 1,
                        "background_tiles": 0,
                    },
                ]
            }
        )
        raw = _flat_layout(5, 5)
        raw["furniture"] = [
            {"uid": "d-1", "type": "DESK_FRONT", "col": 2, "row": 1},
            {"uid": "c-1", "type": "STOOL", "col": 2, "row": 2},
        ]

        office = decode(raw, manifest)

        assert office.seats[0].facing is Direction.NORTH  # desk is above -> face north


class TestDecodeZones:
    def test_zone_bounding_rect_and_hex_color_name(self) -> None:
        raw = _flat_layout(5, 5)
        raw["areas"] = [{"label": "Quiet Zone", "color": "#c0392a"}]
        raw["areaTiles"] = [None] * 25
        for row in (1, 2):
            for col in (1, 2):
                raw["areaTiles"][row * 5 + col] = "Quiet Zone"

        office = decode(raw, _styles())

        assert len(office.zones) == 1
        zone = office.zones[0]
        assert zone.label == "Quiet Zone"
        assert zone.color == "bright_red"
        assert zone.tiles.top_left == GridPosition(1, 1)
        assert zone.tiles.width == 2
        assert zone.tiles.height == 2

    def test_zone_hex_color_not_matching_any_palette_entry_keeps_its_exact_raw_value(self) -> None:
        raw = _flat_layout(5, 5)
        raw["areas"] = [{"label": "Quiet Zone", "color": "#123456"}]
        raw["areaTiles"] = [None] * 25
        raw["areaTiles"][1 * 5 + 1] = "Quiet Zone"

        office = decode(raw, _styles())

        zone = office.zones[0]
        assert zone.color == "forest_green"  # nearest match, not exact
        assert zone.raw_color == "#123456"

    def test_exact_zone_membership_lives_on_the_grid_not_a_bounding_rect(self) -> None:
        raw = _flat_layout(5, 5)
        raw["areas"] = [{"label": "L Zone", "color": "#c0392a"}]
        raw["areaTiles"] = [None] * 25
        for col, row in [(1, 1), (2, 1), (1, 2)]:  # an L-shape, not a full rectangle
            raw["areaTiles"][row * 5 + col] = "L Zone"

        office = decode(raw, _styles())

        assert office.grid.at(GridPosition(1, 1)).zone_label == "L Zone"
        assert office.grid.at(GridPosition(2, 1)).zone_label == "L Zone"
        assert office.grid.at(GridPosition(1, 2)).zone_label == "L Zone"
        assert office.grid.at(GridPosition(2, 2)).zone_label is None  # not part of the L


class TestPassthrough:
    def test_pets_carpets_and_revision_survive_decode(self) -> None:
        raw = _flat_layout(3, 3)
        raw["pets"] = [{"id": "p1", "petType": 0}]
        raw["carpetTiles"] = [None] * 9
        raw["layoutRevision"] = 3

        office = decode(raw, _styles())

        assert office.passthrough["pets"] == raw["pets"]
        assert office.passthrough["carpetTiles"] == raw["carpetTiles"]
        assert office.passthrough["layoutRevision"] == 3


class TestEncodeFurniture:
    def test_reverses_style_and_facing_to_concrete_asset_id(self) -> None:
        office = decode(_flat_layout(5, 5), _styles())
        item = FurnitureItem(
            id="new-1",
            kind=FurnitureKind.SEATING,
            style="wooden_chair",
            position=GridPosition(2, 2),
            facing=Direction.WEST,
        )
        office = office.__class__(grid=office.grid, furniture=[item])

        raw = encode(office, _styles())

        assert raw["furniture"] == [
            {
                "uid": raw["furniture"][0]["uid"],
                "type": "WOODEN_CHAIR_SIDE:left",
                "col": 2,
                "row": 2,
            }
        ]

    def test_unknown_style_facing_combination_raises(self) -> None:
        office = decode(_flat_layout(5, 5), _styles())
        item = FurnitureItem(
            id="new-1",
            kind=FurnitureKind.WALL_FIXTURE,
            style="whiteboard",
            position=GridPosition(1, 1),
            facing=Direction.NORTH,  # whiteboard has no facings at all
        )
        office = office.__class__(grid=office.grid, furniture=[item])

        try:
            encode(office, _styles())
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for an invalid style/facing combination")

    def test_uid_is_preserved_across_a_round_trip_for_unmodified_items(self) -> None:
        raw = _flat_layout(5, 5)
        raw["furniture"] = [{"uid": "stable-uid", "type": "DESK_FRONT", "col": 1, "row": 1}]

        office = decode(raw, _styles())
        encoded = encode(office, _styles())

        assert encoded["furniture"][0]["uid"] == "stable-uid"


class TestRoundTrip:
    def test_decode_encode_decode_is_stable_for_a_simple_layout(self) -> None:
        raw = _flat_layout(6, 6)
        raw["furniture"] = [
            {"uid": "d-1", "type": "DESK_FRONT", "col": 1, "row": 1},
            {"uid": "c-1", "type": "WOODEN_CHAIR_FRONT", "col": 1, "row": 2},
        ]

        first = decode(raw, _styles())
        encoded = encode(first, _styles())
        second = decode(encoded, _styles())

        assert second.width == first.width
        assert second.height == first.height
        assert [f.style for f in second.furniture] == [f.style for f in first.furniture]
        assert [f.position for f in second.furniture] == [f.position for f in first.furniture]

    def test_per_tile_pattern_variation_is_lossless(self) -> None:
        """The whole point of v2: adjacent tiles aren't required to share
        one uniform floor pattern -- an irregular, hand-painted region
        must round-trip exactly, not get simplified to one pattern/color."""

        raw = {
            "version": 1,
            "cols": 3,
            "rows": 1,
            "tiles": [3, 7, 3],  # two different patterns, side by side
            "tileColors": [
                {"h": 10, "s": 10, "b": 10, "c": 0},
                {"h": 200, "s": 40, "b": -20, "c": 5},
                {"h": 10, "s": 10, "b": 10, "c": 0},
            ],
            "furniture": [],
        }

        office = decode(raw, _styles())
        encoded = encode(office, _styles())

        assert encoded["tiles"] == [3, 7, 3]
        redecoded = decode(encoded, _styles())
        assert redecoded.grid.cells == office.grid.cells

    def test_wall_painted_between_floor_tiles_round_trips(self) -> None:
        raw = {
            "version": 1,
            "cols": 3,
            "rows": 1,
            "tiles": [1, 0, 1],  # a wall tile sitting between two floor tiles
            "tileColors": [
                {"h": 1, "s": 1, "b": 1, "c": 0},
                None,
                {"h": 1, "s": 1, "b": 1, "c": 0},
            ],
            "furniture": [],
        }

        office = decode(raw, _styles())
        encoded = encode(office, _styles())

        assert encoded["tiles"] == [1, 0, 1]

    def test_empty_layout_round_trips(self) -> None:
        raw = {
            "version": 1,
            "cols": 3,
            "rows": 3,
            "tiles": [0] * 9,
            "furniture": [],
        }

        office = decode(raw, _styles())
        encoded = encode(office, _styles())

        assert encoded["cols"] == 3
        assert encoded["rows"] == 3
        assert encoded["furniture"] == []

    def test_irregular_zone_shape_round_trips_exactly_with_no_special_casing(self) -> None:
        raw = _flat_layout(5, 5)
        raw["areas"] = [{"label": "L Zone", "color": "#c0392a"}]
        raw["areaTiles"] = [None] * 25
        for col, row in [(1, 1), (2, 1), (1, 2)]:  # an L-shape, not a full rectangle
            raw["areaTiles"][row * 5 + col] = "L Zone"

        office = decode(raw, _styles())
        encoded = encode(office, _styles())

        assert encoded["areaTiles"] == raw["areaTiles"]

    def test_multi_tile_furniture_position_round_trips(self) -> None:
        raw = _flat_layout(6, 6)
        raw["furniture"] = [{"uid": "d-1", "type": "DESK_FRONT", "col": 1, "row": 1}]

        office = decode(raw, _styles())
        encoded = encode(office, _styles())
        redecoded = decode(encoded, _styles())

        assert redecoded.furniture[0].position == GridPosition(1, 1)
        assert redecoded.furniture[0].style == "desk"

    def test_untouched_tile_color_not_matching_any_palette_entry_survives_encode_exactly(
        self,
    ) -> None:
        """The bug this guards against: encode() used to always re-expand
        `color` to the palette's canonical value, silently replacing any
        tile color that wasn't already one of the ~12 fixed entries even
        though decode() never touched it."""

        raw = {
            "version": 1,
            "cols": 1,
            "rows": 1,
            "tiles": [5],
            "tileColors": [{"h": 123, "s": 17, "b": -8, "c": 42}],
            "furniture": [],
        }

        office = decode(raw, _styles())
        encoded = encode(office, _styles())

        assert encoded["tileColors"] == [{"h": 123, "s": 17, "b": -8, "c": 42}]

    def test_untouched_furniture_color_not_matching_any_palette_entry_survives_encode_exactly(
        self,
    ) -> None:
        raw = _flat_layout(5, 5)
        raw["furniture"] = [
            {
                "uid": "f-1",
                "type": "DESK_FRONT",
                "col": 1,
                "row": 1,
                "color": {"h": 123, "s": 17, "b": -8, "c": 42},
            }
        ]

        office = decode(raw, _styles())
        encoded = encode(office, _styles())

        assert encoded["furniture"][0]["color"] == {"h": 123, "s": 17, "b": -8, "c": 42}

    def test_untouched_zone_hex_color_not_matching_any_palette_entry_survives_encode_exactly(
        self,
    ) -> None:
        raw = _flat_layout(5, 5)
        raw["areas"] = [{"label": "Quiet Zone", "color": "#123456"}]
        raw["areaTiles"] = [None] * 25
        raw["areaTiles"][1 * 5 + 1] = "Quiet Zone"

        office = decode(raw, _styles())
        encoded = encode(office, _styles())

        assert encoded["areas"] == [{"label": "Quiet Zone", "color": "#123456"}]

    def test_freshly_authored_color_falls_back_to_the_palettes_canonical_value(self) -> None:
        """The complement of the tests above: a color an LLM tool actually
        picked (no raw ground truth -- `raw_color=None`) has no exact
        original to preserve, so it correctly encodes to the semantic
        name's own canonical palette value."""

        office = decode(_flat_layout(1, 1, fill=5), _styles())
        painted_cell = replace(
            office.grid.at(GridPosition(0, 0)), color="cool_blue", raw_color=None
        )
        office = replace(office, grid=Grid(1, 1, (painted_cell,)))

        encoded = encode(office, _styles())

        assert encoded["tileColors"] == [hsb_for("cool_blue")]
