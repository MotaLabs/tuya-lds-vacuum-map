import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .integrations.homeassistant import HomeAssistantError
from .lds import commands
from .lds.messages import Point, Room
from .map.hit import area_at, room_at
from .map.model import MapModel
from .robot import Robot

log = logging.getLogger(__name__)

START_DELAY_RANGE = (1.0, 300.0)


class TapMode(StrEnum):
    ROOM = "room"
    SPOT = "spot"


class Sender(Protocol):
    async def send_raw(self, value: str) -> None: ...


def start_delay(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError(f"not a number: {value!r}")
    try:
        seconds = float(value)
    except OverflowError as error:
        raise ValueError(f"not a finite number: {value!r}") from error
    low, high = START_DELAY_RANGE
    if not low <= seconds <= high:
        raise ValueError(f"start delay out of range: {value!r}")
    return seconds


async def send_or_log(sender: Sender, command: str) -> bool:
    try:
        await sender.send_raw(command)
    except (HomeAssistantError, TimeoutError) as error:
        log.warning("cleaning command failed: %s", str(error) or type(error).__name__)
        return False
    return True


@dataclass(frozen=True)
class SpotPreview:
    map_id: int
    center: Point
    size_mm: int

    @property
    def square(self) -> tuple[Point, ...]:
        return tuple((x, y) for x, y in commands.spot_square(*self.center, self.size_mm))

    def contains(self, x: int, y: int) -> bool:
        half = self.size_mm // 2
        return abs(x - self.center[0]) <= half and abs(y - self.center[1]) <= half


class SpotPicker:
    def __init__(
        self,
        robot: Robot,
        sender: Sender,
        size_mm: int,
        auto_start: Callable[[], float | None],
        expiry_seconds: float,
        on_change: Callable[[], None],
    ) -> None:
        self.robot = robot
        self.sender = sender
        self.size_mm = size_mm
        self.auto_start = auto_start
        self.expiry = expiry_seconds
        self.on_change = on_change
        self.preview: SpotPreview | None = None
        self._timer: asyncio.Task | None = None

    async def tap(self, map_id: int, x: int, y: int) -> None:
        if self.preview and self.preview.contains(x, y):
            await self._start(self.preview)
            return
        self.preview = SpotPreview(map_id, (x, y), self.size_mm)
        self.reschedule()

    def reschedule(self) -> None:
        self._cancel_timer()
        if self.preview is None:
            return
        delay = self.auto_start()
        self._timer = asyncio.create_task(self._expire() if delay is None else self._start_after(self.preview, delay))

    def clear(self) -> None:
        self._cancel_timer()
        if self.preview:
            self.preview = None
            self.on_change()

    async def _start(self, preview: SpotPreview) -> None:
        self.preview = None
        self._cancel_timer()
        if await send_or_log(self.sender, commands.clean_spot(preview.map_id, *preview.center, preview.size_mm)):
            self.robot.spot = preview.square
        self.on_change()

    async def _start_after(self, preview: SpotPreview, delay: float) -> None:
        await asyncio.sleep(delay)
        self._timer = None
        await self._start(preview)

    async def _expire(self) -> None:
        await asyncio.sleep(self.expiry)
        self._timer = None
        self.clear()

    def _cancel_timer(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None


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
        spots: SpotPicker,
    ) -> None:
        self.robot = robot
        self.map = map_model
        self.sender = sender
        self.on_room_selected = on_room_selected
        self.double_tap = DoubleTap(double_tap_seconds)
        self.spots = spots
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
            await self.spots.tap(map_id, x, y)
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
        self.spots.clear()
        await send_or_log(self.sender, command)

    def _cancel_pending(self) -> None:
        if self._pending and not self._pending.done():
            self._pending.cancel()
        self._pending = None
