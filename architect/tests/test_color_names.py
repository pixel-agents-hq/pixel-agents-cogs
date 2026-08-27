from __future__ import annotations

from ..infrastructure.color_names import (
    hex_for,
    hsb_for,
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
