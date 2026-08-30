"""Color representations shared by architect and painter.

Two independent things live here, deliberately not unified:

1. A small, fixed HSB <-> semantic-name palette (`_PALETTE`/`nearest_name`/
   `hsb_for`/`known_names`, `_HEX_PALETTE`/`nearest_hex_name`/`hex_for`) --
   what architect's own `paint_tiles`/`create_zone` still validate
   against (docs/architect-semantic-ir-design.md section 6.3): "make the
   lounge floor warm and beige," not a computed HSB tuple. Unchanged by
   painter's own color model below.
2. General hex <-> HSB conversion (`hex_to_hsb`/`hsb_to_hex`), free of any
   fixed name set -- what painter uses (docs/painter-design.md's color
   model revision): painter has full control over hue/saturation/
   brightness/contrast and reasons about natural-language color requests
   ("blue," "a lighter shade," "#3b5a7a") itself, entirely in its own LLM,
   never constrained to the 12-name palette above. The math mirrors the
   reference webview's own `colorize.ts` (`rgbToHsl`/`hslToHex`) exactly,
   so a given hex/HSB value renders the way painter's reasoning expects.

`{h,s,b,c}` matches Pixel Agents' own HSB-shift color representation
exactly (`webview-ui/src/components/ui/types.ts`'s `ColorValue` shape, as
seen in the bundled layout JSON): `h` 0-360, `s` 0-100, `b`/`c` -100-100,
always in "colorize" mode (an absolute target color, not a relative
adjustment to the sprite's own original pixels) -- see `hsb_to_hex`'s own
docstring for what that means for the derived hex approximation.
"""

from __future__ import annotations

from typing import TypedDict

HUE_MIN, HUE_MAX = 0, 360
SATURATION_MIN, SATURATION_MAX = 0, 100
BRIGHTNESS_MIN, BRIGHTNESS_MAX = -100, 100
CONTRAST_MIN, CONTRAST_MAX = -100, 100


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


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    """`h` 0-360, `s`/`l` 0-1 -- mirrors the reference webview's own
    `colorize.ts` `rgbToHsl` exactly (same algorithm, same rounding
    behavior), so `hex_to_hsb`'s result renders the way it looks."""

    rf, gf, bf = r / 255, g / 255, b / 255
    mx, mn = max(rf, gf, bf), min(rf, gf, bf)
    lightness = (mx + mn) / 2
    if mx == mn:
        return 0.0, 0.0, lightness
    d = mx - mn
    saturation = d / (2 - mx - mn) if lightness > 0.5 else d / (mx + mn)
    if mx == rf:
        hue = ((gf - bf) / d + (6 if gf < bf else 0)) * 60
    elif mx == gf:
        hue = ((bf - rf) / d + 2) * 60
    else:
        hue = ((rf - gf) / d + 4) * 60
    return hue, saturation, lightness


def _hsl_to_hex(h: float, s: float, l: float) -> str:  # noqa: E741 -- mirrors colorize.ts's own h/s/l names
    """`h` 0-360, `s`/`l` 0-1 -> `#RRGGBB`. Mirrors `colorize.ts`'s own
    `hslToHex` exactly."""

    c = (1 - abs(2 * l - 1)) * s
    hp = (h % 360) / 60
    x = c * (1 - abs((hp % 2) - 1))
    if hp < 1:
        r1, g1, b1 = c, x, 0.0
    elif hp < 2:
        r1, g1, b1 = x, c, 0.0
    elif hp < 3:
        r1, g1, b1 = 0.0, c, x
    elif hp < 4:
        r1, g1, b1 = 0.0, x, c
    elif hp < 5:
        r1, g1, b1 = x, 0.0, c
    else:
        r1, g1, b1 = c, 0.0, x
    m = l - c / 2
    r = max(0, min(255, round((r1 + m) * 255)))
    g = max(0, min(255, round((g1 + m) * 255)))
    b = max(0, min(255, round((b1 + m) * 255)))
    return f"#{r:02X}{g:02X}{b:02X}"


def hex_to_hsb(hex_color: str) -> HsbColor:
    """A target hex color, expressed as a colorize-mode `HsbColor` --
    `brightness`/`contrast` chosen so the *flat-fill* rendering of the
    result (a zone, a carpet, or any single-color surface -- see
    `colorize.ts`'s `flatColorizeSprite`) reproduces `hex_color` exactly.
    On a textured sprite (a floor/wall/furniture pattern, via
    `colorizeSprite`/`adjustSprite`), the result approximates `hex_color`
    at the sprite's own neutral tone rather than reproducing it
    pixel-for-pixel -- the same approximation this module's fixed-palette
    `hsb_for`/`hex_for` pair already accepts for named colors, not a new
    gap painter introduces."""

    r, g, b = _hex_to_rgb(hex_color)
    hue, saturation, lightness = _rgb_to_hsl(r, g, b)
    brightness = round((lightness - 0.5) * 200)
    return {
        "h": round(hue) % 360,
        "s": round(saturation * 100),
        "b": max(BRIGHTNESS_MIN, min(BRIGHTNESS_MAX, brightness)),
        "c": 0,
    }


def hsb_to_hex(color: HsbColor) -> str:
    """The flat-fill hex a colorize-mode `HsbColor` renders as -- the
    exact inverse of `hex_to_hsb` for any color it produced, and a
    reasonable, human-readable approximation for any other `HsbColor`
    (e.g. one read back off a tile/furniture item) even though the real
    per-pixel render may vary slightly by sprite texture, same caveat
    `hex_to_hsb`'s own docstring covers.

    `contrast` never changes this function's result, for any hue/
    saturation/brightness combination -- not a bug, verified against the
    reference webview's own `flatColorizeSprite`: lightness starts at
    0.5, contrast stretches *away from* that same 0.5 midpoint (always a
    no-op multiplying a zero deviation), and only brightness shifts it
    afterward. Contrast only visibly matters on a real textured sprite
    render, where the starting lightness is the sprite's own per-pixel
    value, not a fixed 0.5 -- a caller comparing two `describe_*` reads
    that differ only in `contrast` should not expect their `hex` fields
    to differ."""

    lightness = 0.5
    if color["c"]:
        lightness = 0.5 + (lightness - 0.5) * ((100 + color["c"]) / 100)
    lightness = max(0.0, min(1.0, lightness + color["b"] / 200))
    return _hsl_to_hex(color["h"], color["s"] / 100, lightness)


__all__ = [
    "BRIGHTNESS_MAX",
    "BRIGHTNESS_MIN",
    "CONTRAST_MAX",
    "CONTRAST_MIN",
    "HUE_MAX",
    "HUE_MIN",
    "SATURATION_MAX",
    "SATURATION_MIN",
    "HsbColor",
    "hex_for",
    "hex_to_hsb",
    "hsb_for",
    "hsb_to_hex",
    "known_names",
    "nearest_hex_name",
    "nearest_name",
]
