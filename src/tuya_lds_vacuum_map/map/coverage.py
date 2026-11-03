import math
from itertools import pairwise

from ..lds.messages import Room
from ..profiles import CellCodes
from .grid import Cell, GridMap, Point

MARGIN_CELLS = 2


class Coverage:
    def __init__(self, codes: CellCodes, cell_mm: int, cleaning_radius_mm: int, cells: set[Cell] | None = None) -> None:
        self.codes = codes
        self.cell_mm = cell_mm
        self.cleaning_radius_mm = cleaning_radius_mm
        self.cells: set[Cell] = set(cells or ())
        self._nearest: dict[Cell, int] = {}
        self._seeds_key: tuple | None = None
        reach = math.ceil(cleaning_radius_mm / cell_mm)
        limit = cleaning_radius_mm * cleaning_radius_mm
        self._footprint = [
            (dc, dr)
            for dc in range(-reach, reach + 1)
            for dr in range(-reach, reach + 1)
            if (dc * dc + dr * dr) * cell_mm * cell_mm <= limit
        ]

    def paint(self, points: list[Point]) -> bool:
        if not points:
            return False
        before = len(self.cells)
        for (x1, y1), (x2, y2) in list(pairwise(points)) or [(points[0], points[0])]:
            steps = max(1, int(math.hypot(x2 - x1, y2 - y1) // (self.cell_mm / 2)))
            for i in range(steps + 1):
                col = int((x1 + (x2 - x1) * i / steps) // self.cell_mm)
                row = int((y1 + (y2 - y1) * i / steps) // self.cell_mm)
                self.cells.update((col + dc, row + dr) for dc, dr in self._footprint)
        return len(self.cells) != before

    def clear(self) -> None:
        self.cells.clear()
        self._nearest.clear()

    def to_list(self) -> list[int]:
        return [value for cell in sorted(self.cells) for value in cell]

    def load_list(self, values: list[int]) -> None:
        self.cells = {(values[i], values[i + 1]) for i in range(0, len(values) - 1, 2)}
        self._nearest.clear()

    def apply(self, base: GridMap | None, rooms: tuple[Room, ...]) -> GridMap | None:
        seeds = [(room.id, room.pos) for room in rooms if room.pos and self.codes.is_room(room.id)]
        if tuple(seeds) != self._seeds_key:
            self._seeds_key = tuple(seeds)
            self._nearest.clear()
        if base is not None:
            return self._over(base, seeds)
        return self._alone(seeds) if self.cells else None

    def _center(self, cell: Cell) -> tuple[float, float]:
        return (cell[0] + 0.5) * self.cell_mm, (cell[1] + 0.5) * self.cell_mm

    def _over(self, base: GridMap, seeds: list[tuple[int, Point]]) -> GridMap:
        grid = base.copy()
        for cell in self.cells:
            target = grid.cell_of(*self._center(cell))
            value = grid.get(target)
            if value is None or self.codes.is_room(value):
                continue
            grid.set(target, self._nearest_room(cell, seeds))
        return grid

    def _alone(self, seeds: list[tuple[int, Point]]) -> GridMap:
        cols = [col for col, _ in self.cells]
        rows = [row for _, row in self.cells]
        col0, row0 = min(cols) - MARGIN_CELLS, min(rows) - MARGIN_CELLS
        width = max(cols) - col0 + 1 + MARGIN_CELLS
        height = max(rows) - row0 + 1 + MARGIN_CELLS
        grid = GridMap(
            width,
            height,
            self.cell_mm / 1000,
            col0 * self.cell_mm / 1000,
            row0 * self.cell_mm / 1000,
            bytearray([self.codes.unknown]) * (width * height),
        )
        for col, row in self.cells:
            grid.set((col - col0, row - row0), self._nearest_room((col, row), seeds))
        for col, row in self.cells:
            for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if (col + dc, row + dr) not in self.cells:
                    grid.set((col + dc - col0, row + dr - row0), self.codes.wall)
        return grid

    def _nearest_room(self, cell: Cell, seeds: list[tuple[int, Point]]) -> int:
        cached = self._nearest.get(cell)
        if cached is not None:
            return cached
        x, y = self._center(cell)
        best = min(seeds, key=lambda seed: (seed[1][0] - x) ** 2 + (seed[1][1] - y) ** 2, default=None)
        value = best[0] if best else self.codes.floor
        self._nearest[cell] = value
        return value
