import base64
from dataclasses import dataclass
from typing import Any

import lz4.block

Point = tuple[int, int]
Cell = tuple[int, int]


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
    def from_cloud(cls, payload: dict[str, Any]) -> "GridMap":
        data = payload.get("data", payload)
        width, height = int(data["width"]), int(data["height"])
        raw = base64.b64decode(data["map"])
        try:
            cells = bytearray(lz4.block.decompress(raw, uncompressed_size=width * height))
        except lz4.block.LZ4BlockError as error:
            raise ValueError(f"map cells could not be decompressed: {error}") from error
        if len(cells) != width * height:
            raise ValueError(f"map has {len(cells)} cells, expected {width * height}")
        dock = data.get("chargeHandlePos")
        map_id = data.get("mapId")
        return cls(
            width=width,
            height=height,
            resolution=float(data["resolution"]),
            x_min=float(data["x_min"]),
            y_min=float(data["y_min"]),
            cells=cells,
            map_id=int(map_id) if map_id is not None else None,
            dock=(int(dock[0]), int(dock[1])) if dock else None,
        )

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
