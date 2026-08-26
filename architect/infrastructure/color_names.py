"""A small, fixed HSB <-> semantic color name palette.

Deliberately coarse (docs/architect-semantic-ir-design.md section 6.3):
an LLM should say "make the lounge floor warm and beige," not compute an
HSB tuple. `{h,s,b,c}` here matches Pixel Agents' own HSB-shift color
representation exactly (`webview-ui/src/components/ui/types.py`'s
`ColorValue` shape, as seen in the bundled layout JSON) -- this module
only maps that representation to/from a closed set of names, it never
invents a new color model.
"""

from __future__ import annotations

from typing import TypedDict


class HsbColor(TypedDict):
    h: int
    s: int
    b: int
    c: int


# Each name's canonical HSB value. Chosen to span the hue wheel at a few
# brightness/saturation combinations actually seen in the bundled default
# layout (docs/architect-semantic-ir-design.md section 1) rather than an
# exhaustive palette -- nearest-match (`nearest_name`) still degrades
# gracefully for any HSB value not exactly one of these.
_PALETTE: dict[str, HsbColor] = {
    "warm_beige": {"h": 35, "s": 30, "b": 15, "c": 0},
    "warm_brown": {"h": 25, "s": 45, "b": 5, "c": 10},
    "warm_wood": {"h": 25, "s": 48, "b": -43, "c": -88},
    "cool_blue": {"h": 214, "s": 30, "b": -100, "c": -55},
    "slate_gray": {"h": 209, "s": 39, "b": -25, "c": -80},
    "cool_gray": {"h": 209, "s": 0, "b": -16, "c": -8},
    "forest_green": {"h": 120, "s": 45, "b": 0, "c": 0},
    "royal_purple": {"h": 280, "s": 40, "b": -5, "c": 0},
    "sandy_tan": {"h": 35, "s": 25, "b": 10, "c": 0},
    "bright_red": {"h": 0, "s": 60, "b": 10, "c": 0},
    "sunny_yellow": {"h": 50, "s": 55, "b": 20, "c": 0},
    "neutral": {"h": 0, "s": 0, "b": 0, "c": 0},
}


def _distance(a: HsbColor, b: HsbColor) -> float:
    # Hue is circular (0 and 360 are the same color) -- take the shorter
    # arc rather than the raw difference, or every name near hue 0 would
    # look maximally far from every name near hue 359.
    hue_diff = min(abs(a["h"] - b["h"]), 360 - abs(a["h"] - b["h"]))
    squared = hue_diff**2 + (a["s"] - b["s"]) ** 2 + (a["b"] - b["b"]) ** 2 + (a["c"] - b["c"]) ** 2
    return float(squared**0.5)


def nearest_name(color: HsbColor) -> str:
    """The palette name whose HSB value is closest to `color`."""

    return min(_PALETTE, key=lambda name: _distance(_PALETTE[name], color))


def hsb_for(name: str) -> HsbColor:
    """The canonical HSB value for a palette name.

    Raises `KeyError` for a name outside the fixed palette -- callers
    validate `name` against `known_names()` before calling this, the same
    way `pixel_agents_adapter.py` validates furniture styles against the
    generated manifest.
    """

    return _PALETTE[name]


def known_names() -> frozenset[str]:
    return frozenset(_PALETTE)


# Pixel Agents' `AreaDefinition.color` (zones/areas) is a plain hex RGB
# string ("#ff6b6b"), a different representation from the HSB tile-shift
# above -- same closed set of names, a separate table because the two
# color spaces aren't directly comparable without a lossy conversion.
_HEX_PALETTE: dict[str, str] = {
    "warm_beige": "#d9b382",
    "warm_brown": "#6f4a2c",
    "warm_wood": "#8f6439",
    "cool_blue": "#3b5a7a",
    "slate_gray": "#5c6b7a",
    "cool_gray": "#8a8f94",
    "forest_green": "#2e6b3e",
    "royal_purple": "#6b3fa0",
    "sandy_tan": "#d8c39a",
    "bright_red": "#c0392b",
    "sunny_yellow": "#e0c341",
    "neutral": "#808080",
}


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    raw = hex_color.lstrip("#")
    return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))


def nearest_hex_name(hex_color: str) -> str:
    """The palette name whose hex RGB value is closest to `hex_color`."""

    target = _hex_to_rgb(hex_color)

    def distance(name: str) -> float:
        rgb = _hex_to_rgb(_HEX_PALETTE[name])
        squared = sum((a - b) ** 2 for a, b in zip(rgb, target, strict=True))
        return float(squared**0.5)

    return min(_HEX_PALETTE, key=distance)


def hex_for(name: str) -> str:
    """The canonical hex RGB value for a palette name. Raises `KeyError`
    for a name outside the fixed palette, same contract as `hsb_for`."""

    return _HEX_PALETTE[name]


__all__ = [
    "HsbColor",
    "hex_for",
    "hsb_for",
    "known_names",
    "nearest_hex_name",
    "nearest_name",
]
