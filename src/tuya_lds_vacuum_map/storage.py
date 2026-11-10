import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from pathlib import Path
from typing import Any


class MapSource(StrEnum):
    AUTO = "auto"
    LOCAL = "local"


class RoomTap(StrEnum):
    CLEAN = "clean"
    LIST = "list"


@dataclass
class Snapshot:
    room_message: dict[str, Any] | None = None
    robot_state: str | None = None
    room_mode: bool | None = None
    cloud_map: dict[str, Any] | None = None
    source: MapSource = MapSource.AUTO
    selected_room: str | None = None
    coverage: list[int] = field(default_factory=list)
    room_colors: dict[str, str] = field(default_factory=dict)
    auto_start: bool = True
    start_delay: float = 10.0
    room_tap: RoomTap = RoomTap.LIST


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Snapshot:
        if not self.path.exists():
            return Snapshot()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Snapshot)}
        snapshot = Snapshot(**{key: value for key, value in data.items() if key in known})
        snapshot.source = MapSource(snapshot.source) if snapshot.source in tuple(MapSource) else MapSource.AUTO
        snapshot.room_tap = RoomTap(snapshot.room_tap) if snapshot.room_tap in tuple(RoomTap) else RoomTap.LIST
        return snapshot

    def save(self, snapshot: Snapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(asdict(snapshot), out, separators=(",", ":"))
            os.replace(temporary, self.path)
        except BaseException:
            os.unlink(temporary)
            raise
