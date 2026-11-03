from dataclasses import dataclass, field
from typing import Any

from .codec import decode_name

ROOM_LIST = 21004
AREA_WRITE = 21003
CLEAN_TARGET = 21023
START = 21005
PATH = 21011
COMMANDS = 30000

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


def parse_room_list(message: dict[str, Any]) -> RoomList | None:
    if message.get("infoType") != ROOM_LIST:
        return None
    data = message.get("data") or {}
    rooms = tuple(
        Room(
            id=int(item["id"]),
            name=decode_name(item.get("name")),
            pos=_point(item.get("pos")),
            fan=item.get("fanLevel"),
            water=item.get("waterPump"),
            clean_type=item.get("cleanType"),
        )
        for item in data.get("autoAreaValue") or []
    )
    raw_areas = list(data.get("value") or [])
    map_id = data.get("mapId")
    return RoomList(
        map_id=int(map_id) if map_id is not None else None,
        rooms=rooms,
        areas=tuple(parse_area(item) for item in raw_areas),
        raw_areas=raw_areas,
    )


def parse_area(item: dict[str, Any]) -> Area:
    return Area(
        id=int(item.get("id", 0)),
        mode=item.get("mode", ""),
        active=item.get("active", ""),
        name=decode_name(item.get("tag")) or item.get("name") or "",
        vertexs=tuple(_point(vertex) for vertex in item.get("vertexs") or []),
        is_wall=bool(item.get("isWall")),
    )


def spot_in_commands(message: dict[str, Any]) -> tuple[Point, ...] | None:
    if message.get("infoType") != COMMANDS:
        return None
    for command in (message.get("data") or {}).get("cmds") or []:
        if command.get("infoType") != CLEAN_TARGET:
            continue
        for area in (command.get("data") or {}).get("extraAreas") or []:
            if area.get("mode") == "point":
                return tuple(_point(vertex) for vertex in area.get("vertexs") or [])
    return None


def path_chunk(message: dict[str, Any]) -> dict[str, Any] | None:
    if message.get("infoType") != PATH:
        return None
    return message.get("data")


def _point(value: Any) -> Point | None:
    return (int(value[0]), int(value[1])) if value else None
