"""A shared, structured color-read shape for LLM tool output -- the exact
current color of a tile/wall/furniture item, in the same terms a
`ColorSpec`-shaped write input accepts (hex, or hue/saturation/brightness/
contrast). Both architect's and painter's `tools/*.py` describe/read tools
build this from the identical `TileCell.color`/`.raw_color` and
`FurnitureItem.color`/`.raw_color` fields the shared `Office` aggregate
already carries -- one implementation, not duplicated per cog.

Deliberate second reason pydantic lives in `pixelagents` alongside
`application/office_state.py`/`contracts/layout.py` (see
`pixelagents/tests/test_architecture.py`'s
`test_pydantic_is_confined_to_the_office_schema_boundary`, whose allowlist
this file was added to): not wire-format validation, but a genuinely
shared LLM-tool-facing schema type both cogs' tool layers need
byte-for-byte identical."""

from __future__ import annotations

from pydantic import BaseModel

from .color_names import HsbColor, hsb_for, hsb_to_hex, nearest_name


class ColorSummary(BaseModel):
    """The exact current color, in the same terms a `ColorSpec`-shaped
    write input accepts -- read this before asking for "lighter"/
    "darker"/a hue-preserving adjustment. `closest_named_color` is
    informational only (the nearest of the fixed 12-name palette
    `pixelagents.infrastructure.color_names` defines, purely for a
    human-readable label) -- never treat it as the actual stored color.
    `hex` never moves in response to `contrast` alone (see `hsb_to_hex`'s
    own docstring) -- it's a flat preview of hue/saturation/brightness,
    not the actual in-game render, which does respond to contrast on a
    real tile/wall/furniture texture."""

    hex: str
    hue: int
    saturation: int
    brightness: int
    contrast: int
    closest_named_color: str


def color_summary(
    color_name: str | None, raw_color: tuple[int, int, int, int] | None
) -> ColorSummary | None:
    """Build a `ColorSummary` from a `TileCell`/`FurnitureItem`'s own
    `color`/`raw_color` pair -- `raw_color` (the exact HSB this cell/item
    last decoded or was written with) takes precedence when present;
    `color_name` is the fallback for a never-recolored cell that only has
    the semantic name Pixel Agents' own JSON shipped with. Returns None
    when neither is present (e.g. a void tile)."""

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


__all__ = ["ColorSummary", "color_summary"]
