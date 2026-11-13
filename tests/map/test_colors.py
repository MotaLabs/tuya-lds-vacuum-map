import colorsys
import contextlib
import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from support.fakes import ScriptedRandom
from tuya_lds_vacuum_map.map.colors import HUE_CANDIDATES, format_color, parse_color, random_color


def hue(color: tuple[int, int, int]) -> float:
    return colorsys.rgb_to_hls(*(channel / 255 for channel in color))[0]


def test_colors_round_trip_as_hex():
    assert parse_color("#1a2B3c") == (0x1A, 0x2B, 0x3C)
    assert parse_color(" #ffffff ") == (255, 255, 255)
    assert format_color((0x1A, 0x2B, 0x3C)) == "#1a2b3c"


@pytest.mark.parametrize("text", ["1a2b3c", "#1a2b3", "#1a2b3cd", "#gggggg", "", "red"])
def test_anything_else_is_not_a_color(text):
    with pytest.raises(ValueError, match="#rrggbb"):
        parse_color(text)


def test_random_colors_are_mid_tones():
    rng = random.Random(1)
    for _ in range(50):
        _, lightness, saturation = colorsys.rgb_to_hls(*(channel / 255 for channel in random_color([], rng)))
        assert 0.49 <= lightness <= 0.66
        assert 0.44 <= saturation <= 0.76


def test_a_new_color_keeps_away_from_the_used_hues():
    used = [(230, 40, 40), (40, 230, 40), (40, 40, 230)]
    for seed in range(20):
        new = hue(random_color(used, random.Random(seed)))
        assert min(min(abs(new - hue(color)), 1 - abs(new - hue(color))) for color in used) > 0.06


def test_repeated_draws_spread_over_a_wide_free_gap():
    used = [
        tuple(round(channel * 255) for channel in colorsys.hls_to_rgb(h, 0.5, 0.7)) for h in (0, 0.1, 0.2, 0.3, 0.4)
    ]
    hues = {round(hue(random_color(used, random.Random(seed))), 1) for seed in range(30)}
    assert hues <= {0.5, 0.6, 0.7, 0.8, 0.9}
    assert len(hues) >= 3


@pytest.mark.parametrize(("drawn", "color"), [(0.0, (185, 70, 70)), (1 / 3, (70, 185, 70)), (2 / 3, (70, 70, 185))])
def test_the_color_is_the_drawn_hue_at_the_lowest_lightness_and_saturation(drawn, color):
    assert random_color([], ScriptedRandom([drawn] * HUE_CANDIDATES)) == color


def test_a_hue_half_as_far_as_the_farthest_is_still_a_candidate():
    draws = [0.5] + [0.25] * (HUE_CANDIDATES - 1)
    rng = ScriptedRandom(draws)
    random_color([(255, 0, 0)], rng)
    assert rng.choices == [draws]


def test_the_same_seed_gives_the_same_color():
    assert random_color([], random.Random(7)) == random_color([], random.Random(7))


channels = st.tuples(st.integers(0, 255), st.integers(0, 255), st.integers(0, 255))


@given(channels)
def test_any_color_round_trips(color):
    assert parse_color(format_color(color)) == color


@given(st.text())
def test_any_text_is_a_color_or_a_value_error(text):
    with contextlib.suppress(ValueError):
        assert format_color(parse_color(text)) == text.strip().lower()


@given(st.lists(channels, max_size=16), st.integers())
def test_any_draw_is_a_valid_mid_tone(used, seed):
    color = random_color(used, random.Random(seed))
    assert all(0 <= channel <= 255 for channel in color)
    _, lightness, saturation = colorsys.rgb_to_hls(*(channel / 255 for channel in color))
    assert 0.49 <= lightness <= 0.66
    assert 0.44 <= saturation <= 0.76
