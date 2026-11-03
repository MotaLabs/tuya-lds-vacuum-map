import time
from typing import Any

from .codec import encode_raw
from .messages import AREA_WRITE, CLEAN_TARGET, COMMANDS, START

CLEAN_SEGMENTS = -3
CLEAN_AREAS = -2
SPOT_AREA = {"active": "depth", "id": 100, "mode": "point", "name": "aa", "tag": "room"}
_START = {"data": {"mode": "reAppointClean"}, "infoType": START}


def envelope(commands: list[dict[str, Any]], now_ms: int | None = None) -> str:
    timestamp = now_ms if now_ms is not None else int(time.time() * 1000)
    return encode_raw(
        {
            "dInfo": {"ts": str(timestamp), "userId": "0"},
            "data": {"cmds": commands, "mainCmds": [START]},
            "infoType": COMMANDS,
        }
    )


def clean_rooms(map_id: int, room_ids: list[int], now_ms: int | None = None) -> str:
    target = {"cleanId": [CLEAN_SEGMENTS], "extraAreas": [], "mapId": map_id, "segmentId": list(room_ids)}
    return envelope([{"data": target, "infoType": CLEAN_TARGET}, _START], now_ms)


def spot_square(x: int, y: int, size_mm: int) -> list[list[int]]:
    half = size_mm // 2
    return [[x - half, y - half], [x + half, y - half], [x + half, y + half], [x - half, y + half]]


def clean_spot(map_id: int, x: int, y: int, size_mm: int, now_ms: int | None = None) -> str:
    area = {**SPOT_AREA, "vertexs": spot_square(x, y, size_mm)}
    target = {"cleanId": [CLEAN_SEGMENTS], "extraAreas": [area], "mapId": map_id}
    return envelope([{"data": target, "infoType": CLEAN_TARGET}, _START], now_ms)


def clean_areas(map_id: int, raw_areas: list[dict[str, Any]], now_ms: int | None = None) -> str:
    write = {"data": {"mapId": map_id, "value": raw_areas}, "infoType": AREA_WRITE}
    target = {"data": {"cleanId": [CLEAN_AREAS], "extraAreas": [], "mapId": map_id}, "infoType": CLEAN_TARGET}
    return envelope([write, target, _START], now_ms)
