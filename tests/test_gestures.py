import asyncio

import pytest

from support.fakes import ClosedSocket
from support.maps import CODES, PROFILE, cell_center_mm, cloud_payload, rooms_message
from support.waiting import eventually
from tuya_lds_vacuum_map.gestures import (
    START_DELAY_RANGE,
    DoubleTap,
    RoomPicker,
    SpotPicker,
    SpotPreview,
    TapController,
    TapMode,
    start_delay,
)
from tuya_lds_vacuum_map.integrations.homeassistant import HomeAssistant, HomeAssistantError
from tuya_lds_vacuum_map.integrations.localtuya import LocalTuyaLink
from tuya_lds_vacuum_map.lds.codec import decode_raw
from tuya_lds_vacuum_map.map.coverage import Coverage
from tuya_lds_vacuum_map.map.model import MapModel
from tuya_lds_vacuum_map.robot import Robot

WINDOW = 0.05
EXPIRY = 0.2
DELAY = 0.05
LEFT = cell_center_mm(10, 14)
RIGHT = cell_center_mm(30, 14)
CATS = cell_center_mm(26, 7)


class FakeSender:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[dict] = []
        self.error = error

    async def send_raw(self, value: str) -> None:
        if self.error:
            raise self.error
        self.sent.append(decode_raw(value))


class Setup:
    def __init__(
        self, room_message: dict | None = None, payload: dict | None = None, sender: FakeSender | None = None
    ) -> None:
        self.robot = Robot(PROFILE, room_message)
        self.map = MapModel(Coverage(CODES, 50, 150), payload)
        self.map.compose(self.robot.rooms.rooms if self.robot.rooms else ())
        self.sender = sender or FakeSender()
        self.selected = []
        self.changes = 0
        self.delay: float | None = None
        self.spots = SpotPicker(self.robot, self.sender, 1500, lambda: self.delay, EXPIRY, self.changed)
        self.rooms = RoomPicker(self.sender, lambda: self.delay, self.changed)
        self.taps = TapController(self.robot, self.map, self.sender, self.select, WINDOW, self.spots, self.rooms)

    async def select(self, room) -> None:
        self.selected.append(room)

    def changed(self) -> None:
        self.changes += 1

    def targets(self) -> list[dict]:
        return [command["data"]["cmds"] for command in self.sender.sent]


@pytest.fixture
def setup() -> Setup:
    return Setup(rooms_message(), cloud_payload())


def test_double_tap_needs_the_same_room_inside_the_window():
    taps = DoubleTap(0.5)
    assert not taps.tap(0.0, 1)
    assert taps.tap(0.4, 1)
    assert not taps.tap(0.5, 1)
    assert not taps.tap(1.1, 1)
    assert not taps.tap(1.2, 2)
    assert not taps.tap(1.3, None)
    assert not taps.tap(1.4, 2)


async def test_two_taps_open_the_room_settings(setup):
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await asyncio.sleep(WINDOW * 3)
    assert setup.sender.sent == []
    assert [room.name for room in setup.selected] == ["Left"]


async def test_tap_on_an_area_cleans_the_areas(setup):
    await setup.taps.tap(TapMode.ROOM, *CATS)
    await eventually(lambda: setup.sender.sent)
    [cmds] = setup.targets()
    assert cmds[0]["infoType"] == 21003
    assert cmds[0]["data"] == {"mapId": 42, "value": rooms_message()["data"]["value"]}
    assert cmds[1]["data"] == {"cleanId": [-2], "extraAreas": [], "mapId": 42}


async def test_a_spot_tap_shows_its_square_and_sends_nothing(setup):
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    assert setup.spots.preview == SpotPreview(42, (0, 0), 1500)
    assert setup.spots.preview.square == ((-750, -750), (750, -750), (750, 750), (-750, 750))
    await asyncio.sleep(WINDOW * 3)
    assert setup.sender.sent == []
    assert setup.robot.spot is None


async def test_a_tap_inside_the_preview_cleans_the_previewed_spot(setup):
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await setup.taps.tap(TapMode.SPOT, 750, -750)
    [cmds] = setup.targets()
    assert cmds[0]["data"]["mapId"] == 42
    assert cmds[0]["data"]["extraAreas"][0]["vertexs"] == [[-750, -750], [750, -750], [750, 750], [-750, 750]]
    assert setup.robot.spot == ((-750, -750), (750, -750), (750, 750), (-750, 750))
    assert setup.spots.preview is None
    assert setup.changes == 1
    await asyncio.sleep(EXPIRY * 2)
    assert len(setup.sender.sent) == 1


@pytest.mark.parametrize(("x", "y"), [(751, 0), (-751, 0), (0, 751), (0, -751)])
async def test_a_tap_outside_the_preview_moves_it(setup, x, y):
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await setup.taps.tap(TapMode.SPOT, x, y)
    assert setup.spots.preview.center == (x, y)
    assert setup.sender.sent == []


async def test_an_unconfirmed_preview_goes_away(setup):
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await asyncio.sleep(EXPIRY / 2)
    assert setup.spots.preview
    await eventually(lambda: setup.spots.preview is None)
    assert setup.changes == 1
    assert setup.sender.sent == []


async def test_moving_the_preview_restarts_its_time(setup):
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await asyncio.sleep(EXPIRY * 0.7)
    await setup.taps.tap(TapMode.SPOT, 5000, 0)
    await asyncio.sleep(EXPIRY * 0.7)
    assert setup.spots.preview.center == (5000, 0)


async def test_a_double_tap_keeps_the_preview(setup):
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await asyncio.sleep(WINDOW * 2)
    assert setup.spots.preview
    assert setup.changes == 0


async def test_a_preview_starts_by_itself_after_the_delay(setup):
    setup.delay = DELAY
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await asyncio.sleep(DELAY / 2)
    assert setup.sender.sent == []
    await eventually(lambda: setup.sender.sent)
    [cmds] = setup.targets()
    assert cmds[0]["data"]["extraAreas"][0]["vertexs"] == [[-750, -750], [750, -750], [750, 750], [-750, 750]]
    assert setup.robot.spot == ((-750, -750), (750, -750), (750, 750), (-750, 750))
    assert setup.spots.preview is None
    assert setup.changes == 1


async def test_moving_the_preview_restarts_the_delay(setup):
    setup.delay = DELAY
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await asyncio.sleep(DELAY * 0.7)
    await setup.taps.tap(TapMode.SPOT, 5000, 0)
    await asyncio.sleep(DELAY * 0.7)
    assert setup.sender.sent == []
    await eventually(lambda: setup.sender.sent)
    [cmds] = setup.targets()
    assert cmds[0]["data"]["extraAreas"][0]["vertexs"][0] == [4250, -750]


async def test_turning_the_automatic_start_off_keeps_the_preview(setup):
    setup.delay = DELAY
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    setup.delay = None
    setup.spots.reschedule()
    await asyncio.sleep(DELAY * 2)
    assert setup.sender.sent == []
    assert setup.spots.preview
    await eventually(lambda: setup.spots.preview is None)
    assert setup.sender.sent == []


async def test_turning_the_automatic_start_on_starts_the_preview(setup):
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    setup.delay = DELAY
    setup.spots.reschedule()
    await eventually(lambda: setup.sender.sent)


async def test_rescheduling_without_a_preview_does_nothing(setup):
    setup.delay = DELAY
    setup.spots.reschedule()
    await asyncio.sleep(DELAY * 2)
    assert setup.sender.sent == []
    assert setup.changes == 0


async def test_a_spot_lost_to_a_closed_connection_is_logged(caplog):
    ha = HomeAssistant("http://127.0.0.1:1", "token")
    ha._ws = ClosedSocket()
    ha.connected.set()
    setup = Setup(rooms_message(), cloud_payload(), LocalTuyaLink(ha, "dev", 127))
    setup.delay = DELAY
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await eventually(lambda: "cleaning command failed: websocket closed" in caplog.text)
    assert setup.changes == 1
    assert setup.robot.spot is None


async def test_a_spot_that_fails_to_start_by_itself_is_logged(caplog):
    setup = Setup(rooms_message(), cloud_payload(), FakeSender(TimeoutError()))
    setup.delay = DELAY
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await eventually(lambda: "cleaning command failed: TimeoutError" in caplog.text)
    assert setup.spots.preview is None
    assert setup.robot.spot is None


@pytest.mark.parametrize(("value", "seconds"), [("10", 10.0), ("1", 1.0), ("300.0", 300.0), (2.5, 2.5), (60, 60.0)])
def test_start_delay(value, seconds):
    assert start_delay(value) == seconds


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("0.99", "out of range"),
        ("300.5", "out of range"),
        ("nan", "out of range"),
        ("inf", "out of range"),
        ("-inf", "out of range"),
        ("ten", "could not convert"),
        ("", "could not convert"),
        (True, "not a number"),
        (None, "not a number"),
        ([], "not a number"),
        (10**400, "not a finite number"),
    ],
)
def test_a_start_delay_that_is_not_a_number_in_range_is_refused(value, message):
    with pytest.raises(ValueError, match=message):
        start_delay(value)


def test_the_start_delay_range():
    assert START_DELAY_RANGE == (1.0, 300.0)


async def test_a_spot_that_fails_to_start_is_logged(caplog):
    setup = Setup(rooms_message(), cloud_payload(), FakeSender(HomeAssistantError("offline")))
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    assert "cleaning command failed: offline" in caplog.text
    assert setup.spots.preview is None
    assert setup.robot.spot is None
    assert setup.changes == 1


async def test_tap_outside_rooms_does_nothing(setup):
    await setup.taps.tap(TapMode.ROOM, *cell_center_mm(20, 14))
    await asyncio.sleep(WINDOW * 3)
    assert setup.sender.sent == []
    assert setup.selected == []


async def test_nothing_is_sent_without_a_map_id():
    setup = Setup()
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    assert setup.sender.sent == []


def test_the_room_list_map_id_wins_over_the_cloud():
    assert Setup(rooms_message(), cloud_payload(map_id=7)).taps.map_id() == 42
    assert Setup(None, cloud_payload(map_id=7)).taps.map_id() == 7


async def test_a_failed_command_is_logged(caplog):
    setup = Setup(rooms_message(), cloud_payload(), FakeSender(HomeAssistantError("offline")))
    await setup.taps.tap(TapMode.ROOM, *CATS)
    await eventually(lambda: "cleaning command failed: offline" in caplog.text)


async def test_a_command_that_times_out_is_logged(caplog):
    setup = Setup(rooms_message(), cloud_payload(), FakeSender(TimeoutError()))
    await setup.taps.tap(TapMode.ROOM, *CATS)
    await eventually(lambda: "cleaning command failed: TimeoutError" in caplog.text)


def segments(setup: Setup) -> list[list[int]]:
    return [cmds[0]["data"]["segmentId"] for cmds in setup.targets()]


async def tap_room(setup: Setup, point: tuple[int, int]) -> None:
    changes = setup.changes
    await setup.taps.tap(TapMode.ROOM, *point)
    await eventually(lambda: setup.changes > changes)


async def test_a_room_tap_adds_the_room_to_the_list_after_the_window(setup):
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    assert setup.rooms.ids == []
    await eventually(lambda: setup.rooms.ids == [1])
    assert setup.changes == 1
    await asyncio.sleep(WINDOW * 3)
    assert setup.sender.sent == []


async def test_a_tap_on_a_listed_room_takes_it_out(setup):
    await tap_room(setup, LEFT)
    await tap_room(setup, RIGHT)
    assert setup.rooms.ids == [1, 2]
    await tap_room(setup, LEFT)
    assert setup.rooms.ids == [2]
    assert setup.changes == 3
    assert setup.sender.sent == []


async def test_the_list_is_sent_in_the_order_tapped(setup):
    await tap_room(setup, RIGHT)
    await tap_room(setup, LEFT)
    await setup.taps.start()
    assert segments(setup) == [[2, 1]]
    assert setup.targets()[0][0]["data"]["mapId"] == 42
    assert setup.rooms.ids == []
    assert setup.changes == 3


async def test_a_list_starts_by_itself_after_the_delay(setup):
    setup.delay = DELAY
    await setup.taps.tap(TapMode.ROOM, *RIGHT)
    await asyncio.sleep(WINDOW + DELAY / 2)
    assert setup.sender.sent == []
    await eventually(lambda: setup.sender.sent)
    assert segments(setup) == [[2]]
    assert setup.rooms.ids == []


async def test_each_room_added_restarts_the_delay(setup):
    setup.delay = DELAY * 4
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await asyncio.sleep(WINDOW + DELAY * 2)
    await setup.taps.tap(TapMode.ROOM, *RIGHT)
    await asyncio.sleep(WINDOW + DELAY * 3)
    assert setup.sender.sent == []
    await eventually(lambda: setup.sender.sent)
    assert segments(setup) == [[1, 2]]


async def test_emptying_the_list_stops_the_delay(setup):
    setup.delay = DELAY * 4
    await tap_room(setup, LEFT)
    await tap_room(setup, LEFT)
    await asyncio.sleep(DELAY * 6)
    assert setup.sender.sent == []
    assert setup.rooms.ids == []


async def test_turning_the_automatic_start_off_keeps_the_list(setup):
    setup.delay = DELAY * 4
    await tap_room(setup, LEFT)
    setup.delay = None
    setup.rooms.reschedule()
    await asyncio.sleep(DELAY * 6)
    assert setup.sender.sent == []
    assert setup.rooms.ids == [1]
    setup.delay = DELAY
    setup.rooms.reschedule()
    await eventually(lambda: setup.sender.sent)
    assert segments(setup) == [[1]]


async def test_a_double_tap_leaves_the_list_as_it_is(setup):
    await tap_room(setup, LEFT)
    await setup.taps.tap(TapMode.ROOM, *RIGHT)
    await setup.taps.tap(TapMode.ROOM, *RIGHT)
    await asyncio.sleep(WINDOW * 3)
    assert [room.name for room in setup.selected] == ["Right"]
    assert setup.rooms.ids == [1]


async def test_one_pick_at_a_time_a_list_or_a_spot(setup):
    await tap_room(setup, LEFT)
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    assert setup.rooms.ids == []
    assert setup.spots.preview
    await tap_room(setup, RIGHT)
    assert setup.spots.preview is None
    assert setup.rooms.ids == [2]
    assert setup.sender.sent == []


async def test_an_area_tap_cleans_the_areas_and_drops_the_list(setup):
    await tap_room(setup, LEFT)
    await setup.taps.tap(TapMode.ROOM, *CATS)
    await eventually(lambda: setup.sender.sent)
    [cmds] = setup.targets()
    assert cmds[1]["data"] == {"cleanId": [-2], "extraAreas": [], "mapId": 42}
    assert setup.rooms.ids == []


async def test_start_without_a_list_starts_the_spot_preview(setup):
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await setup.taps.start()
    [cmds] = setup.targets()
    assert cmds[0]["data"]["extraAreas"][0]["vertexs"] == [[-750, -750], [750, -750], [750, 750], [-750, 750]]
    assert setup.spots.preview is None


async def test_start_with_nothing_picked_sends_nothing(setup):
    await setup.taps.start()
    assert setup.sender.sent == []
    assert setup.changes == 0


async def test_clear_drops_the_list_and_the_preview(setup):
    await tap_room(setup, LEFT)
    setup.taps.clear()
    assert setup.rooms.ids == []
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    setup.taps.clear()
    assert setup.spots.preview is None
    assert setup.changes == 3
    await setup.taps.start()
    assert setup.sender.sent == []


def test_a_room_of_another_map_starts_a_new_list():
    rooms = RoomPicker(FakeSender(), lambda: None, lambda: None)
    rooms.toggle(42, 1)
    rooms.toggle(42, 2)
    rooms.toggle(7, 2)
    assert (rooms.map_id, rooms.ids) == (7, [2])


async def test_a_list_that_fails_to_start_is_logged(caplog):
    setup = Setup(rooms_message(), cloud_payload(), FakeSender(TimeoutError()))
    setup.delay = DELAY
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await eventually(lambda: "cleaning command failed: TimeoutError" in caplog.text)
    assert setup.rooms.ids == []


async def test_a_tap_just_before_the_list_starts_joins_it(setup):
    setup.delay = DELAY * 8
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await asyncio.sleep(WINDOW + DELAY * 7)
    await setup.taps.tap(TapMode.ROOM, *RIGHT)
    await eventually(lambda: setup.sender.sent)
    await asyncio.sleep(DELAY * 10)
    assert segments(setup) == [[1, 2]]


async def test_two_rooms_tapped_inside_one_window_are_both_listed(setup):
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await setup.taps.tap(TapMode.ROOM, *RIGHT)
    await eventually(lambda: setup.rooms.ids == [1, 2])


async def test_start_takes_the_room_tapped_just_before(setup):
    await tap_room(setup, LEFT)
    await setup.taps.tap(TapMode.ROOM, *RIGHT)
    await setup.taps.start()
    await asyncio.sleep(WINDOW * 3)
    assert segments(setup) == [[1, 2]]
    assert setup.rooms.ids == []


async def test_clear_drops_the_room_tapped_just_before(setup):
    await tap_room(setup, LEFT)
    setup.delay = DELAY
    await setup.taps.tap(TapMode.ROOM, *RIGHT)
    setup.taps.clear()
    await asyncio.sleep(WINDOW + DELAY * 3)
    assert setup.rooms.ids == []
    assert setup.sender.sent == []


async def test_a_double_tap_restarts_the_delay_of_the_list(setup):
    setup.delay = DELAY * 4
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await asyncio.sleep(WINDOW + DELAY * 3)
    await setup.taps.tap(TapMode.ROOM, *RIGHT)
    await setup.taps.tap(TapMode.ROOM, *RIGHT)
    await asyncio.sleep(DELAY * 2)
    assert setup.sender.sent == []
    await eventually(lambda: setup.sender.sent)
    assert segments(setup) == [[1]]


async def test_an_area_tap_just_before_the_list_starts_sends_only_the_areas(setup):
    setup.delay = DELAY * 8
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await asyncio.sleep(WINDOW + DELAY * 7)
    await setup.taps.tap(TapMode.ROOM, *CATS)
    await eventually(lambda: setup.sender.sent)
    await asyncio.sleep(DELAY * 10)
    [cmds] = setup.targets()
    assert cmds[1]["data"] == {"cleanId": [-2], "extraAreas": [], "mapId": 42}
    assert setup.rooms.ids == []


async def test_a_room_listed_just_before_the_spot_starts_drops_the_spot(setup):
    setup.delay = DELAY * 8
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await asyncio.sleep(DELAY * 7)
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await eventually(lambda: setup.rooms.ids == [1])
    await asyncio.sleep(DELAY * 2)
    assert setup.sender.sent == []
    assert setup.spots.preview is None


async def test_a_setting_changed_while_a_tap_waits_leaves_the_list_held(setup):
    await tap_room(setup, LEFT)
    setup.taps.double_tap.window = DELAY * 4
    setup.delay = DELAY
    await setup.taps.tap(TapMode.ROOM, *RIGHT)
    setup.taps.reschedule()
    await asyncio.sleep(DELAY * 2)
    assert setup.sender.sent == []
    await eventually(lambda: setup.sender.sent)
    assert segments(setup) == [[1, 2]]


async def test_a_spot_preview_starts_after_a_double_tap(setup):
    setup.delay = DELAY * 4
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    assert [room.name for room in setup.selected] == ["Left"]
    await eventually(lambda: setup.sender.sent)
    assert setup.targets()[0][0]["data"]["extraAreas"]
    assert setup.spots.preview is None
