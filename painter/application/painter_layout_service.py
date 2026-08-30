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
from pixelagents.infrastructure.color_names import known_names
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
        self, repository: OfficeLayoutRepository, style_loader: FurnitureStyleLoader
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

    async def recolor_tiles(self, *, area: GridRect, color: str) -> None:
        office, styles = await self._load()
        if color not in known_names():
            raise PainterValidationError(f"unknown color {color!r}")
        if not _rect_in_bounds(area, office):
            raise PainterValidationError(
                f"area extends outside the {office.width}x{office.height} grid "
                f"(0-based coordinates, far edge exclusive: {_rect_out_of_bounds_detail(area, office)})"
            )

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
                    color,
                    zone_label=cell.zone_label,
                )
            else:
                updates[position] = TileCell.wall(color=color, zone_label=cell.zone_label)

        new_grid = office.grid.replacing(updates)
        new_office = replace(office, grid=new_grid)
        await self._repository.save(new_office, styles)

    async def recolor_furniture(self, *, furniture_id: str, color: str) -> FurnitureItem:
        office, styles = await self._load()
        if color not in known_names():
            raise PainterValidationError(f"unknown color {color!r}")
        item = _find_furniture(office, furniture_id)

        updated = replace(item, color=color, raw_color=None)
        new_furniture = [updated if f.id == furniture_id else f for f in office.furniture]
        new_office = replace(office, furniture=new_furniture)
        await self._repository.save(new_office, styles)
        return updated

    async def recolor_furniture_by_style(
        self, *, kind: FurnitureKind, style: str, color: str
    ) -> int:
        """Recolors every placed item of `(kind, style)` at once. Returns
        how many items were recolored -- 0 is not an error (nothing
        matched), since an LLM asking "recolor all the wooden chairs" when
        there happen to be none yet is a normal, answerable case, not a
        malformed request the way an unknown `color` is."""

        office, styles = await self._load()
        if color not in known_names():
            raise PainterValidationError(f"unknown color {color!r}")

        matched_ids = {
            item.id for item in office.furniture if item.kind is kind and item.style == style
        }
        if not matched_ids:
            return 0

        new_furniture = [
            replace(item, color=color, raw_color=None) if item.id in matched_ids else item
            for item in office.furniture
        ]
        new_office = replace(office, furniture=new_furniture)
        await self._repository.save(new_office, styles)
        return len(matched_ids)

    async def _load(self) -> tuple[Office, FurnitureStyleManifest]:
        styles = self._style_loader.styles()
        office = await self._repository.load(styles)
        return office, styles


def _find_furniture(office: Office, furniture_id: str) -> FurnitureItem:
    for item in office.furniture:
        if item.id == furniture_id:
            return item
    raise PainterValidationError(f"furniture {furniture_id!r} does not exist")


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
