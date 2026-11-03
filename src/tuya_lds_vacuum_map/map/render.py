import dataclasses
import io
import math
import os
from dataclasses import dataclass, field
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from ..lds.messages import Area, Room
from ..profiles import CellCodes
from .grid import GridMap, Point

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)

Color = tuple[int, ...]

MAX_IMAGE_PIXELS = 4_000_000


@dataclass(frozen=True)
class Style:
    scale: int = 4
    font_path: str | None = None
    floor: Color = (205, 205, 205)
    wall: Color = (120, 120, 120)
    obstacle: Color = (90, 90, 90)
    background: Color = (245, 245, 245, 255)
    forbidden: Color = (230, 30, 30, 255)
    spot: Color = (230, 30, 30, 255)
    path: Color = (255, 255, 255, 220)
    dock: Color = (130, 90, 230, 255)
    fan: Color = (255, 200, 60, 255)
    water: Color = (90, 190, 255, 255)


@dataclass
class Scene:
    grid: GridMap
    rooms: tuple[Room, ...] = ()
    areas: tuple[Area, ...] = ()
    path: list[Point] = field(default_factory=list)
    robot: Point | None = None
    dock: Point | None = None
    spot: tuple[Point, ...] | None = None
    show_settings: bool = False
    fan_labels: dict[str, str] = field(default_factory=dict)
    water_labels: dict[int, str] = field(default_factory=dict)
    room_colors: dict[int, Color] = field(default_factory=dict)


@lru_cache(maxsize=16)
def load_font(size: int, path: str | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (path, *FONT_CANDIDATES):
        if candidate and os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def calibration(grid: GridMap, scale: int) -> list[dict]:
    x0, y0 = grid.x_min * 1000, grid.y_min * 1000
    span_x, span_y = grid.width * grid.cell_mm, grid.height * grid.cell_mm
    return [
        {"vacuum": {"x": round(x0), "y": round(y0)}, "map": {"x": 0, "y": grid.height * scale}},
        {
            "vacuum": {"x": round(x0 + span_x), "y": round(y0)},
            "map": {"x": grid.width * scale, "y": grid.height * scale},
        },
        {"vacuum": {"x": round(x0), "y": round(y0 + span_y)}, "map": {"x": 0, "y": 0}},
    ]


class Renderer:
    def __init__(self, codes: CellCodes, style: Style | None = None) -> None:
        self.codes = codes
        self.style = style or Style()

    def render(self, scene: Scene) -> tuple[bytes, list[dict]]:
        grid = scene.grid
        scale = min(self.style.scale, max(1, math.isqrt(MAX_IMAGE_PIXELS // (grid.width * grid.height))))
        style = dataclasses.replace(self.style, scale=scale)
        base = Image.frombytes("P", (grid.width, grid.height), bytes(grid.cells))
        base.putpalette(self._palette(scene.room_colors))
        base.info["transparency"] = self.codes.unknown
        base = (
            base.convert("RGBA")
            .transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            .resize((grid.width * scale, grid.height * scale), Image.Resampling.NEAREST)
        )
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        _Painter(ImageDraw.Draw(layer), grid, style).paint(scene)
        canvas = Image.new("RGBA", base.size, style.background)
        canvas.alpha_composite(base)
        canvas.alpha_composite(layer)
        out = io.BytesIO()
        canvas.save(out, format="PNG", optimize=True)
        return out.getvalue(), calibration(grid, scale)

    def _palette(self, room_colors: dict[int, Color]) -> list[int]:
        colors: list[int] = []
        for index in range(256):
            if index == self.codes.wall:
                color = self.style.wall
            elif index == self.codes.unknown:
                color = (0, 0, 0)
            elif index == self.codes.floor:
                color = self.style.floor
            elif self.codes.is_room(index):
                color = room_colors.get(index, self.style.floor)
            else:
                color = self.style.obstacle
            colors += color[:3]
        return colors


class _Painter:
    def __init__(self, draw: ImageDraw.ImageDraw, grid: GridMap, style: Style) -> None:
        self.draw = draw
        self.grid = grid
        self.style = style
        self.font = load_font(15, style.font_path)
        self.small = load_font(12, style.font_path)

    def at(self, point: Point) -> tuple[float, float]:
        grid, scale = self.grid, self.style.scale
        col = (point[0] / 1000 - grid.x_min) / grid.resolution
        row = (point[1] / 1000 - grid.y_min) / grid.resolution
        return col * scale, (grid.height - row) * scale

    def paint(self, scene: Scene) -> None:
        for area in scene.areas:
            self.area(area)
        if len(scene.path) > 1:
            self.draw.line([self.at(p) for p in scene.path], fill=self.style.path, width=2, joint="curve")
        if scene.spot and len(scene.spot) >= 3:
            points = [self.at(v) for v in scene.spot]
            self.draw.polygon(points, fill=(*self.style.spot[:3], 40))
            self.dashed(points, self.style.spot)
        for room in scene.rooms:
            if room.pos and room.name:
                self.room_label(room, scene)
        if scene.dock:
            self.dock(self.at(scene.dock))
        if scene.robot:
            self.robot(self.at(scene.robot))

    def area(self, area: Area) -> None:
        if len(area.vertexs) < 2:
            return
        points = [self.at(v) for v in area.vertexs]
        if area.forbidden:
            self.draw.polygon(points, fill=self.style.forbidden, outline=self.style.forbidden)
        elif area.cleanable:
            self.draw.polygon(points, fill=(255, 255, 255, 60))
            self.dashed(points, (40, 40, 40, 255))
            if area.name:
                left, top = min(p[0] for p in points) + 6, min(p[1] for p in points) + 6
                box = self.draw.textbbox((left, top), area.name, font=self.small)
                self.draw.rounded_rectangle(
                    (box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2), radius=4, fill=(40, 40, 40, 210)
                )
                self.draw.text((left, top), area.name, font=self.small, fill=(255, 255, 255, 255))

    def room_label(self, room: Room, scene: Scene) -> None:
        cx, cy = self.at(room.pos)
        box = self.draw.textbbox((0, 0), room.name, font=self.font)
        width, height = box[2] - box[0], box[3] - box[1]
        self.draw.rounded_rectangle(
            (cx - width / 2 - 10, cy - height / 2 - 7, cx + width / 2 + 10, cy + height / 2 + 7),
            radius=14,
            fill=(255, 255, 255, 240),
            outline=(200, 200, 200, 255),
        )
        self.draw.text((cx - width / 2, cy - height / 2 - box[1]), room.name, font=self.font, fill=(30, 30, 30, 255))
        if scene.show_settings:
            self.settings(room, scene, cx, cy + height / 2 + 12)

    def settings(self, room: Room, scene: Scene, cx: float, top: float) -> None:
        parts = [
            (kind, text)
            for kind, text in (
                ("fan", scene.fan_labels.get(room.fan) if room.fan else None),
                ("water", scene.water_labels.get(room.water) if room.water is not None else None),
            )
            if text
        ]
        if not parts:
            return
        widths = [self.draw.textlength(text, font=self.small) + 16 for _, text in parts]
        total = sum(widths) + 8 * (len(parts) - 1) + 12
        left = cx - total / 2
        self.draw.rounded_rectangle((left, top, left + total, top + 18), radius=9, fill=(40, 40, 40, 210))
        x = left + 6
        for (kind, text), part_width in zip(parts, widths, strict=True):
            if kind == "fan":
                self.fan(x + 5, top + 9, 6)
            else:
                self.drop(x + 5, top + 8, 4)
            self.draw.text((x + 14, top + 2), text, font=self.small, fill=(255, 255, 255, 255))
            x += part_width + 8

    def dock(self, center: tuple[float, float]) -> None:
        x, y = center
        self.draw.rounded_rectangle((x - 9, y - 9, x + 9, y + 9), radius=4, fill=self.style.dock)
        self.draw.polygon(
            [(x + 2, y - 7), (x - 4, y + 1), (x, y + 1), (x - 2, y + 7), (x + 4, y - 1), (x, y - 1)],
            fill=(255, 255, 255, 255),
        )

    def robot(self, center: tuple[float, float]) -> None:
        x, y = center
        self.draw.ellipse(
            (x - 11, y - 11, x + 11, y + 11), fill=(255, 255, 255, 255), outline=(60, 60, 60, 255), width=2
        )
        self.draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(60, 60, 60, 255))

    def dashed(self, points: list[tuple[float, float]], color: Color, width: int = 2, step: float = 8) -> None:
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1], strict=True):
            count = max(1, int(math.hypot(x2 - x1, y2 - y1) // step))
            for i in range(0, count, 2):
                a, b = i / count, min(1, (i + 1) / count)
                self.draw.line(
                    (x1 + (x2 - x1) * a, y1 + (y2 - y1) * a, x1 + (x2 - x1) * b, y1 + (y2 - y1) * b),
                    fill=color,
                    width=width,
                )

    def fan(self, cx: float, cy: float, radius: float) -> None:
        for angle in (90, 210, 330):
            t = math.radians(angle)
            blade = []
            for i in range(24):
                u = 2 * math.pi * i / 24
                ex = radius * 0.55 * math.cos(u) + radius * 0.5
                ey = radius * 0.3 * math.sin(u) + radius * 0.12 * math.cos(u) ** 2
                blade.append((cx + ex * math.cos(t) - ey * math.sin(t), cy + ex * math.sin(t) + ey * math.cos(t)))
            self.draw.polygon(blade, fill=self.style.fan)
        self.draw.ellipse((cx - 1.5, cy - 1.5, cx + 1.5, cy + 1.5), fill=self.style.fan)

    def drop(self, cx: float, cy: float, radius: float) -> None:
        self.draw.ellipse((cx - radius, cy - radius * 0.4, cx + radius, cy + radius * 1.6), fill=self.style.water)
        self.draw.polygon(
            [(cx - radius * 0.85, cy + radius * 0.3), (cx, cy - radius * 1.4), (cx + radius * 0.85, cy + radius * 0.3)],
            fill=self.style.water,
        )
