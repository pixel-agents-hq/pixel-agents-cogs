from __future__ import annotations

from ..domain.office_ir import Direction, FurnitureKind, GridPosition
from ..infrastructure.furniture_styles import FurnitureStyleLoader, FurnitureStyleManifest
from .conftest import FakePixelAgents

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
            "footprint_width": 2,
            "footprint_height": 2,
            "background_tiles": 0,
        },
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
                },
                "east": {
                    "catalog_id": "DESK_SIDE",
                    "footprint_width": 1,
                    "footprint_height": 4,
                    "background_tiles": 1,
                },
            },
            "default_facing": "south",
        },
    ]
}


def test_manifest_from_raw_builds_typed_lookups() -> None:
    manifest = FurnitureStyleManifest.from_raw(_MANIFEST)

    assert set(manifest.style_ids()) == {"wooden_chair", "whiteboard", "desk"}
    style = manifest.by_style_id("wooden_chair")
    assert style is not None
    assert style.kind is FurnitureKind.SEATING
    assert style.default_facing is Direction.SOUTH


def test_can_place_on_walls_and_surfaces_flags() -> None:
    manifest = FurnitureStyleManifest.from_raw(_MANIFEST)

    assert manifest.by_style_id("whiteboard").can_place_on_walls is True  # type: ignore[union-attr]
    assert manifest.by_style_id("wooden_chair").can_place_on_walls is False  # type: ignore[union-attr]


def test_catalog_id_for_style_and_facing() -> None:
    manifest = FurnitureStyleManifest.from_raw(_MANIFEST)

    assert manifest.catalog_id_for("wooden_chair", Direction.WEST) == "WOODEN_CHAIR_SIDE:left"
    # Regression test: the real Pixel Agents asset id ("WHITEBOARD") is
    # almost never the same string as the lower-cased style id
    # ("whiteboard") -- catalog_id_for must return the former, not the
    # latter, for a facing-less style. See docs/architect-semantic-ir-design.md
    # section 6.4 and the real production incident it caused.
    assert manifest.catalog_id_for("whiteboard", None) == "WHITEBOARD"
    assert manifest.catalog_id_for("wooden_chair", None) is None
    assert manifest.catalog_id_for("unknown_style", Direction.SOUTH) is None


def test_style_and_facing_for_reverses_the_lookup() -> None:
    manifest = FurnitureStyleManifest.from_raw(_MANIFEST)

    assert manifest.style_and_facing_for("WOODEN_CHAIR_BACK") == ("wooden_chair", Direction.NORTH)
    assert manifest.style_and_facing_for("WHITEBOARD") == ("whiteboard", None)
    # The lower-cased style id itself must NOT resolve -- Pixel JSON never
    # spells a real furniture[].type in lower case.
    assert manifest.style_and_facing_for("whiteboard") is None
    assert manifest.style_and_facing_for("SOME_UNKNOWN_ASSET") is None


class TestOccupiedCells:
    def test_single_tile_style(self) -> None:
        manifest = FurnitureStyleManifest.from_raw(_MANIFEST)

        cells = manifest.occupied_cells("wooden_chair", Direction.SOUTH, GridPosition(5, 5))

        assert cells == [GridPosition(5, 5)]

    def test_facing_less_style_uses_its_own_footprint(self) -> None:
        manifest = FurnitureStyleManifest.from_raw(_MANIFEST)

        cells = manifest.occupied_cells("whiteboard", None, GridPosition(2, 2))

        assert set(cells) == {
            GridPosition(2, 2),
            GridPosition(3, 2),
            GridPosition(2, 3),
            GridPosition(3, 3),
        }

    def test_background_tiles_are_excluded_from_the_top_rows(self) -> None:
        """DESK_FRONT is a real 3x2 footprint with backgroundTiles:1 --
        the top row doesn't block placement, matching Pixel Agents' own
        getPlacementBlockedTiles."""

        manifest = FurnitureStyleManifest.from_raw(_MANIFEST)

        cells = manifest.occupied_cells("desk", Direction.SOUTH, GridPosition(0, 0))

        assert set(cells) == {GridPosition(0, 1), GridPosition(1, 1), GridPosition(2, 1)}

    def test_footprint_is_not_a_transpose_between_facings(self) -> None:
        manifest = FurnitureStyleManifest.from_raw(_MANIFEST)

        cells = manifest.occupied_cells("desk", Direction.EAST, GridPosition(0, 0))

        # 1x4 footprint, backgroundTiles=1 -> top row excluded, 3 remain.
        assert set(cells) == {GridPosition(0, 1), GridPosition(0, 2), GridPosition(0, 3)}

    def test_unknown_style_or_facing_returns_empty(self) -> None:
        manifest = FurnitureStyleManifest.from_raw(_MANIFEST)

        assert manifest.occupied_cells("not_a_style", None, GridPosition(0, 0)) == []
        assert manifest.occupied_cells("wooden_chair", None, GridPosition(0, 0)) == []


def test_loader_caches_until_the_built_commit_changes() -> None:
    fake = FakePixelAgents(furniture_styles=_MANIFEST, built_commit="a" * 40)
    loader = FurnitureStyleLoader(fake)

    first = loader.styles()
    assert set(first.style_ids()) == {"wooden_chair", "whiteboard", "desk"}

    # Manifest changes underneath, but built_commit hasn't -- still cached.
    fake._furniture_styles = {"styles": []}
    assert loader.styles() is first

    # built_commit changes -- cache invalidates and re-reads.
    fake.built_commit = "b" * 40
    refreshed = loader.styles()
    assert refreshed.style_ids() == []


def test_loader_returns_empty_manifest_when_pixelagents_has_no_webview_yet() -> None:
    fake = FakePixelAgents(ready=False, furniture_styles=None)
    loader = FurnitureStyleLoader(fake)

    assert loader.styles().style_ids() == []
