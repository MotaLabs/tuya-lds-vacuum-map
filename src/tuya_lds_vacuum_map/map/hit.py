from ..lds.messages import Area, Point, Room
from .grid import GridMap


def point_in_polygon(x: float, y: float, vertexs: tuple[Point, ...]) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(vertexs, vertexs[1:] + vertexs[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def area_at(areas: tuple[Area, ...], x: float, y: float) -> Area | None:
    return next(
        (area for area in areas if area.cleanable and len(area.vertexs) >= 3 and point_in_polygon(x, y, area.vertexs)),
        None,
    )


def room_at(grid: GridMap | None, rooms: tuple[Room, ...], x: float, y: float) -> Room | None:
    if grid is None:
        return None
    value = grid.value_at(x, y)
    return next((room for room in rooms if room.id == value), None)
