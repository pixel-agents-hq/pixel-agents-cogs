"""LLM tools for architect's office layout, per
docs/architect-semantic-ir-design.md section 7.

Every mutation tool is a thin wrapper: pydantic `Input` -> an
`OfficeLayoutService` call -> pydantic `Output`, translating
`OfficeValidationError` into `Output.status="error"` + `message` rather
than letting it propagate -- same "handler does the actual work and must
not raise for expected failure modes" convention `tools/base.py` already
documents. `place_furniture`/`move_furniture` build their `Input` model
fresh on every access (`pydantic.create_model`) so the `style` field's
JSON Schema `enum` always reflects the *live* style manifest -- see
`ToolLoopService._wire_spec()`, which calls `tool.Input.model_json_schema()`
once per turn, never cached.

There is no room concept here -- `Zone` is the only spatial-grouping
concept exposed to the LLM, matching Pixel Agents' own model."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from ..application.office_layout_service import OfficeLayoutService, OfficeValidationError
from ..domain.office_ir import (
    Direction,
    FurnitureItem,
    FurnitureKind,
    GridPosition,
    GridRect,
    Office,
    Seat,
    TileCell,
    TileKind,
    Zone,
)
from ..infrastructure.furniture_styles import FurnitureStyleLoader, FurnitureStyleManifest
from .base import ToolSpec

_DIRECTION_VALUES: tuple[str, ...] = tuple(direction.value for direction in Direction)
_KIND_VALUES: tuple[str, ...] = tuple(kind.value for kind in FurnitureKind)
_PAINT_KIND_VALUES: tuple[str, ...] = (TileKind.FLOOR.value, TileKind.WALL.value)


class OccupiedCellSummary(BaseModel):
    col: int
    row: int


class FurnitureSummary(BaseModel):
    id: str
    kind: str
    style: str
    col: int
    row: int
    facing: str | None = None
    label: str | None = None
    color: str | None = None
    # Full footprint (section 6.4), not just the anchor tile -- lets the
    # LLM reason about what a placed item actually blocks without a
    # separate describe_tiles round trip.
    occupied_cells: list[OccupiedCellSummary] = Field(default_factory=list)


class ZoneSummary(BaseModel):
    id: str
    label: str
    color: str
    col: int
    row: int
    width: int
    height: int


class SeatSummary(BaseModel):
    id: str
    occupies_furniture_id: str
    facing: str
    occupant_id: str | None = None


class TileSummary(BaseModel):
    col: int
    row: int
    kind: str
    material: int | None = None
    color: str | None = None
    zone_label: str | None = None
    furniture_id: str | None = None


def _furniture_summary(item: FurnitureItem, styles: FurnitureStyleManifest) -> FurnitureSummary:
    return FurnitureSummary(
        id=item.id,
        kind=item.kind.value,
        style=item.style,
        col=item.position.col,
        row=item.position.row,
        facing=item.facing.value if item.facing else None,
        label=item.label,
        color=item.color,
        occupied_cells=[
            OccupiedCellSummary(col=cell.col, row=cell.row)
            for cell in styles.occupied_cells(item.style, item.facing, item.position)
        ],
    )


def _zone_summary(zone: Zone) -> ZoneSummary:
    return ZoneSummary(
        id=zone.id,
        label=zone.label,
        color=zone.color,
        col=zone.tiles.top_left.col,
        row=zone.tiles.top_left.row,
        width=zone.tiles.width,
        height=zone.tiles.height,
    )


def _seat_summary(seat: Seat) -> SeatSummary:
    return SeatSummary(
        id=seat.id,
        occupies_furniture_id=seat.occupies_furniture_id,
        facing=seat.facing.value,
        occupant_id=seat.occupant_id,
    )


def _tile_summary(
    position: GridPosition, cell: TileCell, furniture_by_cell: dict[GridPosition, str]
) -> TileSummary:
    return TileSummary(
        col=position.col,
        row=position.row,
        kind=cell.kind.value,
        material=cell.material,
        color=cell.color,
        zone_label=cell.zone_label,
        furniture_id=furniture_by_cell.get(position),
    )


def _furniture_by_cell(office: Office, styles: FurnitureStyleManifest) -> dict[GridPosition, str]:
    result: dict[GridPosition, str] = {}
    for item in office.furniture:
        for cell in styles.occupied_cells(item.style, item.facing, item.position):
            result[cell] = item.id
    return result


def _error(output_type: type[BaseModel], exc: OfficeValidationError) -> BaseModel:
    """Every mutation/query `Output` carries `status`/`message` fields --
    build an error instance without repeating that boilerplate at each
    call site. `output_type` must declare both with defaults so this can
    construct one with only the two fields set."""

    return output_type(status="error", message=exc.reason)


# -- describe_office -----------------------------------------------------


class DescribeOfficeInput(BaseModel):
    pass


class DescribeOfficeOutput(BaseModel):
    width: int
    height: int
    zones: list[ZoneSummary]
    furniture: list[FurnitureSummary]
    seats: list[SeatSummary]


class DescribeOfficeTool:
    name = "describe_office"
    description = "Describe the current state of architect's office: zones, furniture, and seats."

    def __init__(self, service: OfficeLayoutService, style_loader: FurnitureStyleLoader) -> None:
        self._service = service
        self._style_loader = style_loader

    @property
    def Input(self) -> type[BaseModel]:
        return DescribeOfficeInput

    @property
    def Output(self) -> type[BaseModel]:
        return DescribeOfficeOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        office = await self._service.describe()
        styles = self._style_loader.styles()
        return DescribeOfficeOutput(
            width=office.width,
            height=office.height,
            zones=[_zone_summary(zone) for zone in office.zones],
            furniture=[_furniture_summary(item, styles) for item in office.furniture],
            seats=[_seat_summary(seat) for seat in office.seats],
        )


# -- find_furniture -----------------------------------------------------


class FindFurnitureInput(BaseModel):
    kind: Literal[_KIND_VALUES] | None = Field(  # type: ignore[valid-type]
        default=None, description="Only return furniture of this kind."
    )


class FindFurnitureOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    furniture: list[FurnitureSummary] = Field(default_factory=list)


class FindFurnitureTool:
    name = "find_furniture"
    description = "Find furniture in architect's office, optionally filtered by kind."

    def __init__(self, service: OfficeLayoutService, style_loader: FurnitureStyleLoader) -> None:
        self._service = service
        self._style_loader = style_loader

    @property
    def Input(self) -> type[BaseModel]:
        return FindFurnitureInput

    @property
    def Output(self) -> type[BaseModel]:
        return FindFurnitureOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, FindFurnitureInput)
        items = await self._service.find_furniture(
            kind=FurnitureKind(raw_input.kind) if raw_input.kind else None
        )
        styles = self._style_loader.styles()
        return FindFurnitureOutput(furniture=[_furniture_summary(item, styles) for item in items])


# -- describe_tiles -----------------------------------------------------


class DescribeTilesInput(BaseModel):
    col: int = Field(description="Top-left column of the region.")
    row: int = Field(description="Top-left row of the region.")
    width: int = Field(description="Region width in tiles.")
    height: int = Field(description="Region height in tiles.")


class DescribeTilesOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    tiles: list[TileSummary] = Field(default_factory=list)


class DescribeTilesTool:
    name = "describe_tiles"
    description = (
        "Show the exact per-tile state (kind, material, color, zone, occupying furniture) "
        "of a bounded region, capped at 400 tiles. Use this to check what's actually at a "
        "position before placing or painting something there."
    )

    def __init__(self, service: OfficeLayoutService, style_loader: FurnitureStyleLoader) -> None:
        self._service = service
        self._style_loader = style_loader

    @property
    def Input(self) -> type[BaseModel]:
        return DescribeTilesInput

    @property
    def Output(self) -> type[BaseModel]:
        return DescribeTilesOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, DescribeTilesInput)
        area = GridRect(
            GridPosition(raw_input.col, raw_input.row), raw_input.width, raw_input.height
        )
        try:
            tiles = await self._service.describe_tiles(area=area)
        except OfficeValidationError as exc:
            return _error(DescribeTilesOutput, exc)
        office = await self._service.describe()
        furniture_by_cell = _furniture_by_cell(office, self._style_loader.styles())
        return DescribeTilesOutput(
            tiles=[
                _tile_summary(position, cell, furniture_by_cell)
                for position, cell in zip(area.positions(), tiles, strict=True)
            ]
        )


# -- paint_tiles -----------------------------------------------------


class PaintTilesInput(BaseModel):
    col: int = Field(description="Top-left column of the region to paint.")
    row: int = Field(description="Top-left row of the region to paint.")
    width: int = Field(description="Region width in tiles.")
    height: int = Field(description="Region height in tiles.")
    kind: Literal[_PAINT_KIND_VALUES] = Field(  # type: ignore[valid-type]
        description="'floor' or 'wall'."
    )
    material: int | None = Field(
        default=None,
        ge=1,
        le=9,
        description="Floor pattern number 1-9. Required when kind is 'floor', ignored for 'wall'.",
    )
    color: str | None = Field(
        default=None, description="Semantic color name, floor only. Omit to keep the current color."
    )


class PaintTilesOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None


class PaintTilesTool:
    name = "paint_tiles"
    description = (
        "Paint a rectangular region to floor or wall. Painting a wall over furniture fails "
        "(remove it first)."
    )

    def __init__(self, service: OfficeLayoutService) -> None:
        self._service = service

    @property
    def Input(self) -> type[BaseModel]:
        return PaintTilesInput

    @property
    def Output(self) -> type[BaseModel]:
        return PaintTilesOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, PaintTilesInput)
        try:
            await self._service.paint_tiles(
                area=GridRect(
                    GridPosition(raw_input.col, raw_input.row), raw_input.width, raw_input.height
                ),
                kind=TileKind(raw_input.kind),
                material=raw_input.material,
                color=raw_input.color,
            )
        except OfficeValidationError as exc:
            return _error(PaintTilesOutput, exc)
        return PaintTilesOutput()


# -- place_furniture / move_furniture: dynamic style/facing enum -----------


def _build_place_furniture_input(style_loader: FurnitureStyleLoader) -> type[BaseModel]:
    """A fresh `Input` model built on every access, per section 7's
    "Constraining style/facing" paragraph: `style`'s JSON Schema `enum` is
    a `Literal` of whatever style ids the *live* manifest currently has --
    pydantic's own `Literal` validation then rejects an unknown style at
    call time with no extra validator code needed. Falls back to a bare
    `str` only when the manifest is empty (no webview built yet), in
    which case `OfficeLayoutService`'s own validation is the backstop."""

    style_ids = style_loader.styles().style_ids()
    style_type: Any = Literal[tuple(style_ids)] if style_ids else str

    return create_model(
        "PlaceFurnitureInput",
        kind=(Literal[_KIND_VALUES], Field(description="Coarse category of the item to place.")),
        style=(
            style_type,
            Field(description="Style id -- must exist in the current style manifest."),
        ),
        col=(int, Field(description="Tile column to anchor the item at.")),
        row=(int, Field(description="Tile row to anchor the item at.")),
        facing=(
            Literal[_DIRECTION_VALUES] | None,
            Field(default=None, description="Facing direction. Omit to use the style's default."),
        ),
        label=(str | None, Field(default=None, description="Optional human-readable name.")),
    )


class PlaceFurnitureOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    item: FurnitureSummary | None = None


class PlaceFurnitureTool:
    name = "place_furniture"
    description = (
        "Place a new piece of furniture at an exact position, anchored on a floor or wall "
        "tile as its style requires. Use describe_tiles first to find a free spot."
    )

    def __init__(self, service: OfficeLayoutService, style_loader: FurnitureStyleLoader) -> None:
        self._service = service
        self._style_loader = style_loader

    @property
    def Input(self) -> type[BaseModel]:
        return _build_place_furniture_input(self._style_loader)

    @property
    def Output(self) -> type[BaseModel]:
        return PlaceFurnitureOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        facing_raw = getattr(raw_input, "facing", None)
        try:
            item = await self._service.place_furniture(
                kind=FurnitureKind(raw_input.kind),  # type: ignore[attr-defined]
                style=raw_input.style,  # type: ignore[attr-defined]
                position=GridPosition(raw_input.col, raw_input.row),  # type: ignore[attr-defined]
                facing=Direction(facing_raw) if facing_raw else None,
                label=getattr(raw_input, "label", None),
            )
        except OfficeValidationError as exc:
            return _error(PlaceFurnitureOutput, exc)
        return PlaceFurnitureOutput(item=_furniture_summary(item, self._style_loader.styles()))


def _build_move_furniture_input() -> type[BaseModel]:
    return create_model(
        "MoveFurnitureInput",
        furniture_id=(str, Field(description="Id of the furniture item to move.")),
        col=(int, Field(description="New tile column.")),
        row=(int, Field(description="New tile row.")),
        facing=(
            Literal[_DIRECTION_VALUES] | None,
            Field(default=None, description="New facing direction. Omit to keep the current one."),
        ),
    )


class MoveFurnitureOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    item: FurnitureSummary | None = None


class MoveFurnitureTool:
    name = "move_furniture"
    description = "Move (and optionally reorient) an existing piece of furniture."

    def __init__(self, service: OfficeLayoutService, style_loader: FurnitureStyleLoader) -> None:
        self._service = service
        self._style_loader = style_loader

    @property
    def Input(self) -> type[BaseModel]:
        return _build_move_furniture_input()

    @property
    def Output(self) -> type[BaseModel]:
        return MoveFurnitureOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        facing_raw = getattr(raw_input, "facing", None)
        try:
            item = await self._service.move_furniture(
                furniture_id=raw_input.furniture_id,  # type: ignore[attr-defined]
                position=GridPosition(raw_input.col, raw_input.row),  # type: ignore[attr-defined]
                facing=Direction(facing_raw) if facing_raw else None,
            )
        except OfficeValidationError as exc:
            return _error(MoveFurnitureOutput, exc)
        return MoveFurnitureOutput(item=_furniture_summary(item, self._style_loader.styles()))


# -- remove_furniture -----------------------------------------------------


class RemoveFurnitureInput(BaseModel):
    furniture_id: str = Field(description="Id of the furniture item to remove.")


class RemoveFurnitureOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None


class RemoveFurnitureTool:
    name = "remove_furniture"
    description = "Remove a piece of furniture (and any seat on it)."

    def __init__(self, service: OfficeLayoutService) -> None:
        self._service = service

    @property
    def Input(self) -> type[BaseModel]:
        return RemoveFurnitureInput

    @property
    def Output(self) -> type[BaseModel]:
        return RemoveFurnitureOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, RemoveFurnitureInput)
        try:
            await self._service.remove_furniture(furniture_id=raw_input.furniture_id)
        except OfficeValidationError as exc:
            return _error(RemoveFurnitureOutput, exc)
        return RemoveFurnitureOutput()


# -- create_zone / resize_zone / remove_zone -----------------------------------------------------


class CreateZoneInput(BaseModel):
    label: str = Field(description="Unique zone name, e.g. 'Quiet Zone'.")
    color: str = Field(description="Semantic color name, e.g. 'cool_blue'.")
    col: int
    row: int
    width: int
    height: int


class CreateZoneOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    zone: ZoneSummary | None = None


class CreateZoneTool:
    name = "create_zone"
    description = "Create a new named overlay zone in architect's office."

    def __init__(self, service: OfficeLayoutService) -> None:
        self._service = service

    @property
    def Input(self) -> type[BaseModel]:
        return CreateZoneInput

    @property
    def Output(self) -> type[BaseModel]:
        return CreateZoneOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, CreateZoneInput)
        tiles = GridRect(
            GridPosition(raw_input.col, raw_input.row), raw_input.width, raw_input.height
        )
        try:
            zone = await self._service.create_zone(
                label=raw_input.label, color=raw_input.color, tiles=tiles
            )
        except OfficeValidationError as exc:
            return _error(CreateZoneOutput, exc)
        return CreateZoneOutput(zone=_zone_summary(zone))


class ResizeZoneInput(BaseModel):
    zone_id: str = Field(description="Id of the zone to resize.")
    col: int
    row: int
    width: int
    height: int


class ResizeZoneOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    zone: ZoneSummary | None = None


class ResizeZoneTool:
    name = "resize_zone"
    description = "Replace a zone's tile region."

    def __init__(self, service: OfficeLayoutService) -> None:
        self._service = service

    @property
    def Input(self) -> type[BaseModel]:
        return ResizeZoneInput

    @property
    def Output(self) -> type[BaseModel]:
        return ResizeZoneOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, ResizeZoneInput)
        tiles = GridRect(
            GridPosition(raw_input.col, raw_input.row), raw_input.width, raw_input.height
        )
        try:
            zone = await self._service.resize_zone(zone_id=raw_input.zone_id, tiles=tiles)
        except OfficeValidationError as exc:
            return _error(ResizeZoneOutput, exc)
        return ResizeZoneOutput(zone=_zone_summary(zone))


class RemoveZoneInput(BaseModel):
    zone_id: str = Field(description="Id of the zone to remove.")


class RemoveZoneOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None


class RemoveZoneTool:
    name = "remove_zone"
    description = "Remove a zone."

    def __init__(self, service: OfficeLayoutService) -> None:
        self._service = service

    @property
    def Input(self) -> type[BaseModel]:
        return RemoveZoneInput

    @property
    def Output(self) -> type[BaseModel]:
        return RemoveZoneOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, RemoveZoneInput)
        try:
            await self._service.remove_zone(zone_id=raw_input.zone_id)
        except OfficeValidationError as exc:
            return _error(RemoveZoneOutput, exc)
        return RemoveZoneOutput()


# -- seat_occupant / vacate_seat -----------------------------------------------------


class SeatOccupantInput(BaseModel):
    seat_id: str = Field(description="Id of the seat to assign.")
    occupant_id: str = Field(description="Id of the occupant to seat.")


class SeatOccupantOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    seat: SeatSummary | None = None


class SeatOccupantTool:
    name = "seat_occupant"
    description = "Assign an occupant to an existing, empty seat."

    def __init__(self, service: OfficeLayoutService) -> None:
        self._service = service

    @property
    def Input(self) -> type[BaseModel]:
        return SeatOccupantInput

    @property
    def Output(self) -> type[BaseModel]:
        return SeatOccupantOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, SeatOccupantInput)
        try:
            seat = await self._service.seat_occupant(
                seat_id=raw_input.seat_id, occupant_id=raw_input.occupant_id
            )
        except OfficeValidationError as exc:
            return _error(SeatOccupantOutput, exc)
        return SeatOccupantOutput(seat=_seat_summary(seat))


class VacateSeatInput(BaseModel):
    seat_id: str = Field(description="Id of the seat to clear.")


class VacateSeatOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    seat: SeatSummary | None = None


class VacateSeatTool:
    name = "vacate_seat"
    description = "Clear a seat's occupant."

    def __init__(self, service: OfficeLayoutService) -> None:
        self._service = service

    @property
    def Input(self) -> type[BaseModel]:
        return VacateSeatInput

    @property
    def Output(self) -> type[BaseModel]:
        return VacateSeatOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, VacateSeatInput)
        try:
            seat = await self._service.vacate_seat(seat_id=raw_input.seat_id)
        except OfficeValidationError as exc:
            return _error(VacateSeatOutput, exc)
        return VacateSeatOutput(seat=_seat_summary(seat))


def build_office_tools(
    service: OfficeLayoutService, style_loader: FurnitureStyleLoader
) -> list[ToolSpec]:
    """All office tools, ready to append to architect's tool list."""

    return [
        DescribeOfficeTool(service, style_loader),
        FindFurnitureTool(service, style_loader),
        DescribeTilesTool(service, style_loader),
        PaintTilesTool(service),
        PlaceFurnitureTool(service, style_loader),
        MoveFurnitureTool(service, style_loader),
        RemoveFurnitureTool(service),
        CreateZoneTool(service),
        ResizeZoneTool(service),
        RemoveZoneTool(service),
        SeatOccupantTool(service),
        VacateSeatTool(service),
    ]


__all__ = ["build_office_tools"]
