import dataclasses

from conftest import PROFILE, rooms_message
from tuya_lds_vacuum_map.lds import commands
from tuya_lds_vacuum_map.lds.codec import encode_name, encode_raw
from tuya_lds_vacuum_map.profiles import DataPoints
from tuya_lds_vacuum_map.robot import Robot

DPS = PROFILE.data_points


def path_value(path_id: int, start: int, points: list[list[int]]) -> str:
    return encode_raw({"infoType": 21011, "data": {"pathID": path_id, "startPos": start, "posArray": points}})


def test_state_changes_report_start_and_stop():
    robot = Robot(PROFILE, state="chargring")
    started = robot.apply({DPS.state: "totaling"})
    assert (started.started_moving, started.stopped_moving, started.redraw) == (True, False, True)
    assert robot.moving
    assert not robot.apply({DPS.state: "sweep"}).started_moving
    stopped = robot.apply({DPS.state: "tocharge"})
    assert not stopped.stopped_moving
    stopped = robot.apply({DPS.state: "chargring"})
    assert (stopped.started_moving, stopped.stopped_moving) == (False, True)


def test_path_points_are_reported():
    robot = Robot(PROFILE)
    update = robot.apply({DPS.path: path_value(1, 0, [[0, 0], [100, 0]])})
    assert update.new_path == [(0, 0), (100, 0)]
    assert robot.path.position == (100, 0)


def test_room_list_updates_rooms_and_reports_renames():
    robot = Robot(PROFILE)
    first = robot.apply({DPS.raw: encode_raw(rooms_message())})
    assert first.rooms_renamed
    assert first.redraw
    assert robot.rooms.names == ["Left", "Right"]
    assert robot.room_message == rooms_message()
    assert not robot.apply({DPS.raw: encode_raw(rooms_message())}).rooms_renamed
    renamed = rooms_message()
    renamed["data"]["autoAreaValue"][0]["name"] = encode_name("Office")
    assert robot.apply({DPS.raw: encode_raw(renamed)}).rooms_renamed


def test_spot_command_is_kept_until_the_spot_clean_ends():
    robot = Robot(PROFILE, state="standby")
    robot.apply({DPS.raw: commands.clean_spot(42, 0, 0, 1000, now_ms=1)})
    assert robot.spot == ((-500, -500), (500, -500), (500, 500), (-500, 500))
    robot.apply({DPS.state: "pointing"})
    robot.apply({DPS.state: "curpointing"})
    assert robot.spot is not None
    robot.apply({DPS.state: "tocharge"})
    assert robot.spot is None


def test_room_command_does_not_touch_the_spot():
    robot = Robot(PROFILE)
    update = robot.apply({DPS.raw: commands.clean_rooms(42, [1], now_ms=1)})
    assert robot.spot is None
    assert not update.redraw


def test_room_mode_switch():
    robot = Robot(PROFILE)
    assert robot.apply({DPS.room_mode: True}).redraw
    assert robot.room_mode is True


def test_bad_values_are_skipped():
    robot = Robot(PROFILE)
    update = robot.apply({DPS.raw: "not base64 json", DPS.state: 3, 999: "x"})
    assert not update.redraw
    assert robot.rooms is None
    assert robot.state is None


def test_data_points_come_from_the_profile():
    profile = dataclasses.replace(PROFILE, data_points=DataPoints(state=1, path=2, raw=3))
    robot = Robot(profile)
    robot.apply({1: "totaling", 3: encode_raw(rooms_message()), DPS.state: "chargring"})
    assert robot.state == "totaling"
    assert robot.rooms.names == ["Left", "Right"]


def test_saved_state_is_restored():
    robot = Robot(PROFILE, rooms_message(), "totaling", True)
    assert robot.rooms.map_id == 42
    assert robot.moving
    assert robot.room_mode
