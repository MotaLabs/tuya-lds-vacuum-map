import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .codec import decode_name

ROOM_LIST = 21004
AREA_WRITE = 21003
CLEAN_TARGET = 21023
START = 21005
PATH = 21011
COMMANDS = 30000
MAX_COORDINATE_MM = 100_000
UNPRINTABLE = frozenset({"Cc", "Cs", "Zl", "Zp"})

Point = tuple[int, int]


@dataclass(frozen=True)
class Room:
    id: int
    name: str
    pos: Point | None = None
    fan: str | None = None
    water: int | None = None
    clean_type: str | None = None


@dataclass(frozen=True)
class Area:
    id: int
    mode: str
    active: str
    name: str
    vertexs: tuple[Point, ...] = ()
    is_wall: bool = False

    @property
    def forbidden(self) -> bool:
        return self.active == "forbid"

    @property
    def cleanable(self) -> bool:
        return self.mode == "area" and not self.forbidden


@dataclass(frozen=True)
class RoomList:
    map_id: int | None
    rooms: tuple[Room, ...]
    areas: tuple[Area, ...]
    raw_areas: list[dict[str, Any]] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [room.name for room in self.rooms if room.name]

    def room_named(self, name: str | None) -> Room | None:
        return next((room for room in self.rooms if room.name == name), None) if name else None


@dataclass(frozen=True)
class PathChunk:
    path_id: object
    start: int
    points: tuple[Point, ...]
    total: int | None = None


def parse_room_list(message: dict[str, Any]) -> RoomList | None:
    if message.get("infoType") != ROOM_LIST:
        return None
    data = as_object(message.get("data"))
    rooms = tuple(
        Room(
            id=as_int(item.get("id")),
            name=as_label(decode_name(item.get("name"))),
            pos=as_point(item.get("pos")),
            fan=as_text(item.get("fanLevel")),
            water=as_optional_int(item.get("waterPump")),
            clean_type=as_text(item.get("cleanType")),
        )
        for item in as_objects(data.get("autoAreaValue"))
    )
    raw_areas = as_objects(data.get("value"))
    return RoomList(
        map_id=as_optional_int(data.get("mapId")),
        rooms=rooms,
        areas=tuple(parse_area(item) for item in raw_areas),
        raw_areas=raw_areas,
    )


def parse_area(item: dict[str, Any]) -> Area:
    return Area(
        id=as_int(item.get("id", 0)),
        mode=as_text(item.get("mode")) or "",
        active=as_text(item.get("active")) or "",
        name=as_label(decode_name(item.get("tag")) or as_text(item.get("name")) or ""),
        vertexs=as_points(item.get("vertexs")),
        is_wall=item.get("isWall") is True,
    )


def spot_in_commands(message: dict[str, Any]) -> tuple[Point, ...] | None:
    if message.get("infoType") != COMMANDS:
        return None
    for command in as_objects(as_object(message.get("data")).get("cmds")):
        if command.get("infoType") != CLEAN_TARGET:
            continue
        for area in as_objects(as_object(command.get("data")).get("extraAreas")):
            if area.get("mode") == "point":
                return as_points(area.get("vertexs")) or None
    return None


def path_chunk(message: dict[str, Any]) -> PathChunk | None:
    data = message.get("data")
    if message.get("infoType") != PATH or not isinstance(data, dict):
        return None
    total = as_optional_int(data.get("totalPoints"))
    return PathChunk(
        path_id=data.get("pathID"),
        start=max(0, as_optional_int(data.get("startPos")) or 0),
        points=as_points(data.get("posArray")),
        total=total if total is not None and total >= 0 else None,
    )


def as_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_objects(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def as_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def as_label(text: str) -> str:
    return "".join(char for char in text if unicodedata.category(char) not in UNPRINTABLE)


def as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"not a number: {value!r}")
    try:
        return int(value)
    except OverflowError as error:
        raise ValueError(f"not a finite number: {value!r}") from error


def as_optional_int(value: Any) -> int | None:
    return None if value is None else as_int(value)


def as_point(value: Any) -> Point | None:
    if not value:
        return None
    if not isinstance(value, list | tuple) or len(value) < 2:
        raise ValueError(f"not a point: {value!r}")
    return point_at(value[0], value[1])


def point_at(x: Any, y: Any) -> Point:
    point = as_int(x), as_int(y)
    if abs(point[0]) > MAX_COORDINATE_MM or abs(point[1]) > MAX_COORDINATE_MM:
        raise ValueError(f"point out of range: {(x, y)!r}")
    return point


def as_points(value: Any) -> tuple[Point, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(point for point in map(as_point, value) if point)
