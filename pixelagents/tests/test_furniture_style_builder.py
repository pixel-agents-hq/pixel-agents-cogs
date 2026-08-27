"""Unit tests for furniture_style_builder's catalog -> style manifest
derivation. Pure function, no filesystem/network -- hand-built catalog
fixtures cover each rule in docs/architect-semantic-ir-design.md §6.4,
including v2's per-facing footprint/background_tiles and style-level
can_place_on_walls/can_place_on_surfaces."""

from __future__ import annotations

import unittest

from pixelagents.infrastructure.furniture_style_builder import build_furniture_style_manifest


def _entry(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "X",
        "label": "X",
        "category": "misc",
        "footprintW": 1,
        "footprintH": 1,
    }
    base.update(overrides)
    return base


class TestGroupedMirroredStyle(unittest.TestCase):
    def test_collapses_rotation_group_into_one_style_with_all_facings(self) -> None:
        catalog = [
            _entry(
                id="WOODEN_CHAIR_FRONT",
                label="Wooden Chair - Front",
                category="chairs",
                groupId="WOODEN_CHAIR",
                orientation="front",
            ),
            _entry(
                id="WOODEN_CHAIR_BACK",
                label="Wooden Chair - Back",
                category="chairs",
                groupId="WOODEN_CHAIR",
                orientation="back",
            ),
            _entry(
                id="WOODEN_CHAIR_SIDE",
                label="Wooden Chair - Side",
                category="chairs",
                groupId="WOODEN_CHAIR",
                orientation="side",
                mirrorSide=True,
            ),
        ]

        manifest = build_furniture_style_manifest(catalog)

        self.assertEqual(len(manifest["styles"]), 1)
        style = manifest["styles"][0]
        self.assertEqual(style["style"], "wooden_chair")
        self.assertEqual(style["kind"], "seating")
        self.assertEqual(style["label"], "Wooden Chair - Front")
        self.assertFalse(style["can_place_on_walls"])
        self.assertFalse(style["can_place_on_surfaces"])
        self.assertEqual(
            style["facings"],
            {
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
        )
        self.assertEqual(style["default_facing"], "south")

    def test_real_asset_footprint_is_not_a_transpose_per_facing(self) -> None:
        """Regression guard for the whole reason footprint is stored per
        facing rather than rotated from one (w,h) pair: confirmed directly
        against the real DESK manifest, DESK_FRONT is 3x2 with
        backgroundTiles:1, DESK_SIDE (the same style, rotated) is 1x4 --
        not a transpose of one pair."""

        catalog = [
            _entry(
                id="DESK_FRONT",
                label="Desk - Front",
                category="desks",
                groupId="DESK",
                orientation="front",
                footprintW=3,
                footprintH=2,
                backgroundTiles=1,
            ),
            _entry(
                id="DESK_SIDE",
                label="Desk - Side",
                category="desks",
                groupId="DESK",
                orientation="side",
                footprintW=1,
                footprintH=4,
                backgroundTiles=1,
            ),
        ]

        manifest = build_furniture_style_manifest(catalog)

        style = manifest["styles"][0]
        self.assertEqual(
            style["facings"]["south"],
            {
                "catalog_id": "DESK_FRONT",
                "footprint_width": 3,
                "footprint_height": 2,
                "background_tiles": 1,
            },
        )
        self.assertEqual(
            style["facings"]["east"],
            {
                "catalog_id": "DESK_SIDE",
                "footprint_width": 1,
                "footprint_height": 4,
                "background_tiles": 1,
            },
        )


class TestUngroupedStyle(unittest.TestCase):
    def test_ungrouped_item_has_no_facings(self) -> None:
        catalog = [
            _entry(
                id="WHITEBOARD",
                label="Whiteboard",
                category="wall",
                footprintW=2,
                footprintH=2,
                canPlaceOnWalls=True,
            )
        ]

        manifest = build_furniture_style_manifest(catalog)

        self.assertEqual(len(manifest["styles"]), 1)
        style = manifest["styles"][0]
        self.assertEqual(style["style"], "whiteboard")
        self.assertEqual(style["kind"], "wall_fixture")
        self.assertEqual(style["facings"], {})
        self.assertIsNone(style["default_facing"])
        self.assertEqual(style["catalog_id"], "WHITEBOARD")
        self.assertEqual(style["footprint_width"], 2)
        self.assertEqual(style["footprint_height"], 2)
        self.assertEqual(style["background_tiles"], 0)
        self.assertTrue(style["can_place_on_walls"])
        self.assertFalse(style["can_place_on_surfaces"])

    def test_ungrouped_item_records_its_real_catalog_id(self) -> None:
        """Regression test for a real production bug: the style id is
        lower-cased for LLM/tool use ("whiteboard"), but Pixel JSON's own
        `furniture[].type` is never spelled in lower case ("WHITEBOARD").
        Without a separate `catalog_id` field, every facing-less item --
        CUSHIONED_BENCH, WHITEBOARD, BIN, most decor -- silently failed to
        decode, because the only "catalog id" a consumer could recover was
        the lower-cased style id itself, which never matches anything real."""

        catalog = [_entry(id="CUSHIONED_BENCH", label="Cushioned Bench", category="chairs")]

        manifest = build_furniture_style_manifest(catalog)

        style = manifest["styles"][0]
        self.assertEqual(style["style"], "cushioned_bench")
        self.assertEqual(style["catalog_id"], "CUSHIONED_BENCH")

    def test_can_place_on_surfaces_style_level_flag(self) -> None:
        catalog = [_entry(id="COFFEE", label="Coffee", category="misc", canPlaceOnSurfaces=True)]

        manifest = build_furniture_style_manifest(catalog)

        self.assertTrue(manifest["styles"][0]["can_place_on_surfaces"])


class TestStatePairStyle(unittest.TestCase):
    def test_on_state_variant_is_excluded_only_off_variant_used(self) -> None:
        catalog = [
            _entry(
                id="PC_FRONT_OFF",
                label="PC - Front - Off",
                category="electronics",
                groupId="PC",
                orientation="front",
                state="off",
                canPlaceOnSurfaces=True,
            ),
            _entry(
                id="PC_FRONT_ON",
                label="PC - Front - On",
                category="electronics",
                groupId="PC",
                orientation="front",
                state="on",
                canPlaceOnSurfaces=True,
            ),
        ]

        manifest = build_furniture_style_manifest(catalog)

        self.assertEqual(len(manifest["styles"]), 1)
        style = manifest["styles"][0]
        self.assertEqual(
            style["facings"],
            {
                "south": {
                    "catalog_id": "PC_FRONT_OFF",
                    "footprint_width": 1,
                    "footprint_height": 1,
                    "background_tiles": 0,
                }
            },
        )
        self.assertTrue(style["can_place_on_surfaces"])


class TestUnknownCategory(unittest.TestCase):
    def test_unrecognized_category_is_omitted_not_crashed_on(self) -> None:
        catalog = [_entry(id="MYSTERY", label="Mystery", category="not_a_real_category")]

        manifest = build_furniture_style_manifest(catalog)

        self.assertEqual(manifest["styles"], [])


class TestMultipleStyles(unittest.TestCase):
    def test_sorted_by_style_id_and_independent_of_each_other(self) -> None:
        catalog = [
            _entry(id="ZEBRA", label="Zebra", category="decor"),
            _entry(id="APPLE", label="Apple", category="decor"),
        ]

        manifest = build_furniture_style_manifest(catalog)

        self.assertEqual([s["style"] for s in manifest["styles"]], ["apple", "zebra"])


if __name__ == "__main__":
    unittest.main()
