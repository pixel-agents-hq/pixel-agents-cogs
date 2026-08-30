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
make one (docs/painter-design.md §7.4).

**Color model**: there is no fixed palette here -- painter has full
control over hue/saturation/brightness/contrast (or a hex shorthand),
and reasons about natural-language requests ("blue," "a lighter shade,"
"#3b5a7a") entirely in its own LLM. `ColorSpec` accepts exactly one of
`hex` or `hue`+`saturation`, plus an optional `brightness`/`contrast`
adjustment on top -- the same "give exactly one of these two shapes"
convention `RecolorTilesInput`'s own width/height-vs-end_col/end_row
already uses. `describe_tile_colors`/`describe_furniture_colors` report
the *exact* current color in the same terms (hex + hue/saturation/
brightness/contrast) so painter's LLM can reason about adjustments
("read the current brightness, then ask for a higher one") rather than
guessing blind."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from pixelagents.domain import FurnitureItem, FurnitureKind, GridPosition, GridRect, TileCell
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
    hex_to_hsb,
    hsb_for,
    hsb_to_hex,
    nearest_name,
)

from ..application.painter_layout_service import PainterLayoutService, PainterValidationError
from .base import ToolSpec

_KIND_VALUES: tuple[str, ...] = tuple(kind.value for kind in FurnitureKind)


def _error(output_cls: type[BaseModel], exc: PainterValidationError) -> BaseModel:
    """Every Output here carries `status`/`message` fields -- build an
    error instance without repeating that boilerplate at each call site,
    same convention architect's own office_tools.py `_error` uses."""

    return output_cls(status="error", message=exc.reason)


class ColorSpec(BaseModel):
    """A target color -- give exactly one of `hex` or `hue`+`saturation`,
    never both and not neither. `brightness`/`contrast` apply either way,
    as an adjustment on top of whichever base color that resolves to (0/0
    means "exactly that base color, no adjustment"). To make an existing
    tile/furniture item's *current* color lighter or darker without
    changing its hue, read it first (describe_tile_colors/
    describe_furniture_colors reports hue/saturation/brightness/contrast
    directly) and pass its hue+saturation back with an adjusted
    brightness."""

    hex: str | None = Field(
        default=None,
        description=(
            "Target color as a 6-digit hex RGB string, e.g. '#3b5a7a' or '3b5a7a'. Good for a "
            "specific named/described color ('gold', 'forest green', '#e0c341'). Alternative to "
            "hue+saturation -- give exactly one of hex OR hue+saturation."
        ),
    )
    hue: int | None = Field(
        default=None,
        ge=HUE_MIN,
        le=HUE_MAX,
        description=(
            "Hue, 0-360 (0/360=red, 60=yellow, 120=green, 180=cyan, 240=blue, 300=magenta/purple, "
            "any value in between blends smoothly). Give together with saturation, as an "
            "alternative to hex -- useful for reusing an exact hue/saturation read back from "
            "describe_tile_colors/describe_furniture_colors."
        ),
    )
    saturation: int | None = Field(
        default=None,
        ge=SATURATION_MIN,
        le=SATURATION_MAX,
        description=(
            "Saturation, 0 (gray, no color) to 100 (fully saturated/vivid). Give together with "
            "hue, as an alternative to hex."
        ),
    )
    brightness: int = Field(
        default=0,
        ge=BRIGHTNESS_MIN,
        le=BRIGHTNESS_MAX,
        description=(
            "Brightness adjustment from the base color (hex or hue+saturation above), -100 "
            "(much darker) to 100 (much brighter). 0 means no adjustment. This is what 'a "
            "lighter shade' or 'darker' means -- increase or decrease this, not the hue."
        ),
    )
    contrast: int = Field(
        default=0,
        ge=CONTRAST_MIN,
        le=CONTRAST_MAX,
        description=(
            "Contrast adjustment from the base color, -100 to 100. 0 means no adjustment. Usually "
            "leave at 0 unless asked for a flatter (negative) or more vivid/punchy (positive) look."
        ),
    )


def _resolve_color(spec: ColorSpec) -> HsbColor | str:
    has_hex = spec.hex is not None
    has_hue_saturation = spec.hue is not None and spec.saturation is not None
    if has_hex == has_hue_saturation:
        return "give exactly one of hex, or hue and saturation together, not both and not neither"
    if has_hex:
        assert spec.hex is not None
        cleaned = spec.hex.lstrip("#")
        if len(cleaned) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in cleaned):
            return f"{spec.hex!r} is not a valid 6-digit hex color, e.g. '#3b5a7a'"
        base = hex_to_hsb(cleaned)
    else:
        assert spec.hue is not None and spec.saturation is not None
        base = {"h": spec.hue, "s": spec.saturation, "b": 0, "c": 0}
    return {
        "h": base["h"],
        "s": base["s"],
        "b": max(BRIGHTNESS_MIN, min(BRIGHTNESS_MAX, base["b"] + spec.brightness)),
        "c": max(CONTRAST_MIN, min(CONTRAST_MAX, base["c"] + spec.contrast)),
    }


class ColorSummary(BaseModel):
    """The exact current color, in the same terms `ColorSpec` accepts --
    read this before asking for "lighter"/"darker"/a hue-preserving
    adjustment. `closest_named_color` is informational only (the nearest
    of architect's own small fixed palette, purely for a human-readable
    label) -- never treat it as the actual stored color. `hex` never
    moves in response to `contrast` alone (see `hsb_to_hex`'s own
    docstring) -- it's a flat preview of hue/saturation/brightness, not
    the actual in-game render, which does respond to contrast on a real
    tile/wall/furniture texture."""

    hex: str
    hue: int
    saturation: int
    brightness: int
    contrast: int
    closest_named_color: str


def _color_summary(
    color_name: str | None, raw_color: tuple[int, int, int, int] | None
) -> ColorSummary | None:
    if raw_color is not None:
        hsb: HsbColor = {"h": raw_color[0], "s": raw_color[1], "b": raw_color[2], "c": raw_color[3]}
    elif color_name is not None:
        hsb = hsb_for(color_name)
    else:
        return None
    return ColorSummary(
        hex=hsb_to_hex(hsb),
        hue=hsb["h"],
        saturation=hsb["s"],
        brightness=hsb["b"],
        contrast=hsb["c"],
        closest_named_color=nearest_name(hsb),
    )


class TileColorSummary(BaseModel):
    col: int
    row: int
    kind: str
    color: ColorSummary | None = None


class FurnitureColorSummary(BaseModel):
    id: str
    kind: str
    style: str
    label: str | None = None
    color: ColorSummary | None = None


def _tile_summary(position: GridPosition, cell: TileCell) -> TileColorSummary:
    return TileColorSummary(
        col=position.col,
        row=position.row,
        kind=cell.kind.value,
        color=_color_summary(cell.color, cell.raw_color),
    )


def _furniture_summary(item: FurnitureItem) -> FurnitureColorSummary:
    return FurnitureColorSummary(
        id=item.id,
        kind=item.kind.value,
        style=item.style,
        label=item.label,
        color=_color_summary(item.color, item.raw_color),
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
        "structured, no-vision replacement for looking at a picture of the office. Each tile's "
        "color is reported as hex plus hue/saturation/brightness/contrast, not a name -- read "
        "this before adjusting an existing color lighter/darker. Use consult_architect first to "
        "learn a region's kind/position if you don't already know it."
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
        "Each item's color is reported as hex plus hue/saturation/brightness/contrast, not a "
        "name -- read this before adjusting an existing color lighter/darker. Use "
        "consult_architect first to learn which furniture exists and where if you don't already "
        "know its id/style."
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
    color: ColorSpec = Field(description="The target color -- see ColorSpec's own field docs.")


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
        "Name the region either with width/height or with end_col/end_row -- give exactly one. "
        "The color is exact hex or hue/saturation/brightness/contrast, not a name from a fixed "
        "list -- reason about what 'blue', 'gold', 'a lighter shade', etc. actually mean in "
        "those terms yourself."
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
        color = _resolve_color(raw_input.color)
        if isinstance(color, str):
            return RecolorTilesOutput(status="error", message=color)
        try:
            await self._service.recolor_tiles(area=area, color=color)
        except PainterValidationError as exc:
            return _error(RecolorTilesOutput, exc)
        return RecolorTilesOutput()


class RecolorFurnitureInput(BaseModel):
    furniture_id: str = Field(description="The id of the furniture item to recolor.")
    color: ColorSpec = Field(description="The target color -- see ColorSpec's own field docs.")


class RecolorFurnitureOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None


class RecolorFurnitureTool:
    name = "recolor_furniture"
    description = (
        "Recolor a single furniture item by id, without moving or replacing it. The color is "
        "exact hex or hue/saturation/brightness/contrast, not a name from a fixed list."
    )

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
        color = _resolve_color(raw_input.color)
        if isinstance(color, str):
            return RecolorFurnitureOutput(status="error", message=color)
        try:
            await self._service.recolor_furniture(furniture_id=raw_input.furniture_id, color=color)
        except PainterValidationError as exc:
            return _error(RecolorFurnitureOutput, exc)
        return RecolorFurnitureOutput()


class RecolorFurnitureByStyleInput(BaseModel):
    kind: Literal[_KIND_VALUES] = Field(description="Furniture kind, e.g. 'seating'.")  # type: ignore[valid-type]
    style: str = Field(description="The exact style id, e.g. 'wooden_chair'.")
    color: ColorSpec = Field(
        description="The target color to apply to every matching item -- see ColorSpec's own field docs."
    )


class RecolorFurnitureByStyleOutput(BaseModel):
    status: Literal["ok", "error"] = "ok"
    message: str | None = None
    recolored_count: int = 0


class RecolorFurnitureByStyleTool:
    name = "recolor_furniture_by_style"
    description = (
        "Recolor every placed furniture item of a given kind+style at once -- e.g. every "
        "wooden chair. recolored_count is 0, not an error, when nothing currently matches. The "
        "color is exact hex or hue/saturation/brightness/contrast, not a name from a fixed list."
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
        color = _resolve_color(raw_input.color)
        if isinstance(color, str):
            return RecolorFurnitureByStyleOutput(status="error", message=color)
        try:
            count = await self._service.recolor_furniture_by_style(
                kind=FurnitureKind(raw_input.kind), style=raw_input.style, color=color
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
    "ColorSpec",
    "ColorSummary",
    "DescribeFurnitureColorsTool",
    "DescribeTileColorsTool",
    "RecolorFurnitureByStyleTool",
    "RecolorFurnitureTool",
    "RecolorTilesTool",
    "build_painter_tools",
]
