"""OfficeLayoutService: the one mutation surface Discord commands and LLM
tools both call. Framework-neutral -- depends only on `OfficeLayoutRepository`
and `FurnitureStyleLoader`, no discord.py, no pydantic.

Every mutation follows the same shape (docs/architect-semantic-ir-design.md
section 8): load the current `Office`, apply the change to a *new* value
(the IR dataclasses are frozen -- `dataclasses.replace`/`Grid.replacing`,
never in-place edits), validate the whole resulting `Office` before ever
encoding it, and only then persist. A validation failure leaves the
stored layout untouched, since nothing was written until validation
passed. Live delivery to any connected `cctv` dashboard page happens
automatically via corridor's own `OfficeStateChanged` publish on that
persist (docs/cctv-design.md) -- this service pushes no broadcast of its
own.

There is no room concept anywhere in this service -- `Zone` is the only
spatial-grouping concept exposed to the LLM, matching Pixel Agents' own
model (it has no room concept either). `place_furniture` always requires
an explicit `position`; the LLM calls `describe_tiles` to find one instead
of relying on a room to auto-place within.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any

from pixelagents.infrastructure.color_names import known_names
from pixelagents.infrastructure.furniture_styles import (
    FurnitureFacing,
    FurnitureStyle,
    FurnitureStyleLoader,
    FurnitureStyleManifest,
)

from ..domain import (
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
from ..infrastructure.office_layout_repository import OfficeLayoutRepository

_MAX_DESCRIBE_TILES_AREA = 400


class OfficeValidationError(Exception):
    """Raised for any of section 8's whole-`Office` validation rules.
    `reason` is LLM-readable -- tool `Output`s surface it verbatim as
    their `message` field."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Touching:
    """An alternative to `place_furniture`'s `position` -- place flush
    against an existing item's edge instead of naming a coordinate. See
    `_touching_anchor`'s own docstring for the arithmetic and why the
    caller never has to reason about background_tiles."""

    furniture_id: str
    side: Direction
    offset: int = 0


class OfficeLayoutService:
    def __init__(
        self,
        repository: OfficeLayoutRepository,
        style_loader: FurnitureStyleLoader,
    ) -> None:
        self._repository = repository
        self._style_loader = style_loader

    # -- queries -----------------------------------------------------

    async def describe(self) -> Office:
        office, _ = await self._load()
        return office

    async def find_furniture(self, *, kind: FurnitureKind | None = None) -> list[FurnitureItem]:
        office, _ = await self._load()
        items = office.furniture
        if kind is not None:
            items = [item for item in items if item.kind is kind]
        return items

    async def describe_tiles(self, *, area: GridRect) -> list[TileCell]:
        office, _ = await self._load()
        if area.width * area.height > _MAX_DESCRIBE_TILES_AREA:
            raise OfficeValidationError(
                f"describe_tiles area is too large ({area.width * area.height} tiles, "
                f"max {_MAX_DESCRIBE_TILES_AREA})"
            )
        if not _rect_in_bounds(area, office):
            raise OfficeValidationError(
                f"area extends outside the {office.width}x{office.height} grid "
                f"(0-based coordinates, far edge exclusive: {_rect_out_of_bounds_detail(area, office)})"
            )
        return [office.grid.at(position) for position in area.positions()]

    async def find_furniture_anchors(
        self,
        *,
        style: str,
        facing: Direction | None = None,
        area: GridRect,
        limit: int = 20,
    ) -> list[GridPosition]:
        """Read-only: every anchor at or near `area` where
        `place_furniture(style=style, facing=facing, position=...)` would
        succeed right now, probing the exact same `_furniture_placement_error`
        place_furniture/move_furniture already validate against -- never
        reimplements the wall/floor-anchor rule, so it can't drift out of
        sync with it. Works for any style (this is generic, not restricted
        to `can_place_on_walls`), but the tool layer (`office_tools.py`'s
        `FindFurnitureAnchorsTool`) now steers callers toward two specific
        uses: a `can_place_on_walls` style's row overhang (sized to the
        style's own `footprint_height`, not a guess -- the anchor a caller
        can't otherwise find without a failed placement attempt first), and
        pre-checking an empty region fits a style before its first
        placement there. Finding a spot *adjacent to an existing item* is
        `place_furniture`'s `touching` parameter's job now (see `Touching`/
        `_touching_anchor`) -- it computes the flush anchor directly from
        live state instead of a caller searching a strip and reusing the
        result, which can go stale the moment an earlier call in the same
        session changes the layout. Anchors are returned in scan order,
        rows top-to-bottom then columns left-to-right within `area`."""

        office, styles = await self._load()
        if area.width * area.height > _MAX_DESCRIBE_TILES_AREA:
            raise OfficeValidationError(
                f"find_furniture_anchors area is too large ({area.width * area.height} tiles, "
                f"max {_MAX_DESCRIBE_TILES_AREA})"
            )
        if not _rect_in_bounds(area, office):
            raise OfficeValidationError(
                f"area extends outside the {office.width}x{office.height} grid "
                f"(0-based coordinates, far edge exclusive: {_rect_out_of_bounds_detail(area, office)})"
            )
        style_def = styles.by_style_id(style)
        if style_def is None:
            raise OfficeValidationError(f"style {style!r} does not exist")
        resolved_facing = facing if facing is not None else style_def.default_facing
        record = style_def.facing_record(resolved_facing)
        if record is None:
            return []

        occupied = _occupied_cells_by_others(office, styles)
        row_start = area.top_left.row - (record.footprint_height - 1)
        row_end = area.top_left.row + area.height
        col_end = area.top_left.col + area.width

        anchors: list[GridPosition] = []
        for row in range(row_start, row_end):
            for col in range(area.top_left.col, col_end):
                position = GridPosition(col, row)
                error = _furniture_placement_error(
                    office, styles, style_def, resolved_facing, position, occupied
                )
                if error is None:
                    anchors.append(position)
                    if len(anchors) >= limit:
                        return anchors
        return anchors

    # -- mutations -----------------------------------------------------

    async def paint_tiles(
        self,
        *,
        area: GridRect,
        kind: TileKind,
        material: int | None = None,
        color: str | None = None,
    ) -> None:
        office, styles = await self._load()
        if not _rect_in_bounds(area, office):
            raise OfficeValidationError(
                f"area extends outside the {office.width}x{office.height} grid "
                f"(0-based coordinates, far edge exclusive: {_rect_out_of_bounds_detail(area, office)})"
            )
        if kind is TileKind.FLOOR:
            if material is None or not (1 <= material <= 9):
                raise OfficeValidationError("material must be an integer from 1 through 9")
            if color is not None and color not in known_names():
                raise OfficeValidationError(f"unknown color {color!r}")
        elif kind is TileKind.WALL:
            occupied = _occupied_cells_by_others(office, styles)
            for position in area.positions():
                blocker = occupied.get(position)
                if blocker is not None:
                    raise OfficeValidationError(
                        f"cannot paint a wall at ({position.col}, {position.row}): "
                        f"furniture {blocker.id!r} occupies it -- remove it first"
                    )
        else:
            raise OfficeValidationError(f"paint_tiles does not support kind={kind.value!r}")

        updates: dict[GridPosition, TileCell] = {}
        for position in area.positions():
            cell = office.grid.at(position)
            if kind is TileKind.WALL:
                updates[position] = TileCell.wall(zone_label=cell.zone_label)
            else:
                assert material is not None
                # `color is None` means this cell's color is untouched by
                # this call -- carry its exact original raw_color forward
                # too, not just the semantic name, so it still round-trips
                # byte-for-byte on encode (docs/architect-semantic-ir-design.md
                # section 6.3). A newly authored color has no raw ground
                # truth of its own yet, so raw_color is correctly absent.
                new_color: str | None
                new_raw_color: tuple[int, int, int, int] | None
                if color is not None:
                    new_color, new_raw_color = color, None
                else:
                    new_color, new_raw_color = cell.color, cell.raw_color
                updates[position] = TileCell.floor(
                    material,
                    new_color,
                    raw_color=new_raw_color,
                    zone_label=cell.zone_label,
                )
        new_grid = office.grid.replacing(updates)
        new_office = replace(office, grid=new_grid)
        self._validate(new_office, styles)
        await self._persist(new_office, styles)

    async def place_furniture(
        self,
        *,
        kind: FurnitureKind,
        style: str,
        position: GridPosition | None = None,
        touching: Touching | None = None,
        facing: Direction | None = None,
        label: str | None = None,
    ) -> FurnitureItem:
        """Exactly one of `position` (an absolute anchor) or `touching` (an
        existing item's id/side/offset) must be given -- `touching` is
        preferred whenever the goal is adjacency (seating around a table,
        lining chairs against a desk): it computes the flush anchor itself,
        from the *current* office state at the moment this call runs, so
        there is no coordinate math to get wrong and no staleness risk from
        reusing a position computed before an earlier call in the same
        session changed the layout."""

        if (position is None) == (touching is None):
            raise OfficeValidationError(
                "place_furniture requires exactly one of position or touching"
            )
        office, styles = await self._load()
        style_def = styles.by_style_id(style)
        if style_def is None:
            raise OfficeValidationError(f"style {style!r} does not exist")
        if style_def.kind is not kind:
            raise OfficeValidationError(
                f"style {style!r} is kind {style_def.kind.value!r}, not {kind.value!r}"
            )
        resolved_facing = facing if facing is not None else style_def.default_facing
        if touching is not None:
            record = style_def.facing_record(resolved_facing)
            if record is None:
                raise OfficeValidationError(
                    f"style {style!r} has no facing {resolved_facing.value if resolved_facing else None!r}"
                )
            target = self._find_furniture(office, touching.furniture_id)
            target_style = styles.by_style_id(target.style)
            if target_style is None:
                raise OfficeValidationError(
                    f"furniture {touching.furniture_id!r} has unknown style {target.style!r}"
                )
            target_record = target_style.facing_record(target.facing)
            if target_record is None:
                raise OfficeValidationError(
                    f"furniture {touching.furniture_id!r}'s style {target.style!r} has no "
                    f"facing {target.facing.value if target.facing else None!r}"
                )
            position = _touching_anchor(
                target, target_record, touching.side, touching.offset, record
            )
        assert position is not None
        occupied = _occupied_cells_by_others(office, styles)
        error = _furniture_placement_error(
            office, styles, style_def, resolved_facing, position, occupied
        )
        if error is not None:
            raise OfficeValidationError(error)

        item = FurnitureItem(
            id=str(uuid.uuid4()),
            kind=kind,
            style=style,
            position=position,
            facing=resolved_facing,
            label=label,
        )
        new_office = replace(
            office,
            furniture=[*office.furniture, item],
            # Seed id_uid_map so encode() reuses `item.id` itself as the
            # persisted Pixel Agents uid, rather than generating an
            # unrelated one -- otherwise the very next load() would decode
            # this item under a *different* id than the one just returned
            # to the caller (docs/architect-semantic-ir-design.md section
            # 6.2's uid-preservation only covers items a previous decode
            # already knew about).
            passthrough={
                **office.passthrough,
                "id_uid_map": {**_id_uid_map(office), item.id: item.id},
            },
        )
        self._validate(new_office, styles)
        await self._persist(new_office, styles)
        return item

    async def move_furniture(
        self, *, furniture_id: str, position: GridPosition, facing: Direction | None = None
    ) -> FurnitureItem:
        office, styles = await self._load()
        item = self._find_furniture(office, furniture_id)
        style_def = styles.by_style_id(item.style)
        if style_def is None:
            raise OfficeValidationError(
                f"furniture {furniture_id!r} has unknown style {item.style!r}"
            )
        resolved_facing = facing if facing is not None else item.facing
        occupied = _occupied_cells_by_others(office, styles, exclude_id=furniture_id)
        error = _furniture_placement_error(
            office, styles, style_def, resolved_facing, position, occupied
        )
        if error is not None:
            raise OfficeValidationError(error)

        updated = replace(item, position=position, facing=resolved_facing)
        new_furniture = [updated if f.id == furniture_id else f for f in office.furniture]
        new_office = replace(office, furniture=new_furniture)
        self._validate(new_office, styles)
        await self._persist(new_office, styles)
        return updated

    async def remove_furniture(self, *, furniture_id: str) -> None:
        office, styles = await self._load()
        self._find_furniture(office, furniture_id)
        new_furniture = [f for f in office.furniture if f.id != furniture_id]
        new_seats = [s for s in office.seats if s.occupies_furniture_id != furniture_id]
        new_office = replace(office, furniture=new_furniture, seats=new_seats)
        self._validate(new_office, styles)
        await self._persist(new_office, styles)

    async def create_zone(self, *, label: str, color: str, tiles: GridRect) -> Zone:
        office, styles = await self._load()
        if color not in known_names():
            raise OfficeValidationError(f"unknown color {color!r}")
        if any(zone.label == label for zone in office.zones):
            raise OfficeValidationError(f"a zone labeled {label!r} already exists")
        if not _rect_in_bounds(tiles, office):
            raise OfficeValidationError(
                f"zone extends outside the {office.width}x{office.height} grid "
                f"(0-based coordinates, far edge exclusive: {_rect_out_of_bounds_detail(tiles, office)})"
            )

        # Matches `_decode_zones`'s own id scheme so a zone created here
        # round-trips to the same id after the next persist + reload.
        zone = Zone(id=f"zone:{label}", label=label, color=color, tiles=tiles)
        new_grid = _tag_zone_label(office.grid, tiles.positions(), label)
        new_office = replace(office, zones=[*office.zones, zone], grid=new_grid)
        self._validate(new_office, styles)
        await self._persist(new_office, styles)
        return zone

    async def resize_zone(self, *, zone_id: str, tiles: GridRect) -> Zone:
        office, styles = await self._load()
        zone = self._find_zone(office, zone_id)
        if not _rect_in_bounds(tiles, office):
            raise OfficeValidationError(
                f"zone extends outside the {office.width}x{office.height} grid "
                f"(0-based coordinates, far edge exclusive: {_rect_out_of_bounds_detail(tiles, office)})"
            )
        new_grid = _clear_zone_label(office.grid, zone.label)
        new_grid = _tag_zone_label(new_grid, tiles.positions(), zone.label)
        updated = replace(zone, tiles=tiles)
        new_zones = [updated if z.id == zone_id else z for z in office.zones]
        new_office = replace(office, zones=new_zones, grid=new_grid)
        self._validate(new_office, styles)
        await self._persist(new_office, styles)
        return updated

    async def replace_layout(self, *, raw: dict[str, Any]) -> Office:
        """Accept a whole raw Pixel Agents layout -- e.g. the full-office
        payload architect's in-browser editor sends after a drag-and-drop
        session, not an incremental change -- and persist it wholesale.
        `decode()` parses it against the live style manifest the same way
        `_load()` does; section 8's whole-`Office` validation still applies
        before anything is persisted, so a malformed or corrupted payload
        is rejected exactly like any other mutation, never partially
        written. Raises `OfficeValidationError` for a structurally invalid
        or rule-violating layout; a caller reachable from an unauthenticated
        transport (the browser editor has no login of its own) must treat
        any other exception `decode()` itself can raise -- e.g. a missing
        or wrong-typed field -- as equally possible and equally safe to
        just drop, since nothing is persisted unless every step here
        succeeds."""

        styles = self._style_loader.styles()
        office = self._repository.decode_raw(raw, styles)
        self._validate(office, styles)
        await self._persist(office, styles)
        return office

    async def remove_zone(self, *, zone_id: str) -> None:
        office, styles = await self._load()
        zone = self._find_zone(office, zone_id)
        new_grid = _clear_zone_label(office.grid, zone.label)
        new_zones = [z for z in office.zones if z.id != zone_id]
        new_office = replace(office, zones=new_zones, grid=new_grid)
        self._validate(new_office, styles)
        await self._persist(new_office, styles)

    async def seat_occupant(self, *, seat_id: str, occupant_id: str) -> Seat:
        office, styles = await self._load()
        seat = self._find_seat(office, seat_id)
        if not any(occupant.id == occupant_id for occupant in office.occupants):
            raise OfficeValidationError(f"occupant {occupant_id!r} does not exist")
        updated = replace(seat, occupant_id=occupant_id)
        new_seats = [updated if s.id == seat_id else s for s in office.seats]
        new_office = replace(office, seats=new_seats)
        self._validate(new_office, styles)
        await self._persist(new_office, styles)
        return updated

    async def vacate_seat(self, *, seat_id: str) -> Seat:
        office, styles = await self._load()
        seat = self._find_seat(office, seat_id)
        updated = replace(seat, occupant_id=None)
        new_seats = [updated if s.id == seat_id else s for s in office.seats]
        new_office = replace(office, seats=new_seats)
        self._validate(new_office, styles)
        await self._persist(new_office, styles)
        return updated

    # -- internals -----------------------------------------------------

    async def _load(self) -> tuple[Office, FurnitureStyleManifest]:
        styles = self._style_loader.styles()
        office = await self._repository.load(styles)
        return office, styles

    async def _persist(self, office: Office, styles: FurnitureStyleManifest) -> None:
        await self._repository.save(office, styles)

    @staticmethod
    def _find_zone(office: Office, zone_id: str) -> Zone:
        for zone in office.zones:
            if zone.id == zone_id:
                return zone
        raise OfficeValidationError(f"zone {zone_id!r} does not exist")

    @staticmethod
    def _find_furniture(office: Office, furniture_id: str) -> FurnitureItem:
        for item in office.furniture:
            if item.id == furniture_id:
                return item
        raise OfficeValidationError(f"furniture {furniture_id!r} does not exist")

    @staticmethod
    def _find_seat(office: Office, seat_id: str) -> Seat:
        for seat in office.seats:
            if seat.id == seat_id:
                return seat
        raise OfficeValidationError(f"seat {seat_id!r} does not exist")

    def _validate(self, office: Office, styles: FurnitureStyleManifest) -> None:
        _validate_zones_in_bounds(office)
        _validate_furniture(office, styles)
        _validate_seats(office)


def _id_uid_map(office: Office) -> dict[str, str]:
    raw = office.passthrough.get("id_uid_map", {})
    return raw if isinstance(raw, dict) else {}


def _rect_in_bounds(rect: GridRect, office: Office) -> bool:
    return (
        rect.top_left.col >= 0
        and rect.top_left.row >= 0
        and rect.top_left.col + rect.width <= office.width
        and rect.top_left.row + rect.height <= office.height
    )


def _rect_out_of_bounds_detail(rect: GridRect, office: Office) -> str:
    """Spells out exactly which edge of `rect` overshoots, for an
    `OfficeValidationError` message an LLM can act on without re-guessing
    -- coordinates are 0-based (`GridPosition`/`GridRect` docstrings), so
    the actual failure is almost always `col/row + width/height` landing
    one past the grid's last valid index, not a negative coordinate."""

    problems: list[str] = []
    if rect.top_left.col < 0:
        problems.append(f"col {rect.top_left.col} is negative")
    if rect.top_left.row < 0:
        problems.append(f"row {rect.top_left.row} is negative")
    right = rect.top_left.col + rect.width
    if right > office.width:
        problems.append(f"col {rect.top_left.col} + width {rect.width} = {right} > {office.width}")
    bottom = rect.top_left.row + rect.height
    if bottom > office.height:
        problems.append(
            f"row {rect.top_left.row} + height {rect.height} = {bottom} > {office.height}"
        )
    return "; ".join(problems)


def _all_positions(grid: Grid) -> list[GridPosition]:
    return [GridPosition(col, row) for row in range(grid.height) for col in range(grid.width)]


def _tag_zone_label(grid: Grid, positions: list[GridPosition], label: str) -> Grid:
    updates: dict[GridPosition, TileCell] = {}
    for position in positions:
        if grid.in_bounds(position):
            updates[position] = replace(grid.at(position), zone_label=label)
    return grid.replacing(updates) if updates else grid


def _clear_zone_label(grid: Grid, label: str) -> Grid:
    updates: dict[GridPosition, TileCell] = {}
    for position in _all_positions(grid):
        cell = grid.at(position)
        if cell.zone_label == label:
            updates[position] = replace(cell, zone_label=None)
    return grid.replacing(updates) if updates else grid


def _occupied_cells_by_others(
    office: Office, styles: FurnitureStyleManifest, *, exclude_id: str | None = None
) -> dict[GridPosition, FurnitureItem]:
    occupied: dict[GridPosition, FurnitureItem] = {}
    for item in office.furniture:
        if item.id == exclude_id:
            continue
        for cell in styles.occupied_cells(item.style, item.facing, item.position):
            occupied[cell] = item
    return occupied


def _touching_anchor(
    target: FurnitureItem,
    target_record: FurnitureFacing,
    side: Direction,
    offset: int,
    new_record: FurnitureFacing,
) -> GridPosition:
    """The anchor for `new_record`'s footprint so it sits flush against
    `target`'s `side`. Pure arithmetic on footprint dimensions --
    bounds/collision are still validated the normal way afterward, same as
    any other position.

    occupied_cells() only ever excludes background_tiles *rows* (dr starts
    at background_tiles, never affects dc) -- so south touching (the side
    background_tiles never strips) is always the plain "one tile past the
    target's occupied edge" case, and column growth itself (footprint_width)
    never has a background asymmetry. North is the one side where the
    touch row itself changes: when target has background rows, its own
    north-most `background_tiles` rows are walkable (not in its
    occupied_cells), so the flush position for a new item is target's own
    anchor row itself, not one tile further out.

    `offset`'s own axis has the same asymmetry once more, in the other
    pair of sides: for west/east, offset runs along target's *occupied*
    rows, not its full anchor-inclusive footprint -- target's background
    rows are its decorative back edge, not real surface a side item should
    align against, so offset=0 starts at target.anchor_row +
    target.background_tiles, not target.anchor_row itself. For north/south,
    offset runs along columns, which never have this asymmetry, so it
    starts at target.anchor_col unmodified."""

    if side is Direction.SOUTH:
        touch_row = target.position.row + target_record.footprint_height
        return GridPosition(target.position.col + offset, touch_row - new_record.background_tiles)
    if side is Direction.NORTH:
        touch_row = (
            target.position.row + target_record.background_tiles - 1
            if target_record.background_tiles > 0
            else target.position.row - 1
        )
        return GridPosition(
            target.position.col + offset, touch_row - new_record.footprint_height + 1
        )
    # West/east: offset runs along target's *occupied* rows, not its full
    # anchor-inclusive footprint -- target's background rows (if any) are
    # its decorative back edge, not real table surface a side chair should
    # align against, so offset 0 starts at target.anchor_row +
    # background_tiles, same skip north touching already has to make.
    row_start = target.position.row + target_record.background_tiles
    if side is Direction.WEST:
        return GridPosition(target.position.col - new_record.footprint_width, row_start + offset)
    return GridPosition(  # Direction.EAST
        target.position.col + target_record.footprint_width, row_start + offset
    )


def _furniture_placement_error(
    office: Office,
    styles: FurnitureStyleManifest,
    style_def: FurnitureStyle,
    facing: Direction | None,
    position: GridPosition,
    occupied: dict[GridPosition, FurnitureItem],
) -> str | None:
    """Every rule in section 8 for one candidate `(style_def, facing,
    position)`, given `occupied` (every *other* item's real footprint
    cells). `None` means the placement is valid."""

    record = style_def.facing_record(facing)
    if record is None:
        return f"style {style_def.style!r} has no facing {facing.value if facing else None!r}"

    if style_def.can_place_on_walls:
        # Real Pixel Agents rule (webview-ui's canPlaceFurniture in
        # editorActions.ts): only the *bottom* row of a wall item's
        # footprint has to sit on a WALL tile -- every row above it
        # (including any background rows) can be void/floor/anything,
        # since a wall fixture's sprite extends upward from the tile it's
        # actually mounted on. `position` is the footprint's top-left, not
        # its wall row -- checking `position` itself against WALL directly
        # rejected every real multi-row wall fixture (e.g. HANGING_PLANT,
        # footprint_height=2), since its anchor sits one row above the
        # wall tile it's actually mounted on. Column still has to be a
        # real column -- there's no known case of a wall fixture
        # overhanging the grid horizontally -- but `position.row` may be
        # negative: upstream allows this for a fixture hanging off a wall
        # that's only one tile thick at the grid's own north edge, where
        # there's no floor/void tile "above" row 0 for `position` to be
        # in-bounds against in the first place -- only the wall segment
        # the bottom row actually touches has to be real. (No south-edge
        # equivalent: the wall row is always the *bottom* of the
        # footprint by definition, so the rows above it are already
        # inside the grid whenever the wall itself is the last row.)
        if not (0 <= position.col < office.grid.width):
            return f"position ({position.col}, {position.row}) is outside the grid"
        bottom_row = position.row + record.footprint_height - 1
        for dc in range(record.footprint_width):
            wall_cell = GridPosition(position.col + dc, bottom_row)
            if not office.grid.in_bounds(wall_cell):
                return f"footprint extends outside the grid at ({wall_cell.col}, {wall_cell.row})"
            actual_kind = office.grid.at(wall_cell).kind
            if actual_kind is not TileKind.WALL:
                return (
                    f"style {style_def.style!r} must have the bottom row of its "
                    f"{record.footprint_width}x{record.footprint_height} footprint anchored on "
                    f"a wall tile: row {bottom_row} (= anchor row {position.row} + "
                    f"footprint_height {record.footprint_height} - 1) must be WALL for every "
                    f"column {position.col}..{position.col + record.footprint_width - 1}, but "
                    f"({wall_cell.col}, {wall_cell.row}) is {actual_kind.value}, not wall"
                )
    else:
        if not office.grid.in_bounds(position):
            return f"position ({position.col}, {position.row}) is outside the grid"
        for cell in styles.occupied_cells(style_def.style, facing, position):
            if not office.grid.in_bounds(cell):
                return f"footprint extends outside the grid at ({cell.col}, {cell.row})"
            actual_kind = office.grid.at(cell).kind
            if actual_kind is not TileKind.FLOOR:
                return (
                    f"style {style_def.style!r} must be anchored on a floor tile: "
                    f"({cell.col}, {cell.row}) is {actual_kind.value}, not floor"
                )

    for cell in styles.occupied_cells(style_def.style, facing, position):
        # A can_place_on_walls style's footprint can legitimately reach a
        # row that doesn't exist in `Grid` at all (the phantom space above
        # row 0 the comment above describes) -- nothing real can occupy
        # that space, but another wall fixture's *own* phantom cells can
        # still collide with it, so this still has to fall through to the
        # `occupied` lookup below rather than returning outright.
        if not office.grid.in_bounds(cell) and not style_def.can_place_on_walls:
            return f"footprint extends outside the grid at ({cell.col}, {cell.row})"
        existing = occupied.get(cell)
        if existing is None:
            continue
        existing_style = styles.by_style_id(existing.style)
        stacking_allowed = (
            style_def.can_place_on_surfaces and existing.kind is FurnitureKind.DESK
        ) or (
            existing_style is not None
            and existing_style.can_place_on_surfaces
            and style_def.kind is FurnitureKind.DESK
        )
        if not stacking_allowed:
            return (
                f"overlaps furniture {existing.id!r} -- move it to a free tile first, "
                "then retry (moves cannot swap through an occupied cell)"
            )
    return None


def _validate_zones_in_bounds(office: Office) -> None:
    for zone in office.zones:
        if not _rect_in_bounds(zone.tiles, office):
            raise OfficeValidationError(f"zone {zone.id!r} extends outside the grid")


def _validate_furniture(office: Office, styles: FurnitureStyleManifest) -> None:
    occupied: dict[GridPosition, FurnitureItem] = {}
    for item in office.furniture:
        style_def = styles.by_style_id(item.style)
        if style_def is None:
            raise OfficeValidationError(f"furniture {item.id!r} has unknown style {item.style!r}")
        error = _furniture_placement_error(
            office, styles, style_def, item.facing, item.position, occupied
        )
        if error is not None:
            raise OfficeValidationError(f"furniture {item.id!r}: {error}")
        for cell in styles.occupied_cells(item.style, item.facing, item.position):
            occupied[cell] = item


def _validate_seats(office: Office) -> None:
    furniture_by_id = {item.id: item for item in office.furniture}
    occupant_ids = {occupant.id for occupant in office.occupants}
    seated_occupants: set[str] = set()
    for seat in office.seats:
        target = furniture_by_id.get(seat.occupies_furniture_id)
        if target is None or target.kind is not FurnitureKind.SEATING:
            raise OfficeValidationError(f"seat {seat.id!r} does not sit on a seating item")
        if seat.occupant_id is not None:
            if seat.occupant_id not in occupant_ids:
                raise OfficeValidationError(
                    f"seat {seat.id!r} references unknown occupant {seat.occupant_id!r}"
                )
            if seat.occupant_id in seated_occupants:
                raise OfficeValidationError(
                    f"occupant {seat.occupant_id!r} already holds another seat"
                )
            seated_occupants.add(seat.occupant_id)


__all__ = ["OfficeLayoutService", "OfficeValidationError", "Touching"]
