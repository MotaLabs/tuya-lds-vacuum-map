import logging
from dataclasses import dataclass, field
from typing import Any

from .lds import codec
from .lds.messages import Point, RoomList, parse_room_list, path_chunk, spot_in_commands
from .map.path import PathTracker
from .profiles import Profile

log = logging.getLogger(__name__)


@dataclass
class Update:
    redraw: bool = False
    rooms_changed: bool = False
    rooms_renamed: bool = False
    started_moving: bool = False
    stopped_moving: bool = False
    new_path: list[Point] = field(default_factory=list)


class Robot:
    def __init__(
        self,
        profile: Profile,
        room_message: dict[str, Any] | None = None,
        state: str | None = None,
        room_mode: bool | None = None,
    ) -> None:
        self.profile = profile
        self.room_message = room_message
        self.rooms: RoomList | None = parse_room_list(room_message) if room_message else None
        self.state = state
        self.room_mode = room_mode
        self.path = PathTracker()
        self.spot: tuple[Point, ...] | None = None

    @property
    def moving(self) -> bool:
        return self.state in self.profile.states.moving

    def apply(self, data_points: dict[int, Any]) -> Update:
        update = Update()
        dps = self.profile.data_points
        for dp, value in data_points.items():
            try:
                if dp == dps.state and isinstance(value, str):
                    self._apply_state(value, update)
                elif dp == dps.path and isinstance(value, str):
                    self._apply_path(value, update)
                elif dp == dps.raw and isinstance(value, str):
                    self._apply_raw(value, update)
                elif dp == dps.room_mode and isinstance(value, bool):
                    self.room_mode = value
                    update.redraw = True
            except (ValueError, KeyError, TypeError) as error:
                log.warning("could not read data point %s: %s", dp, error)
        return update

    def _apply_state(self, value: str, update: Update) -> None:
        was_moving, previous = self.moving, self.state
        self.state = value
        update.started_moving |= self.moving and not was_moving
        update.stopped_moving |= was_moving and not self.moving
        spot_states = self.profile.states.spot
        if previous in spot_states and value not in spot_states:
            self.spot = None
        update.redraw = True

    def _apply_path(self, value: str, update: Update) -> None:
        chunk = path_chunk(codec.decode_raw(value))
        if chunk is not None:
            update.new_path += self.path.update(chunk)
            update.redraw = True

    def _apply_raw(self, value: str, update: Update) -> None:
        message = codec.decode_raw(value)
        rooms = parse_room_list(message)
        if rooms is not None:
            update.rooms_changed = True
            update.rooms_renamed |= rooms.names != (self.rooms.names if self.rooms else [])
            self.rooms = rooms
            self.room_message = message
            update.redraw = True
        spot = spot_in_commands(message)
        if spot:
            self.spot = spot
            update.redraw = True
