from __future__ import annotations

from ..infrastructure.color_names import (
    hex_for,
    hex_to_hsb,
    hsb_for,
    hsb_to_hex,
    known_names,
    nearest_hex_name,
    nearest_name,
)


def test_every_palette_name_round_trips_to_itself() -> None:
    for name in known_names():
        assert nearest_name(hsb_for(name)) == name


def test_nearest_name_picks_the_closest_hue() -> None:
    # Close to warm_beige's {h:35, s:30, b:15, c:0} but not exact.
    assert nearest_name({"h": 36, "s": 29, "b": 14, "c": 1}) == "warm_beige"


def test_hue_distance_wraps_around_the_color_wheel() -> None:
    # h=358 should be "close" to h=0-ish named colors, not maximally far.
    near_red = {"h": 358, "s": 60, "b": 10, "c": 0}
    assert nearest_name(near_red) == "bright_red"


def test_hsb_for_unknown_name_raises() -> None:
    try:
        hsb_for("not_a_real_color")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for an unknown color name")


def test_every_hex_palette_name_round_trips_to_itself() -> None:
    for name in known_names():
        assert nearest_hex_name(hex_for(name)) == name


def test_nearest_hex_name_picks_the_closest_rgb() -> None:
    assert nearest_hex_name("#c0392a") == "bright_red"


def test_hex_to_hsb_and_back_reproduces_pure_hues_exactly() -> None:
    # Pure, fully-saturated hues have no rounding ambiguity between hue
    # sextants -- an exact round trip, unlike a textured/partial color.
    for hex_color in ("#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#00FFFF", "#FF00FF"):
        assert hsb_to_hex(hex_to_hsb(hex_color)) == hex_color


def test_hex_to_hsb_and_back_is_a_close_approximation_for_an_arbitrary_color() -> None:
    # Not every hex round-trips bit-exact (rounding hue/saturation to
    # integers, per this module's own docstring) -- each channel must
    # still land within 1 of the original.
    original = (0x3B, 0x5A, 0x7A)
    back = hsb_to_hex(hex_to_hsb("#3b5a7a")).lstrip("#")
    reproduced = (int(back[0:2], 16), int(back[2:4], 16), int(back[4:6], 16))
    assert all(abs(a - b) <= 1 for a, b in zip(original, reproduced, strict=True))


def test_hex_to_hsb_white_is_zero_saturation_max_brightness() -> None:
    assert hex_to_hsb("#FFFFFF") == {"h": 0, "s": 0, "b": 100, "c": 0}


def test_hex_to_hsb_black_is_zero_saturation_min_brightness() -> None:
    assert hex_to_hsb("#000000") == {"h": 0, "s": 0, "b": -100, "c": 0}


def test_hex_to_hsb_neutral_gray_is_the_zero_point() -> None:
    assert hex_to_hsb("#808080") == {"h": 0, "s": 0, "b": 0, "c": 0}


def test_hex_to_hsb_accepts_a_leading_hash_or_not() -> None:
    assert hex_to_hsb("#3b5a7a") == hex_to_hsb("3b5a7a")


def test_hsb_to_hex_neutral_color_is_mid_gray() -> None:
    assert hsb_to_hex({"h": 0, "s": 0, "b": 0, "c": 0}) == "#808080"


def test_hsb_to_hex_contrast_is_always_inert() -> None:
    """Not a bug -- verified against the reference webview's own
    `flatColorizeSprite`, which this function mirrors exactly: lightness
    starts at 0.5, contrast stretches *away from* that same 0.5 midpoint
    (`(0.5 - 0.5) * factor` is always 0 regardless of `c`), and only
    *then* does brightness shift it -- so contrast never changes this
    function's result, for any combination of hue/saturation/brightness.
    Contrast only visibly matters on a real textured sprite render
    (`colorizeSprite`, non-flat, real per-pixel luminance varying above/
    below 0.5) -- this hex derivation is a flat preview/round-trip
    convenience, not a sprite renderer, and callers reading `contrast`
    off `describe_tile_colors`/`describe_furniture_colors` should not
    expect it to move the paired `hex` field."""

    for b in (-40, 0, 40):
        for s in (0, 50, 100):
            base = hsb_to_hex({"h": 0, "s": s, "b": b, "c": 0})
            contrasted = hsb_to_hex({"h": 0, "s": s, "b": b, "c": 80})
            assert base == contrasted, (b, s)
