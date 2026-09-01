"""PainterLayoutService: painter's color-only mutation surface over the
office layout it shares with architect (docs/painter-design.md).

Deliberately narrower than architect's own `OfficeLayoutService`: no
`kind`/`material` parameter exists anywhere in this module, so no method
here can convert a cell between floor/wall/void, or add, move, or remove
furniture -- painter's write surface is physically incapable of any
structural change, not just instructed not to make one. Every mutation
follows architect's own service's shape: load the current `Office`, apply
the change to a *new* value (the IR dataclasses are frozen), and persist
-- there is no separate validate-then-commit step since every check here
is a precondition checked before any `replace()` happens, not a
post-mutation invariant like architect's own furniture-placement checks.

**Color model**: unlike architect's own `paint_tiles`/`create_zone`
(which validate against a small, fixed set of semantic color names),
painter has full control over the actual `{h,s,b,c}` colorize-mode value
-- there is no closed palette here at all. Every write takes an `HsbColor`
(hue 0-360, saturation 0-100, brightness/contrast -100..100) and stores it
*exactly* as `raw_color`, so an arbitrary painter-chosen color round-trips
losslessly rather than snapping to the nearest of a dozen named colors.
`color` (the semantic-name field every `TileCell`/`FurnitureItem` still
carries) is set to the closest fixed-palette name purely as a
human-readable label for `describe_tile_colors`/`describe_furniture_colors`
output -- it is never used to reconstruct the actual color on encode
(`raw_color` always takes precedence there, see
`pixelagents/infrastructure/pixel_agents_adapter.py`). painter's own LLM
is expected to reason about hue/saturation/brightness/contrast (or a hex
shorthand converted to it) directly -- "make it blue," "a lighter shade,"
"#3b5a7a" -- not to pick from a name list. See docs/painter-design.md's
color model revision for the full rationale.
"""

from __future__ import annotations

from dataclasses import replace

from pixelagents.domain import (
    FurnitureItem,
    FurnitureKind,
    GridPosition,
    GridRect,
    Office,
    TileCell,
    TileKind,
)
from pixelagents.infrastructure.color_names import (
    BRIGHTNESS_MAX,
    BRIGHTNESS_MIN,
    CONTRAST_MAX,
    CONTRAST_MIN,
    HUE_MAX,
    HUE_MIN,
    SATURATION_MAX,
    SATURATION_MIN,
    HsbColor,
    nearest_name,
)
from pixelagents.infrastructure.furniture_styles import FurnitureStyleLoader, FurnitureStyleManifest

from ..infrastructure.office_layout_repository import OfficeLayoutRepository


class PainterValidationError(Exception):
    """Raised for any input a painter tool should report back to the LLM
    as `Output.status="error"` rather than letting propagate -- same
    "handler does the actual work and must not raise for expected failure
    modes" convention architect's own `OfficeValidationError` follows."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PainterLayoutService:
    def __init__(
        self,
        repository: OfficeLayoutRepository,
        style_loader: FurnitureStyleLoader,
    ) -> None:
        self._repository = repository
        self._style_loader = style_loader

    async def describe_tile_colors(self, *, area: GridRect) -> list[TileCell]:
        office, _ = await self._load()
        if not _rect_in_bounds(area, office):
            raise PainterValidationError(
                f"area extends outside the {office.width}x{office.height} grid "
                f"(0-based coordinates, far edge exclusive: {_rect_out_of_bounds_detail(area, office)})"
            )
        return [office.grid.at(position) for position in area.positions()]

    async def describe_furniture_colors(
        self, *, kind: FurnitureKind | None = None, style: str | None = None
    ) -> list[FurnitureItem]:
        office, _ = await self._load()
        items = office.furniture
        if kind is not None:
            items = [item for item in items if item.kind is kind]
        if style is not None:
            items = [item for item in items if item.style == style]
        return items

    async def recolor_tiles(self, *, area: GridRect, color: HsbColor) -> None:
        office, styles = await self._load()
        _validate_hsb(color)
        if not _rect_in_bounds(area, office):
            raise PainterValidationError(
                f"area extends outside the {office.width}x{office.height} grid "
                f"(0-based coordinates, far edge exclusive: {_rect_out_of_bounds_detail(area, office)})"
            )

        label = nearest_name(color)
        raw = _as_tuple(color)
        updates: dict[GridPosition, TileCell] = {}
        for position in area.positions():
            cell = office.grid.at(position)
            if cell.kind is TileKind.VOID:
                raise PainterValidationError(
                    f"cannot recolor void at ({position.col}, {position.row}) -- void tiles "
                    "are outside the playable map and have no sprite to color"
                )
            if cell.kind is TileKind.FLOOR:
                updates[position] = TileCell.floor(
                    cell.material,  # type: ignore[arg-type]
                    label,
                    raw_color=raw,
                    zone_label=cell.zone_label,
                )
            else:
                updates[position] = TileCell.wall(
                    color=label, raw_color=raw, zone_label=cell.zone_label
                )

        new_grid = office.grid.replacing(updates)
        new_office = replace(office, grid=new_grid)
        await self._persist(new_office, styles)

    async def recolor_furniture(self, *, furniture_id: str, color: HsbColor) -> FurnitureItem:
        office, styles = await self._load()
        _validate_hsb(color)
        item = _find_furniture(office, furniture_id)

        updated = replace(item, color=nearest_name(color), raw_color=_as_tuple(color))
        new_furniture = [updated if f.id == furniture_id else f for f in office.furniture]
        new_office = replace(office, furniture=new_furniture)
        await self._persist(new_office, styles)
        return updated

    async def recolor_furniture_by_style(
        self, *, kind: FurnitureKind, style: str, color: HsbColor
    ) -> int:
        """Recolors every placed item of `(kind, style)` at once. Returns
        how many items were recolored -- 0 is not an error (nothing
        matched), since an LLM asking "recolor all the wooden chairs" when
        there happen to be none yet is a normal, answerable case, not a
        malformed request the way an invalid `color` is."""

        office, styles = await self._load()
        _validate_hsb(color)

        matched_ids = {
            item.id for item in office.furniture if item.kind is kind and item.style == style
        }
        if not matched_ids:
            return 0

        label = nearest_name(color)
        raw = _as_tuple(color)
        new_furniture = [
            replace(item, color=label, raw_color=raw) if item.id in matched_ids else item
            for item in office.furniture
        ]
        new_office = replace(office, furniture=new_furniture)
        await self._persist(new_office, styles)
        return len(matched_ids)

    async def _load(self) -> tuple[Office, FurnitureStyleManifest]:
        styles = self._style_loader.styles()
        office = await self._repository.load(styles)
        return office, styles

    async def _persist(self, office: Office, styles: FurnitureStyleManifest) -> None:
        await self._repository.save(office, styles)


def _find_furniture(office: Office, furniture_id: str) -> FurnitureItem:
    for item in office.furniture:
        if item.id == furniture_id:
            return item
    raise PainterValidationError(f"furniture {furniture_id!r} does not exist")


def _validate_hsb(color: HsbColor) -> None:
    """Defense in depth -- the tool layer's `Field(ge=..., le=...)`
    constraints already reject an out-of-range value before it reaches
    here, same convention architect's own service re-checks `material`'s
    1-9 bound rather than trusting the tool layer alone."""

    problems: list[str] = []
    if not HUE_MIN <= color["h"] <= HUE_MAX:
        problems.append(f"hue {color['h']} must be {HUE_MIN}-{HUE_MAX}")
    if not SATURATION_MIN <= color["s"] <= SATURATION_MAX:
        problems.append(f"saturation {color['s']} must be {SATURATION_MIN}-{SATURATION_MAX}")
    if not BRIGHTNESS_MIN <= color["b"] <= BRIGHTNESS_MAX:
        problems.append(f"brightness {color['b']} must be {BRIGHTNESS_MIN}-{BRIGHTNESS_MAX}")
    if not CONTRAST_MIN <= color["c"] <= CONTRAST_MAX:
        problems.append(f"contrast {color['c']} must be {CONTRAST_MIN}-{CONTRAST_MAX}")
    if problems:
        raise PainterValidationError("; ".join(problems))


def _as_tuple(color: HsbColor) -> tuple[int, int, int, int]:
    return (color["h"], color["s"], color["b"], color["c"])


def _rect_in_bounds(rect: GridRect, office: Office) -> bool:
    return (
        rect.top_left.col >= 0
        and rect.top_left.row >= 0
        and rect.top_left.col + rect.width <= office.width
        and rect.top_left.row + rect.height <= office.height
    )


def _rect_out_of_bounds_detail(rect: GridRect, office: Office) -> str:
    """Spells out exactly which edge of `rect` overshoots, for a
    `PainterValidationError` message an LLM can act on without
    re-guessing -- same convention architect's own service uses."""

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


__all__ = ["PainterLayoutService", "PainterValidationError"]
