from typing import Any

from .grid import Point


class PathTracker:
    def __init__(self) -> None:
        self.path_id: int | None = None
        self.points: list[Point] = []

    @property
    def position(self) -> Point | None:
        return self.points[-1] if self.points else None

    def update(self, chunk: dict[str, Any]) -> list[Point]:
        path_id = chunk.get("pathID")
        incoming = [(int(p[0]), int(p[1])) for p in chunk.get("posArray") or []]
        if path_id != self.path_id:
            self.path_id = path_id
            self.points = []
        start = min(int(chunk.get("startPos") or 0), len(self.points))
        joint = self.points[start - 1 : start] if start > 0 else []
        self.points[start:] = incoming
        total = chunk.get("totalPoints")
        if isinstance(total, int) and total >= 0:
            del self.points[total:]
        return joint + incoming
