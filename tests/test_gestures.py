import asyncio

import pytest

from conftest import CODES, PROFILE, cell_center_mm, cloud_payload, rooms_message
from tuya_lds_vacuum_map.gestures import DoubleTap, TapController, TapMode
from tuya_lds_vacuum_map.integrations.homeassistant import HomeAssistantError
from tuya_lds_vacuum_map.lds.codec import decode_raw
from tuya_lds_vacuum_map.map.coverage import Coverage
from tuya_lds_vacuum_map.map.model import MapModel
from tuya_lds_vacuum_map.robot import Robot

WINDOW = 0.05
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
        self.taps = TapController(self.robot, self.map, self.sender, self.select, WINDOW, 1500)

    async def select(self, room) -> None:
        self.selected.append(room)

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
    await asyncio.sleep(WINDOW * 3)
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
    await asyncio.sleep(WINDOW * 3)
    [cmds] = setup.targets()
    assert cmds[0]["infoType"] == 21003
    assert cmds[0]["data"]["value"] == rooms_message()["data"]["value"]
    assert cmds[1]["data"]["cleanId"] == [-2]


async def test_spot_is_sent_at_once_and_shown(setup):
    await setup.taps.tap(TapMode.ROOM, *LEFT)
    await setup.taps.tap(TapMode.SPOT, 0, 0)
    [cmds] = setup.targets()
    assert cmds[0]["data"]["extraAreas"][0]["vertexs"] == [[-750, -750], [750, -750], [750, 750], [-750, 750]]
    assert setup.robot.spot == ((-750, -750), (750, -750), (750, 750), (-750, 750))
    await asyncio.sleep(WINDOW * 3)
    assert len(setup.sender.sent) == 1


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
    await asyncio.sleep(WINDOW * 3)
    assert "offline" in caplog.text
