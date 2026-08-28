"""`encode()`/`decode()`: the only module that knows Pixel Agents' raw
layout JSON shape.

v2 (docs/architect-semantic-ir-design.md sections 6.1/6.2): both
directions are now a **direct, lossless, per-cell mapping** through
`Office.grid` -- no inference, no sampling, no wholesale rebuild. This is
*simpler* than the v1 versions of these functions, not more complex; see
the module's git history for what v1 looked like (room flood-fill on
every decode, dominant-color sampling, "always emit pattern 1").

There is no room concept anywhere in this module (or anywhere in Pixel
Agents itself) -- `Zone`/`areaTiles` is the only spatial-grouping concept
upstream actually has, and it's what this module builds directly.

Color is the one place a raw value and a semantic value coexist
(section 6.3): `decode()` always also stashes the exact original
`{h,s,b,c}`/hex on `TileCell.raw_color`/`FurnitureItem.raw_color`/
`Zone.raw_color`, and `encode()` prefers that over the semantic name's
canonical palette value whenever it's present. `raw_color` is `None` only
for a color that's genuinely new -- authored or changed by a mutation
(`OfficeLayoutService`'s job to clear it there) -- so an *untouched* cell's
exact color survives a round trip, and only a cell a tool actually
repainted gets the palette's nearest-match value, matching the "no
inference, no sampling" claim above on every axis, not just tiles/areas."""

from __future__ import annotations

import time
import uuid
from typing import Any, cast

from ..domain.office_ir import (
    Direction,
    FurnitureItem,
    FurnitureKind,
    Grid,
    GridPosition,
    GridRect,
    Office,
    Seat,
    TileCell,
    TileKind,
    Zone,
)
from .color_names import HsbColor, hex_for, hsb_for, nearest_hex_name, nearest_name
from .furniture_styles import FurnitureStyleManifest

_WALL = 0
_VOID = 255


def _hsb_to_raw(color: HsbColor) -> tuple[int, int, int, int]:
    return (color["h"], color["s"], color["b"], color["c"])


def _raw_to_hsb(raw: tuple[int, int, int, int]) -> HsbColor:
    h, s, b, c = raw
    return {"h": h, "s": s, "b": b, "c": c}


def decode(raw: dict[str, Any], styles: FurnitureStyleManifest) -> Office:
    """Pixel Agents JSON -> Semantic IR. See module docstring and
    docs/architect-semantic-ir-design.md section 6.1."""

    cols = cast(int, raw["cols"])
    rows = cast(int, raw["rows"])
    tiles = cast(list[int], raw["tiles"])
    tile_colors = cast("list[HsbColor | None] | None", raw.get("tileColors"))
    raw_furniture = cast("list[dict[str, Any]]", raw.get("furniture", []))
    raw_areas = cast("list[dict[str, Any]]", raw.get("areas", []))
    raw_area_tiles = cast("list[str | None] | None", raw.get("areaTiles"))

    grid = _build_grid(cols, rows, tiles, tile_colors, raw_area_tiles)
    furniture, id_uid_map, foreign_furniture = _decode_furniture(raw_furniture, styles)
    seats = _decode_seats(furniture)
    zones = _decode_zones(grid, raw_areas)

    passthrough: dict[str, object] = {"id_uid_map": id_uid_map}
    if foreign_furniture:
        passthrough["foreign_furniture"] = foreign_furniture
    for key in ("pets", "carpetTiles", "layoutRevision"):
        if key in raw:
            passthrough[key] = raw[key]

    return Office(
        grid=grid,
        zones=zones,
        furniture=furniture,
        seats=seats,
        occupants=[],
        passthrough=passthrough,
    )


def _build_grid(
    cols: int,
    rows: int,
    tiles: list[int],
    tile_colors: list[HsbColor | None] | None,
    area_tiles: list[str | None] | None,
) -> Grid:
    """Direct, per-cell, exact -- every cell, always. No clustering, no
    inference, no sampling."""

    cells: list[TileCell] = []
    for i in range(cols * rows):
        value = tiles[i]
        zone_label = area_tiles[i] if area_tiles is not None else None
        if value == _WALL:
            cells.append(TileCell.wall(zone_label=zone_label))
        elif value == _VOID:
            cells.append(TileCell.void(zone_label=zone_label))
        else:
            color_raw = tile_colors[i] if tile_colors is not None else None
            color = nearest_name(color_raw) if color_raw is not None else None
            raw_color = _hsb_to_raw(color_raw) if color_raw is not None else None
            cells.append(TileCell.floor(value, color, raw_color=raw_color, zone_label=zone_label))
    return Grid(cols, rows, tuple(cells))


def _decode_furniture(
    raw_furniture: list[dict[str, Any]], styles: FurnitureStyleManifest
) -> tuple[list[FurnitureItem], dict[str, str], list[dict[str, Any]]]:
    items: list[FurnitureItem] = []
    id_uid_map: dict[str, str] = {}
    foreign: list[dict[str, Any]] = []

    for entry in raw_furniture:
        catalog_id = cast(str, entry["type"])
        lookup = styles.style_and_facing_for(catalog_id)
        if lookup is None:
            foreign.append(entry)
            continue
        style_id, facing = lookup
        style = styles.by_style_id(style_id)
        assert style is not None  # style_and_facing_for only returns known styles

        uid = cast(str, entry["uid"])
        color_raw = cast("HsbColor | None", entry.get("color"))
        items.append(
            FurnitureItem(
                id=uid,
                kind=style.kind,
                style=style_id,
                position=GridPosition(col=entry["col"], row=entry["row"]),
                facing=facing,
                color=nearest_name(color_raw) if color_raw is not None else None,
                raw_color=_hsb_to_raw(color_raw) if color_raw is not None else None,
            )
        )
        id_uid_map[uid] = uid

    return items, id_uid_map, foreign


# Adjacent-tile offsets checked when a seat has no orientation of its own
# and must infer facing from a neighboring desk -- mirrors `layoutToSeats`
# in the vendored `layoutSerializer.ts`: desk above -> face up (north),
# desk below -> face down (south), etc.
_DESK_DIRECTIONS: tuple[tuple[int, int, Direction], ...] = (
    (0, -1, Direction.NORTH),
    (0, 1, Direction.SOUTH),
    (-1, 0, Direction.WEST),
    (1, 0, Direction.EAST),
)


def _decode_seats(furniture: list[FurnitureItem]) -> list[Seat]:
    desk_positions = {item.position for item in furniture if item.kind is FurnitureKind.DESK}
    seats: list[Seat] = []
    for item in furniture:
        if item.kind is not FurnitureKind.SEATING:
            continue
        facing = item.facing
        if facing is None:
            for dc, dr, direction in _DESK_DIRECTIONS:
                if GridPosition(item.position.col + dc, item.position.row + dr) in desk_positions:
                    facing = direction
                    break
        if facing is None:
            facing = Direction.SOUTH
        seats.append(Seat(id=f"seat:{item.id}", occupies_furniture_id=item.id, facing=facing))
    return seats


def _decode_zones(grid: Grid, raw_areas: list[dict[str, Any]]) -> list[Zone]:
    """Zone metadata 1:1 from `areas[]`; exact membership already lives in
    `grid`'s own `zone_label` per cell (populated in `_build_grid`), so
    the bounding-rect summary here is purely a read-time convenience, not
    a second source of truth to keep in sync (v1 needed
    `Office.passthrough["zone_raw_tiles"]` for exactly this; v2 doesn't)."""

    zones: list[Zone] = []
    for area in raw_areas:
        label = cast(str, area["label"])
        positions = [
            GridPosition(col, row)
            for row in range(grid.height)
            for col in range(grid.width)
            if grid.at(GridPosition(col, row)).zone_label == label
        ]
        if not positions:
            continue
        min_col = min(p.col for p in positions)
        max_col = max(p.col for p in positions)
        min_row = min(p.row for p in positions)
        max_row = max(p.row for p in positions)
        raw_color = cast(str, area["color"])
        zones.append(
            Zone(
                id=f"zone:{label}",
                label=label,
                color=nearest_hex_name(raw_color),
                raw_color=raw_color,
                tiles=GridRect(
                    GridPosition(min_col, min_row),
                    width=max_col - min_col + 1,
                    height=max_row - min_row + 1,
                ),
            )
        )
    return zones


def encode(office: Office, styles: FurnitureStyleManifest) -> dict[str, Any]:
    """Semantic IR -> Pixel Agents JSON. See module docstring and
    docs/architect-semantic-ir-design.md section 6.2.

    `office.id -> uid` preservation needs no output plumbing here:
    `decode()` always sets `FurnitureItem.id` equal to the Pixel Agents
    `uid` it came from, so `office.passthrough["id_uid_map"]` from the
    *previous* decode already answers "does this id have a uid yet" for
    every mutation method's load-mutate-encode-persist cycle
    (`application/office_layout_service.py`) -- only a genuinely new item
    (no prior decode ever saw its `id`) falls through to
    `_generate_uid()`, and the very next decode of the persisted result
    makes that new uid its `id` again, closing the loop."""

    tiles, tile_colors, area_tiles = _encode_grid(office.grid)
    furniture = _encode_furniture(office, styles)

    result: dict[str, Any] = {
        "version": 1,
        "cols": office.width,
        "rows": office.height,
        "tiles": tiles,
        "tileColors": tile_colors,
        "furniture": furniture,
    }
    if office.zones or any(label is not None for label in area_tiles):
        result["areas"] = [
            {
                "label": zone.label,
                "color": zone.raw_color if zone.raw_color is not None else hex_for(zone.color),
            }
            for zone in office.zones
        ]
        result["areaTiles"] = area_tiles
    for key in ("pets", "carpetTiles", "layoutRevision"):
        if key in office.passthrough:
            result[key] = office.passthrough[key]
    foreign = cast("list[dict[str, Any]] | None", office.passthrough.get("foreign_furniture"))
    if foreign:
        result["furniture"].extend(foreign)

    return result


def _encode_grid(grid: Grid) -> tuple[list[int], list[HsbColor | None], list[str | None]]:
    """Direct, per-cell, exact -- the inverse of `_build_grid`. Replaces
    v1's "rebuild wholesale from Room rectangles, always emitting pattern
    1" entirely: there is no reconciliation problem to solve, since an
    untouched cell is still exactly the `TileCell` `decode()` produced
    for it."""

    tiles: list[int] = []
    tile_colors: list[HsbColor | None] = []
    area_tiles: list[str | None] = []
    for cell in grid.cells:
        if cell.kind is TileKind.WALL:
            tiles.append(_WALL)
            tile_colors.append(None)
        elif cell.kind is TileKind.VOID:
            tiles.append(_VOID)
            tile_colors.append(None)
        else:
            tiles.append(cast(int, cell.material))
            if cell.color is None:
                tile_colors.append(None)
            elif cell.raw_color is not None:
                tile_colors.append(_raw_to_hsb(cell.raw_color))
            else:
                tile_colors.append(hsb_for(cell.color))
        area_tiles.append(cell.zone_label)
    return tiles, tile_colors, area_tiles


def _encode_furniture(office: Office, styles: FurnitureStyleManifest) -> list[dict[str, Any]]:
    previous_id_uid_map = cast("dict[str, str]", office.passthrough.get("id_uid_map", {}))
    encoded: list[dict[str, Any]] = []

    for item in office.furniture:
        catalog_id = styles.catalog_id_for(item.style, item.facing)
        if catalog_id is None:
            raise ValueError(
                f"furniture item {item.id!r} has style {item.style!r}/facing "
                f"{item.facing!r}, which does not exist in the current style manifest"
            )
        uid = previous_id_uid_map.get(item.id) or _generate_uid()

        entry: dict[str, Any] = {
            "uid": uid,
            "type": catalog_id,
            "col": item.position.col,
            "row": item.position.row,
        }
        if item.color is not None:
            entry["color"] = (
                _raw_to_hsb(item.raw_color) if item.raw_color is not None else hsb_for(item.color)
            )
        encoded.append(entry)

    return encoded


def _generate_uid() -> str:
    # Same generation scheme upstream's own editor uses: `f-<timestamp>-<random>`.
    return f"f-{int(time.time() * 1000)}-{uuid.uuid4().hex[:4]}"


__all__ = ["decode", "encode"]
