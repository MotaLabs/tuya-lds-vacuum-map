import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Protocol

from .integrations.homeassistant import HomeAssistantError
from .lds import commands
from .lds.messages import Room
from .map.hit import area_at, room_at
from .map.model import MapModel
from .robot import Robot

log = logging.getLogger(__name__)


class TapMode(StrEnum):
    ROOM = "room"
    SPOT = "spot"


class Sender(Protocol):
    async def send_raw(self, value: str) -> None: ...


class DoubleTap:
    def __init__(self, window_seconds: float) -> None:
        self.window = window_seconds
        self._last: tuple[float, int] | None = None

    def tap(self, now: float, room_id: int | None) -> bool:
        if room_id is not None and self._last and self._last[1] == room_id and now - self._last[0] <= self.window:
            self._last = None
            return True
        self._last = (now, room_id) if room_id is not None else None
        return False


class TapController:
    def __init__(
        self,
        robot: Robot,
        map_model: MapModel,
        sender: Sender,
        on_room_selected: Callable[[Room], Awaitable[None]],
        double_tap_seconds: float,
        spot_size_mm: int,
    ) -> None:
        self.robot = robot
        self.map = map_model
        self.sender = sender
        self.on_room_selected = on_room_selected
        self.double_tap = DoubleTap(double_tap_seconds)
        self.spot_size_mm = spot_size_mm
        self._pending: asyncio.Task | None = None

    def map_id(self) -> int | None:
        if self.robot.rooms and self.robot.rooms.map_id is not None:
            return self.robot.rooms.map_id
        return self.map.map_id

    async def tap(self, mode: TapMode, x: int, y: int) -> None:
        map_id = self.map_id()
        if map_id is None:
            log.warning("tap ignored: no map id known yet")
            return
        if mode is TapMode.SPOT:
            self._cancel_pending()
            await self.sender.send_raw(commands.clean_spot(map_id, x, y, self.spot_size_mm))
            self.robot.spot = tuple((vx, vy) for vx, vy in commands.spot_square(x, y, self.spot_size_mm))
            return
        rooms = self.robot.rooms
        room = room_at(self.map.view, rooms.rooms if rooms else (), x, y)
        if self.double_tap.tap(asyncio.get_running_loop().time(), room.id if room else None):
            self._cancel_pending()
            await self.on_room_selected(room)
            return
        area = area_at(rooms.areas if rooms else (), x, y)
        if area is not None and rooms is not None:
            command = commands.clean_areas(map_id, rooms.raw_areas)
        elif room is not None:
            command = commands.clean_rooms(map_id, [room.id])
        else:
            return
        self._cancel_pending()
        self._pending = asyncio.create_task(self._send_after_window(command))

    async def _send_after_window(self, command: str) -> None:
        await asyncio.sleep(self.double_tap.window)
        try:
            await self.sender.send_raw(command)
        except HomeAssistantError as error:
            log.warning("cleaning command failed: %s", error)

    def _cancel_pending(self) -> None:
        if self._pending and not self._pending.done():
            self._pending.cancel()
        self._pending = None
