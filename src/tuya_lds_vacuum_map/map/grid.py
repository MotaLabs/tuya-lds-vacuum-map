import base64
import math
from dataclasses import dataclass
from typing import Any

import lz4.block

from ..lds.messages import as_int, as_object, as_optional_int, as_point

Point = tuple[int, int]
Cell = tuple[int, int]

MAX_CELLS = 4_000_000


@dataclass
class GridMap:
    width: int
    height: int
    resolution: float
    x_min: float
    y_min: float
    cells: bytearray
    map_id: int | None = None
    dock: Point | None = None

    @classmethod
    def from_cloud(cls, payload: Any) -> "GridMap":
        data = as_object(payload)
        data = as_object(data.get("data", data))
        try:
            width, height = as_int(data.get("width")), as_int(data.get("height"))
            resolution, x_min, y_min = (_finite(data.get(key)) for key in ("resolution", "x_min", "y_min"))
            encoded = data.get("map")
            if not isinstance(encoded, str):
                raise ValueError("the map cells are missing")
            map_id, dock = as_optional_int(data.get("mapId")), as_point(data.get("chargeHandlePos"))
        except ValueError as error:
            raise ValueError(f"cloud map is malformed: {error}") from error
        if width <= 0 or height <= 0 or width * height > MAX_CELLS:
            raise ValueError(f"cloud map of {width}x{height} cells is empty or too large")
        if resolution <= 0:
            raise ValueError(f"cloud map resolution {resolution} is not positive")
        try:
            cells = bytearray(lz4.block.decompress(base64.b64decode(encoded), uncompressed_size=width * height))
        except lz4.block.LZ4BlockError as error:
            raise ValueError(f"map cells could not be decompressed: {error}") from error
        if len(cells) != width * height:
            raise ValueError(f"map has {len(cells)} cells, expected {width * height}")
        return cls(width, height, resolution, x_min, y_min, cells, map_id, dock)

    @property
    def cell_mm(self) -> float:
        return self.resolution * 1000

    def cell_of(self, x_mm: float, y_mm: float) -> Cell:
        return int((x_mm / 1000 - self.x_min) // self.resolution), int((y_mm / 1000 - self.y_min) // self.resolution)

    def contains(self, cell: Cell) -> bool:
        col, row = cell
        return 0 <= col < self.width and 0 <= row < self.height

    def get(self, cell: Cell) -> int | None:
        return self.cells[cell[1] * self.width + cell[0]] if self.contains(cell) else None

    def set(self, cell: Cell, value: int) -> None:
        if self.contains(cell):
            self.cells[cell[1] * self.width + cell[0]] = value

    def value_at(self, x_mm: float, y_mm: float) -> int | None:
        return self.get(self.cell_of(x_mm, y_mm))

    def copy(self) -> "GridMap":
        return GridMap(
            self.width,
            self.height,
            self.resolution,
            self.x_min,
            self.y_min,
            bytearray(self.cells),
            self.map_id,
            self.dock,
        )


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"not a number: {value!r}")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"not a finite number: {value!r}") from error
    if not math.isfinite(number):
        raise ValueError(f"not a finite number: {value!r}")
    return number
