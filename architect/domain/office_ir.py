"""The Semantic Intermediate Representation (IR) between architect's LLM
tools/Discord commands and Pixel Agents' raw layout JSON.

Zero framework imports -- no `pixelagents`, no `pydantic`, no `redbot` --
same "trivially unit-testable" convention `domain/models.py` already
follows, extended here to "zero Pixel-Agents-specific imports" too: this
module must never know an asset ID string or HSB math. It *does* store
raw tile pattern integers (`TileCell.material`) -- a lossless-storage
decision, not a Pixel-Agents-specific import, see
docs/architect-semantic-ir-design.md section 4.1 (v2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Direction(Enum):
    NORTH = "north"  # away from viewer / "back"
    SOUTH = "south"  # toward viewer / "front"
    EAST = "east"  # viewer's right / "right"
    WEST = "west"  # viewer's left / "left"


class FurnitureKind(Enum):
    """Coarse semantic category an LLM reasons in. Derived from the
    catalog's own `category`/`isDesk` flags (see the generated style
    manifest, section 6.4), not invented."""

    DESK = "desk"
    SEATING = "seating"  # chairs, benches, sofas
    STORAGE = "storage"  # bookshelves, bins
    DECOR = "decor"  # plants, paintings, clocks
    ELECTRONICS = "electronics"
    WALL_FIXTURE = "wall_fixture"  # whiteboard, wall-mounted decor
    MISC = "misc"


@dataclass(frozen=True, slots=True)
class GridPosition:
    col: int
    row: int


@dataclass(frozen=True, slots=True)
class GridRect:
    """Inclusive tile rectangle. Used for a `Zone`'s bounding box and for
    any tool that paints/queries a bounded region (`paint_tiles`,
    `describe_tiles`) -- a plain rectangle, not a polygon, since a zone's
    *exact* per-tile membership always lives on `Grid.cells[i].zone_label`
    directly; this is just the bounding-box summary."""

    top_left: GridPosition
    width: int
    height: int

    def contains(self, position: GridPosition) -> bool:
        return (
            self.top_left.col <= position.col < self.top_left.col + self.width
            and self.top_left.row <= position.row < self.top_left.row + self.height
        )

    def overlaps(self, other: GridRect) -> bool:
        return (
            self.top_left.col < other.top_left.col + other.width
            and other.top_left.col < self.top_left.col + self.width
            and self.top_left.row < other.top_left.row + other.height
            and other.top_left.row < self.top_left.row + self.height
        )

    def positions(self) -> list[GridPosition]:
        """Every cell inside, row-major."""

        return [
            GridPosition(self.top_left.col + dc, self.top_left.row + dr)
            for dr in range(self.height)
            for dc in range(self.width)
        ]


class TileKind(Enum):
    """Derived, never independently authored -- see `TileCell`'s own
    invariant. Kept as an explicit enum (rather than making every call
    site compare `material` to magic numbers 0/255) purely for
    readability at every "is this walkable/wall/void" check."""

    WALL = "wall"  # Pixel JSON pattern 0
    VOID = "void"  # Pixel JSON pattern 255 -- outside the playable map
    FLOOR = "floor"  # Pixel JSON pattern 1-9


@dataclass(frozen=True, slots=True)
class TileCell:
    """One grid cell, exactly mirroring one `tiles[i]`/`tileColors[i]`/
    `areaTiles[i]` triple -- the lossless ground truth
    docs/architect-semantic-ir-design.md section 4.1 (v2) describes.
    `kind` and `material` must never disagree -- use `TileCell.floor()`/
    `.wall()`/`.void()` below, never the raw constructor with a
    hand-picked `kind`, so this invariant can never be violated by
    construction."""

    kind: TileKind
    material: int | None  # 1-9 for FLOOR, always None otherwise -- opaque,
    # no semantic meaning (section 1), constrained 1-9 by
    # Field(ge=1, le=9) wherever a tool accepts it
    color: str | None  # semantic color name (section 6.3), FLOOR only
    zone_label: str | None  # which Zone owns this cell, if any

    @classmethod
    def wall(cls, *, zone_label: str | None = None) -> TileCell:
        # `zone_label` is still representable on a wall cell -- `areaTiles`
        # is an independent array upstream, so a wall tile can genuinely
        # have a zone label in the raw JSON, and losslessness means
        # preserving that even though it renders oddly.
        return cls(TileKind.WALL, material=None, color=None, zone_label=zone_label)

    @classmethod
    def void(cls, *, zone_label: str | None = None) -> TileCell:
        return cls(TileKind.VOID, material=None, color=None, zone_label=zone_label)

    @classmethod
    def floor(cls, material: int, color: str | None, *, zone_label: str | None = None) -> TileCell:
        return cls(TileKind.FLOOR, material=material, color=color, zone_label=zone_label)


@dataclass(frozen=True, slots=True)
class Grid:
    """The lossless ground truth (section 4.1, v2). Row-major, exactly
    `width * height` cells, always -- constructed once per `decode()`,
    replaced wholesale (not patched in place) by every mutation's
    copy-on-write, same shape `Office` itself already uses."""

    width: int
    height: int
    cells: tuple[TileCell, ...]

    def __post_init__(self) -> None:
        expected = self.width * self.height
        if len(self.cells) != expected:
            raise ValueError(f"Grid expects {expected} cells, got {len(self.cells)}")

    def _index(self, position: GridPosition) -> int:
        return position.row * self.width + position.col

    def in_bounds(self, position: GridPosition) -> bool:
        return 0 <= position.col < self.width and 0 <= position.row < self.height

    def at(self, position: GridPosition) -> TileCell:
        return self.cells[self._index(position)]

    def replacing(self, updates: dict[GridPosition, TileCell]) -> Grid:
        """Copy-on-write: a new `Grid` with `updates` applied, everything
        else byte-for-byte identical."""

        cells = list(self.cells)
        for position, cell in updates.items():
            cells[self._index(position)] = cell
        return Grid(self.width, self.height, tuple(cells))


@dataclass(frozen=True, slots=True)
class FurnitureItem:
    """One placed piece of furniture. `id` is always identical to the
    Pixel Agents `uid` it was decoded from (or, for an item architect just
    created, the freshly-minted id that becomes its `uid` on first save --
    section 6.2) -- there is no separate architect-owned id namespace.
    Occupied cells are *derived* on demand from
    `(style, facing, position)` against the style manifest's real
    footprint (section 6.4), not stored here -- storing them would risk
    disagreeing with the manifest after a webview rebuild changes it."""

    id: str
    kind: FurnitureKind
    style: str  # one of the ids in the generated style manifest (section 6.4) --
    # NOT a Pixel Agents asset ID and NOT a freely chosen string
    position: GridPosition  # top-left anchor of its footprint
    facing: Direction | None = None  # None for non-orientable items (plants, clocks)
    label: str | None = None  # optional human/LLM-given name, e.g. "Priya's desk"
    color: str | None = None  # optional semantic color name, e.g. "blue" (section 6.3)


@dataclass(frozen=True, slots=True)
class Seat:
    """A place an occupant can sit. First-class in the IR even though
    Pixel Agents derives seats from chair footprints at render time
    (section 1)."""

    id: str
    occupies_furniture_id: str  # FurnitureItem.id of the chair/bench/sofa
    facing: Direction
    occupant_id: str | None = None  # Occupant.id, or None if empty


@dataclass(frozen=True, slots=True)
class Occupant:
    """An agent or person who can hold a seat. No Pixel Agents JSON field
    corresponds to this today -- modeled now so seat-assignment tools have
    a target the day architect grows a creation path (still absent)."""

    id: str
    display_name: str
    role: str | None = None  # e.g. "lead", "reviewer" -- free text for now


@dataclass(frozen=True, slots=True)
class Zone:
    """Promotes Pixel Agents' own `AreaDefinition`/`areaTiles` concept to
    a first-class IR entity. `tiles` is a bounding-rect *summary*,
    computed from `Grid` at read time (every cell with this zone's
    `zone_label` is the real, exact membership) -- not separately
    authored or persisted, so there is no passthrough side-channel to
    keep in sync."""

    id: str
    label: str
    color: str  # semantic color name (section 6.3), not hex
    tiles: GridRect  # bounding-box summary, derived from Grid


@dataclass(frozen=True, slots=True)
class Office:
    """The IR root -- one architect layout. `grid` is the lossless ground
    truth (section 4.1, v2); `zones` are labels over it -- the only
    spatial-grouping concept exposed to the LLM, matching Pixel Agents'
    own model, which has no room concept at all (section 1). `furniture`/
    `seats`/`occupants` are unchanged in shape from v1. `width`/`height`
    delegate to `grid` rather than being separately stored, so there is
    exactly one place grid dimensions can live -- never two fields that
    could disagree.

    `passthrough` still exists for what the IR has no concept for at all
    -- `pets`, `carpetTiles`, `layoutRevision`, unrecognized furniture,
    and the `id <-> uid` map (section 6.2)."""

    grid: Grid
    zones: list[Zone] = field(default_factory=list)
    furniture: list[FurnitureItem] = field(default_factory=list)
    seats: list[Seat] = field(default_factory=list)
    occupants: list[Occupant] = field(default_factory=list)
    passthrough: dict[str, object] = field(default_factory=dict)

    @property
    def width(self) -> int:
        return self.grid.width

    @property
    def height(self) -> int:
        return self.grid.height


__all__ = [
    "Direction",
    "FurnitureItem",
    "FurnitureKind",
    "Grid",
    "GridPosition",
    "GridRect",
    "Occupant",
    "Office",
    "Seat",
    "TileCell",
    "TileKind",
    "Zone",
]
