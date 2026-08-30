"""LLM tools for painter's color-only mutation surface, per
docs/painter-design.md §7.3/§7.4.

Every tool is a thin wrapper: pydantic `Input` -> a `PainterLayoutService`
call -> pydantic `Output`, translating `PainterValidationError` into
`Output.status="error"` + `message` rather than letting it propagate --
same "handler does the actual work and must not raise for expected
failure modes" convention architect's own `office_tools.py` documents.

No `kind`/`material` parameter appears on any Input model here, and there
is no wall<->floor conversion path -- painter's tool surface is
physically incapable of any structural change, not just instructed not to
make one (docs/painter-design.md §7.4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from pixelagents.domain import FurnitureItem, FurnitureKind, GridPosition, GridRect, TileCell

from ..application.painter_layout_service import PainterLayoutService, PainterValidationError
from .base import ToolSpec

_KIND_VALUES: tuple[str, ...] = tuple(kind.value for kind in FurnitureKind)


def _error(output_cls: type[BaseModel], exc: PainterValidationError) -> BaseModel:
    """Every Output here carries `status`/`message` fields -- build an
    error instance without repeating that boilerplate at each call site,
    same convention architect's own office_tools.py `_error` uses."""

    return output_cls(status="error", message=exc.reason)


class TileColorSummary(BaseModel):
    col: int
    row: int
    kind: str
    color: str | None = None


class FurnitureColorSummary(BaseModel):
    id: str
    kind: str
    style: str
    label: str | None = None
    color: str | None = None


def _tile_summary(position: GridPosition, cell: TileCell) -> TileColorSummary:
    return TileColorSummary(
        col=position.col, row=position.row, kind=cell.kind.value, color=cell.color
    )


def _furniture_summary(item: FurnitureItem) -> FurnitureColorSummary:
    return FurnitureColorSummary(
        id=item.id, kind=item.kind.value, style=item.style, label=item.label, color=item.color
    )


class DescribeTileColorsInput(BaseModel):
    col: int = Field(
        description="Top-left column of the region. 0-based -- 0 is the office's westmost column."
    )
    row: int = Field(
        description="Top-left row of the region. 0-based -- 0 is the office's northmost row."
    )
    width: int = Field(
        description=(
            "Region width in tiles. The region covers columns col..col+width-1 inclusive -- "
            "col+width must not exceed the office's width, which consult_architect can report."
        )
    )
    height: int = Field(
        description=(
            "Region height in tiles. The region covers rows row..row+height-1 inclusive -- "
            "row+height must not exceed the office's height, which consult_architect can report."
        )
    )


class DescribeTileColorsOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    tiles: list[TileColorSummary] = Field(default_factory=list)


class DescribeTileColorsTool:
    name = "describe_tile_colors"
    description = (
        "Show the current color of every floor and wall tile in a bounded region -- the "
        "structured, no-vision replacement for looking at a picture of the office. Use "
        "consult_architect first to learn a region's kind/position if you don't already know it."
    )

    def __init__(self, service: PainterLayoutService) -> None:
        self._service = service

    @property
    def Input(self) -> type[BaseModel]:
        return DescribeTileColorsInput

    @property
    def Output(self) -> type[BaseModel]:
        return DescribeTileColorsOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, DescribeTileColorsInput)
        area = GridRect(
            GridPosition(raw_input.col, raw_input.row), raw_input.width, raw_input.height
        )
        try:
            cells = await self._service.describe_tile_colors(area=area)
        except PainterValidationError as exc:
            return _error(DescribeTileColorsOutput, exc)
        return DescribeTileColorsOutput(
            tiles=[
                _tile_summary(position, cell)
                for position, cell in zip(area.positions(), cells, strict=True)
            ]
        )


class DescribeFurnitureColorsInput(BaseModel):
    kind: Literal[_KIND_VALUES] | None = Field(  # type: ignore[valid-type]
        default=None,
        description="Only furniture of this kind, e.g. 'seating'. Omit for every kind.",
    )
    style: str | None = Field(
        default=None, description="Only furniture of this exact style id. Omit for every style."
    )


class DescribeFurnitureColorsOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    furniture: list[FurnitureColorSummary] = Field(default_factory=list)


class DescribeFurnitureColorsTool:
    name = "describe_furniture_colors"
    description = (
        "Show the current color of placed furniture, optionally filtered by kind and/or style. "
        "Use consult_architect first to learn which furniture exists and where if you don't "
        "already know its id/style."
    )

    def __init__(self, service: PainterLayoutService) -> None:
        self._service = service

    @property
    def Input(self) -> type[BaseModel]:
        return DescribeFurnitureColorsInput

    @property
    def Output(self) -> type[BaseModel]:
        return DescribeFurnitureColorsOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, DescribeFurnitureColorsInput)
        kind = FurnitureKind(raw_input.kind) if raw_input.kind is not None else None
        items = await self._service.describe_furniture_colors(kind=kind, style=raw_input.style)
        return DescribeFurnitureColorsOutput(furniture=[_furniture_summary(item) for item in items])


class RecolorTilesInput(BaseModel):
    col: int = Field(
        description=(
            "Top-left column of the region to recolor. 0-based -- 0 is the office's westmost "
            "column."
        )
    )
    row: int = Field(
        description=(
            "Top-left row of the region to recolor. 0-based -- 0 is the office's northmost row."
        )
    )
    width: int | None = Field(
        default=None,
        description=(
            "Region width in tiles -- a COUNT, not a second column. Give exactly one of "
            "width/height OR end_col/end_row, never both -- end_col/end_row avoids off-by-one "
            "arithmetic entirely: col=5,end_col=10 and col=5,width=6 describe the identical "
            "6-column region."
        ),
    )
    height: int | None = Field(
        default=None,
        description="Region height in tiles -- a COUNT. See width's description.",
    )
    end_col: int | None = Field(
        default=None,
        description=(
            "Rightmost column to recolor, INCLUSIVE. Alternative to width; give exactly one of "
            "width/height OR end_col/end_row, never both. Must be >= col."
        ),
    )
    end_row: int | None = Field(
        default=None,
        description=(
            "Bottommost row to recolor, INCLUSIVE. Alternative to height; give exactly one of "
            "width/height OR end_col/end_row, never both. Must be >= row."
        ),
    )
    color: str = Field(description="Semantic color name to apply to every tile in the region.")


class RecolorTilesOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None


def _resolve_recolor_region(raw_input: RecolorTilesInput) -> GridRect | str:
    """Same two mutually-exclusive region shapes architect's own
    `paint_tiles` accepts (`_resolve_paint_region`) -- duplicated rather
    than imported, since architect's write tools are deliberately not
    exposed to painter at all (docs/painter-design.md §6.3)."""

    by_count = raw_input.width is not None and raw_input.height is not None
    by_corner = raw_input.end_col is not None and raw_input.end_row is not None
    if by_count == by_corner:
        return "give exactly one of width/height or end_col/end_row, not both and not neither"
    if by_count:
        assert raw_input.width is not None and raw_input.height is not None
        return GridRect(
            GridPosition(raw_input.col, raw_input.row), raw_input.width, raw_input.height
        )
    assert raw_input.end_col is not None and raw_input.end_row is not None
    if raw_input.end_col < raw_input.col or raw_input.end_row < raw_input.row:
        return (
            f"end_col ({raw_input.end_col}) and end_row ({raw_input.end_row}) must be >= "
            f"col ({raw_input.col}) and row ({raw_input.row}) respectively"
        )
    return GridRect(
        GridPosition(raw_input.col, raw_input.row),
        raw_input.end_col - raw_input.col + 1,
        raw_input.end_row - raw_input.row + 1,
    )


class RecolorTilesTool:
    name = "recolor_tiles"
    description = (
        "Recolor every floor and wall tile in a rectangular region, without changing anything "
        "else about them -- floor tiles keep their pattern, walls stay walls, nothing is added, "
        "removed, or moved. Fails if the region includes a void tile (nothing to color there). "
        "Name the region either with width/height or with end_col/end_row -- give exactly one."
    )

    def __init__(self, service: PainterLayoutService) -> None:
        self._service = service

    @property
    def Input(self) -> type[BaseModel]:
        return RecolorTilesInput

    @property
    def Output(self) -> type[BaseModel]:
        return RecolorTilesOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, RecolorTilesInput)
        area = _resolve_recolor_region(raw_input)
        if isinstance(area, str):
            return RecolorTilesOutput(status="error", message=area)
        try:
            await self._service.recolor_tiles(area=area, color=raw_input.color)
        except PainterValidationError as exc:
            return _error(RecolorTilesOutput, exc)
        return RecolorTilesOutput()


class RecolorFurnitureInput(BaseModel):
    furniture_id: str = Field(description="The id of the furniture item to recolor.")
    color: str = Field(description="Semantic color name to apply.")


class RecolorFurnitureOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None


class RecolorFurnitureTool:
    name = "recolor_furniture"
    description = "Recolor a single furniture item by id, without moving or replacing it."

    def __init__(self, service: PainterLayoutService) -> None:
        self._service = service

    @property
    def Input(self) -> type[BaseModel]:
        return RecolorFurnitureInput

    @property
    def Output(self) -> type[BaseModel]:
        return RecolorFurnitureOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, RecolorFurnitureInput)
        try:
            await self._service.recolor_furniture(
                furniture_id=raw_input.furniture_id, color=raw_input.color
            )
        except PainterValidationError as exc:
            return _error(RecolorFurnitureOutput, exc)
        return RecolorFurnitureOutput()


class RecolorFurnitureByStyleInput(BaseModel):
    kind: Literal[_KIND_VALUES] = Field(description="Furniture kind, e.g. 'seating'.")  # type: ignore[valid-type]
    style: str = Field(description="The exact style id, e.g. 'wooden_chair'.")
    color: str = Field(description="Semantic color name to apply to every matching item.")


class RecolorFurnitureByStyleOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    recolored_count: int = 0


class RecolorFurnitureByStyleTool:
    name = "recolor_furniture_by_style"
    description = (
        "Recolor every placed furniture item of a given kind+style at once -- e.g. every "
        "wooden chair. recolored_count is 0, not an error, when nothing currently matches."
    )

    def __init__(self, service: PainterLayoutService) -> None:
        self._service = service

    @property
    def Input(self) -> type[BaseModel]:
        return RecolorFurnitureByStyleInput

    @property
    def Output(self) -> type[BaseModel]:
        return RecolorFurnitureByStyleOutput

    async def handler(self, raw_input: BaseModel) -> BaseModel:
        assert isinstance(raw_input, RecolorFurnitureByStyleInput)
        try:
            count = await self._service.recolor_furniture_by_style(
                kind=FurnitureKind(raw_input.kind), style=raw_input.style, color=raw_input.color
            )
        except PainterValidationError as exc:
            return _error(RecolorFurnitureByStyleOutput, exc)
        return RecolorFurnitureByStyleOutput(recolored_count=count)


def build_painter_tools(service: PainterLayoutService) -> list[ToolSpec]:
    return [
        DescribeTileColorsTool(service),
        DescribeFurnitureColorsTool(service),
        RecolorTilesTool(service),
        RecolorFurnitureTool(service),
        RecolorFurnitureByStyleTool(service),
    ]


__all__ = [
    "DescribeFurnitureColorsTool",
    "DescribeTileColorsTool",
    "RecolorFurnitureByStyleTool",
    "RecolorFurnitureTool",
    "RecolorTilesTool",
    "build_painter_tools",
]
