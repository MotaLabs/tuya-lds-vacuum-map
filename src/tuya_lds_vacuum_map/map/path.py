from typing import Any

from ..lds.messages import PathChunk, as_int, as_points
from .grid import Point


class PathTracker:
    path_id: object
    points: list[Point]
    offset: int

    def __init__(self) -> None:
        self.restore(None, [], 0)

    def restore(self, path_id: object, points: Any, start: Any) -> None:
        kept, offset = list(as_points(points)), as_int(start)
        if offset < 0:
            raise ValueError(f"the path cannot start before 0: {start!r}")
        self.path_id, self.points, self.offset = path_id, kept, offset

    @property
    def position(self) -> Point | None:
        return self.points[-1] if self.points else None

    def update(self, chunk: PathChunk) -> list[Point]:
        incoming = list(chunk.points)
        start = chunk.start - self.offset
        if chunk.path_id != self.path_id or start not in range(len(self.points) + 1):
            self.path_id, self.points, self.offset = chunk.path_id, [], chunk.start
        joint = self.points[start - 1 : start]
        self.points[start:] = incoming
        if chunk.total is not None:
            del self.points[max(0, chunk.total - self.offset) :]
        return joint + incoming
