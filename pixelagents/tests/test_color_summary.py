from __future__ import annotations

from ..infrastructure.color_names import hsb_for, hsb_to_hex
from ..infrastructure.color_summary import color_summary


def test_raw_color_takes_precedence_over_semantic_name() -> None:
    # cool_blue's canonical HSB differs from this raw tuple -- the exact
    # raw value must win, not the (stale) semantic name alongside it.
    summary = color_summary("cool_blue", (10, 20, 30, 40))
    assert summary is not None
    assert (summary.hue, summary.saturation, summary.brightness, summary.contrast) == (
        10,
        20,
        30,
        40,
    )


def test_falls_back_to_semantic_name_when_raw_color_is_none() -> None:
    hsb = hsb_for("warm_beige")
    summary = color_summary("warm_beige", None)
    assert summary is not None
    assert (summary.hue, summary.saturation, summary.brightness, summary.contrast) == (
        hsb["h"],
        hsb["s"],
        hsb["b"],
        hsb["c"],
    )


def test_returns_none_when_both_are_none() -> None:
    assert color_summary(None, None) is None


def test_hex_matches_hsb_to_hex_for_the_same_hsb() -> None:
    raw = (200, 40, -10, 5)
    summary = color_summary(None, raw)
    assert summary is not None
    hsb = {"h": raw[0], "s": raw[1], "b": raw[2], "c": raw[3]}
    assert summary.hex == hsb_to_hex(hsb)


def test_closest_named_color_is_computed_not_the_original_name() -> None:
    # An off-palette raw color still gets a best-effort closest_named_color
    # label, distinct from whatever (possibly stale) name was passed in.
    summary = color_summary("cool_blue", (0, 60, 10, 0))
    assert summary is not None
    assert summary.closest_named_color == "bright_red"
