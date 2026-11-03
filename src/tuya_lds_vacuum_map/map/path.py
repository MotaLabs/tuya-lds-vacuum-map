from ..lds.messages import PathChunk
from .grid import Point


class PathTracker:
    def __init__(self) -> None:
        self.path_id: object = None
        self.points: list[Point] = []

    @property
    def position(self) -> Point | None:
        return self.points[-1] if self.points else None

    def update(self, chunk: PathChunk) -> list[Point]:
        incoming = list(chunk.points)
        if chunk.path_id != self.path_id:
            self.path_id = chunk.path_id
            self.points = []
        start = min(chunk.start, len(self.points))
        joint = self.points[start - 1 : start] if start > 0 else []
        self.points[start:] = incoming
        if chunk.total is not None:
            del self.points[chunk.total :]
        return joint + incoming
