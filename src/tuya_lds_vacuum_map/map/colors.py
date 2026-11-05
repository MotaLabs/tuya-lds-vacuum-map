import colorsys
import random
import re
from collections.abc import Iterable

Color = tuple[int, int, int]

COLOR_PATTERN = "#[0-9a-fA-F]{6}"
HUE_CANDIDATES = 24
LIGHTNESS = (0.5, 0.65)
SATURATION = (0.45, 0.75)

_HEX = re.compile(f"^{COLOR_PATTERN}$")


def parse_color(text: str) -> Color:
    text = text.strip()
    if not _HEX.match(text):
        raise ValueError(f"not a #rrggbb color: {text!r}")
    value = int(text[1:], 16)
    return value >> 16, (value >> 8) & 0xFF, value & 0xFF


def format_color(color: Color) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)


def random_color(used: Iterable[Color], rng: random.Random) -> Color:
    hues = [colorsys.rgb_to_hls(*(channel / 255 for channel in color))[0] for color in used]

    def distance(hue: float) -> float:
        return min((min(abs(hue - other), 1 - abs(hue - other)) for other in hues), default=1.0)

    candidates = [rng.random() for _ in range(HUE_CANDIDATES)]
    farthest = max(map(distance, candidates))
    hue = rng.choice([candidate for candidate in candidates if distance(candidate) >= farthest / 2])
    red, green, blue = colorsys.hls_to_rgb(hue, rng.uniform(*LIGHTNESS), rng.uniform(*SATURATION))
    return round(red * 255), round(green * 255), round(blue * 255)
