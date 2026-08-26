"""office_ir needs no mocking, no stubs, nothing framework-related -- same
"trivially unit-testable" convention test_domain_models.py already
exercises for GlobalSettings, plus a contract test asserting the module
stays free of Pixel-Agents-specific imports (docs/architect-semantic-ir-design.md
section 9)."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

from ..domain.office_ir import (
    Direction,
    FurnitureItem,
    FurnitureKind,
    Grid,
    GridPosition,
    GridRect,
    Occupant,
    Office,
    Seat,
    TileCell,
    TileKind,
    Zone,
)


def _flat_grid(width: int, height: int) -> Grid:
    return Grid(width, height, tuple(TileCell.wall() for _ in range(width * height)))


def test_office_ir_has_zero_pixel_agents_or_framework_imports() -> None:
    source_path = Path(__file__).parent.parent / "domain" / "office_ir.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden = {"pixelagents", "pydantic", "redbot", "discord"}

    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    assert imported_roots & forbidden == set()


def test_grid_rect_contains_and_overlaps() -> None:
    rect = GridRect(GridPosition(2, 2), width=3, height=3)

    assert rect.contains(GridPosition(2, 2))
    assert rect.contains(GridPosition(4, 4))
    assert not rect.contains(GridPosition(5, 5))
    assert not rect.contains(GridPosition(1, 2))

    assert rect.overlaps(GridRect(GridPosition(4, 4), width=2, height=2))
    assert not rect.overlaps(GridRect(GridPosition(5, 5), width=2, height=2))


def test_grid_rect_positions_enumerates_every_cell_row_major() -> None:
    rect = GridRect(GridPosition(1, 1), width=2, height=2)

    assert rect.positions() == [
        GridPosition(1, 1),
        GridPosition(2, 1),
        GridPosition(1, 2),
        GridPosition(2, 2),
    ]


class TestTileCell:
    def test_wall_and_void_have_no_material_or_color(self) -> None:
        wall = TileCell.wall()
        void = TileCell.void()

        assert wall.kind is TileKind.WALL
        assert wall.material is None
        assert wall.color is None
        assert void.kind is TileKind.VOID
        assert void.material is None

    def test_floor_carries_material_and_optional_zone(self) -> None:
        cell = TileCell.floor(5, "warm_beige", zone_label="Quiet Zone")

        assert cell.kind is TileKind.FLOOR
        assert cell.material == 5
        assert cell.color == "warm_beige"
        assert cell.zone_label == "Quiet Zone"


class TestGrid:
    def test_rejects_a_cell_count_mismatch(self) -> None:
        try:
            Grid(2, 2, (TileCell.wall(),))
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for a mismatched cell count")

    def test_at_reads_the_right_row_major_cell(self) -> None:
        cells = tuple(TileCell.floor(i + 1, None) for i in range(6))
        grid = Grid(3, 2, cells)

        assert grid.at(GridPosition(0, 0)).material == 1
        assert grid.at(GridPosition(2, 0)).material == 3
        assert grid.at(GridPosition(0, 1)).material == 4
        assert grid.at(GridPosition(2, 1)).material == 6

    def test_in_bounds(self) -> None:
        grid = _flat_grid(3, 3)

        assert grid.in_bounds(GridPosition(0, 0))
        assert grid.in_bounds(GridPosition(2, 2))
        assert not grid.in_bounds(GridPosition(3, 0))
        assert not grid.in_bounds(GridPosition(0, -1))

    def test_replacing_is_copy_on_write(self) -> None:
        grid = _flat_grid(2, 2)
        new_cell = TileCell.floor(1, "warm_beige")

        updated = grid.replacing({GridPosition(1, 1): new_cell})

        assert updated.at(GridPosition(1, 1)) == new_cell
        assert updated.at(GridPosition(0, 0)).kind is TileKind.WALL
        # original is untouched
        assert grid.at(GridPosition(1, 1)).kind is TileKind.WALL


def test_office_holds_every_entity_kind() -> None:
    office = Office(
        grid=_flat_grid(10, 10),
        zones=[
            Zone(
                id="z1", label="Quiet Zone", color="blue", tiles=GridRect(GridPosition(5, 5), 2, 2)
            )
        ],
        furniture=[
            FurnitureItem(
                id="f1",
                kind=FurnitureKind.DESK,
                style="desk",
                position=GridPosition(1, 1),
                facing=Direction.SOUTH,
            )
        ],
        seats=[Seat(id="s1", occupies_furniture_id="f1", facing=Direction.NORTH)],
        occupants=[Occupant(id="o1", display_name="Priya")],
    )

    assert office.width == 10
    assert office.height == 10
    assert office.zones[0].color == "blue"
    assert office.furniture[0].kind is FurnitureKind.DESK
    assert office.seats[0].occupies_furniture_id == "f1"
    assert office.occupants[0].display_name == "Priya"
    assert office.passthrough == {}


def test_office_ir_entities_are_frozen() -> None:
    position = GridPosition(1, 1)
    try:
        position.col = 2  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("GridPosition should be frozen")
