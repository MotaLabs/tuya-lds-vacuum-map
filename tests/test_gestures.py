import asyncio

import pytest

from support.maps import CODES, PROFILE, cell_center_mm, cloud_payload, rooms_message
from support.waiting import eventually
from tuya_lds_vacuum_map.gestures import DoubleTap, SpotPicker, SpotPreview, TapController, TapMode
from tuya_lds_vacuum_map.integrations.homeassistant import HomeAssistantError
from tuya_lds_vacuum_map.lds.codec import decode_raw
from tuya_lds_vacuum_map.map.coverage import Coverage
from tuya_lds_vacuum_map.map.model import MapModel
from tuya_lds_vacuum_map.robot import Robot

WINDOW = 0.05
EXPIRY = 0.1
LEFT = cell_center_mm(10, 14)
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
        self.spots = SpotPicker(self.robot, self.sender, 1500, EXPIRY, self.changed)
        self.taps = TapController(self.robot, self.map, self.sender, self.select, WINDOW, self.spots)

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


async def test_one_tap_cleans_the_room_after_the_window(setup):
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await asyncio.sleep(WINDOW / 5)
    assert setup.sender.sent == []
    await eventually(lambda: setup.sender.sent)
    [cmds] = setup.targets()
    assert cmds[0]["data"] == {"cleanId": [-3], "extraAreas": [], "mapId": 42, "segmentId": [1]}
    assert setup.selected == []


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


async def test_a_room_command_takes_the_preview_away(setup):
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    assert setup.spots.preview
    await eventually(lambda: setup.sender.sent)
    assert setup.spots.preview is None
    assert setup.changes == 1
    [cmds] = setup.targets()
    assert cmds[0]["data"]["segmentId"] == [1]


async def test_a_double_tap_keeps_the_preview(setup):
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await asyncio.sleep(WINDOW * 2)
    assert setup.spots.preview
    assert setup.changes == 0


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
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await eventually(lambda: "cleaning command failed: offline" in caplog.text)


async def test_a_command_that_times_out_is_logged(caplog):
    setup = Setup(rooms_message(), cloud_payload(), FakeSender(TimeoutError()))
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await eventually(lambda: "cleaning command failed: TimeoutError" in caplog.text)
