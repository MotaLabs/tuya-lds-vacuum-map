from typing import Any

from ..lds.messages import Point, Room
from .coverage import Coverage
from .grid import GridMap


class MapModel:
    def __init__(self, coverage: Coverage, cloud_payload: dict[str, Any] | None = None) -> None:
        self.coverage = coverage
        self.cloud_payload: dict[str, Any] | None = None
        self.cloud: GridMap | None = None
        self.view: GridMap | None = None
        if cloud_payload:
            self.cloud = GridMap.from_cloud(cloud_payload)
            self.cloud_payload = cloud_payload

    @property
    def dock(self) -> Point | None:
        return self.cloud.dock if self.cloud else None

    @property
    def map_id(self) -> int | None:
        return self.cloud.map_id if self.cloud else None

    def set_cloud(self, payload: dict[str, Any]) -> bool:
        grid = GridMap.from_cloud(payload)
        if payload == self.cloud_payload:
            return False
        self.cloud = grid
        self.cloud_payload = payload
        self.coverage.clear()
        return True

    def compose(self, rooms: tuple[Room, ...]) -> GridMap | None:
        self.view = self.coverage.apply(self.cloud, rooms)
        return self.view
