import asyncio
import base64
import colorsys
import dataclasses
import json
import logging
import random
import tempfile
import time

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from support.fakes import FakeCloud, FakeCloudSync
from support.harness import DEVICE, TIMING, Harness
from support.maps import CODES, PROFILE, build_cells, cell_center_mm, cloud_payload, rooms_message
from support.strategies import json_scalars, json_values, path_chunks, room_lists, spot_commands
from support.waiting import eventually, stop
from tuya_lds_vacuum_map.integrations.localtuya import DP_EVENT, STATUS_EVENT
from tuya_lds_vacuum_map.integrations.tuya_cloud import Reported, TuyaCloudError
from tuya_lds_vacuum_map.lds.codec import decode_raw, encode_name, encode_raw
from tuya_lds_vacuum_map.map.colors import format_color
from tuya_lds_vacuum_map.profiles import DEFAULT_PROFILE, Units
from tuya_lds_vacuum_map.session import ROOM_SETTINGS_EVENT
from tuya_lds_vacuum_map.storage import MapSource, Snapshot, Storage

DPS = PROFILE.data_points


@pytest.fixture
def harness(tmp_path) -> Harness:
    return Harness(tmp_path)


async def test_room_list_republishes_the_room_options(harness):
    await harness.dp(DPS.raw, encode_raw(rooms_message()))
    topic = f"homeassistant/select/tlvm_{DEVICE}_room_settings/config"
    await eventually(lambda: topic in harness.client.topics())
    config = json.loads(harness.client.last(topic))
    assert config["options"] == ["None", "Left", "Right"]
    assert harness.session().dirty.is_set()
    assert harness.session().unsaved


async def test_statistics_reach_their_sensors(harness):
    await harness.dp(DPS.clean_time, 480)
    await harness.dp(DPS.total_count, 950)
    await harness.dp(DPS.total_count, 950)
    topics = harness.session().topics
    assert harness.client.last(topics.statistic("clean_time")) == "480"
    assert harness.client.topics().count(topics.statistic("total_count")) == 1
    assert harness.client.last(topics.statistic("total_count")) == "950"
    harness.client.published.clear()
    await harness.session().on_connect()
    assert harness.client.last(topics.statistic("clean_time")) == "480"
    assert harness.client.last(topics.statistic("total_count")) == "950"


async def test_discovery_announces_a_sensor_per_statistic_of_the_profile(harness):
    await harness.session().publish_discovery()
    prefix = f"homeassistant/sensor/tlvm_{DEVICE}"
    sensors = sorted(topic for topic in harness.client.topics() if topic.startswith(prefix))
    assert sensors == [
        f"{prefix}_{name}/config" for name in ("clean_area", "clean_time", "total_area", "total_count", "total_time")
    ]
    total_time = json.loads(harness.client.last(f"{prefix}_total_time/config"))
    assert (total_time["name"], total_time["unit_of_measurement"]) == ("Total cleaning time", "s")
    area = json.loads(harness.client.last(f"{prefix}_clean_area/config"))
    assert (area["name"], area["unit_of_measurement"]) == ("Cleaned area", "m²")


async def test_the_statistics_take_their_units_from_the_profile(tmp_path):
    profile = dataclasses.replace(PROFILE, units=Units(time="min", area="ft²"))
    harness = Harness(tmp_path, profiles={DEFAULT_PROFILE: profile})
    await harness.session().publish_discovery()
    prefix = f"homeassistant/sensor/tlvm_{DEVICE}"
    units = {
        name: json.loads(harness.client.last(f"{prefix}_{name}/config")).get("unit_of_measurement")
        for name in ("clean_time", "total_time", "clean_area", "total_area", "total_count")
    }
    assert units == {
        "clean_time": "min",
        "total_time": "min",
        "clean_area": "ft²",
        "total_area": "ft²",
        "total_count": None,
    }


async def test_a_profile_without_statistics_announces_no_sensor(tmp_path):
    profile = dataclasses.replace(PROFILE, data_points=dataclasses.replace(PROFILE.data_points, total_count=None))
    harness = Harness(tmp_path, profiles={DEFAULT_PROFILE: profile})
    await harness.session().publish_discovery()
    assert not any(topic.endswith("_total_count/config") for topic in harness.client.topics())
    assert any(topic.endswith("_total_time/config") for topic in harness.client.topics())


async def test_events_from_other_devices_are_ignored(harness):
    await harness.app.on_ha_event(DP_EVENT, {"device_id": "other", "dp": DPS.state, "value": "totaling"})
    assert harness.session().robot.state is None
    assert not harness.session().dirty.is_set()


async def test_path_paints_the_coverage(harness):
    path = encode_raw({"infoType": 21011, "data": {"pathID": 1, "startPos": 0, "posArray": [[0, 0], [300, 0]]}})
    await harness.app.on_ha_event(STATUS_EVENT, {"device_id": DEVICE, "new_status": {str(DPS.path): path}})
    assert (6, 0) in harness.session().map.coverage.cells


async def test_cleaning_wakes_the_cloud_sync(harness):
    harness.session().cloud_sync = sync = FakeCloudSync()
    await harness.dp(DPS.state, "totaling")
    await harness.dp(DPS.state, "chargring")
    assert sync.calls == ["wake", "after_cleaning"]


async def test_catching_up_applies_the_robot_status_from_the_cloud(harness):
    session = harness.session()
    path = encode_raw({"infoType": 21011, "data": {"pathID": 1, "startPos": 0, "posArray": [[0, 0], [300, 0]]}})
    points = {
        DPS.state: Reported("totaling", 5),
        DPS.room_mode: Reported(True, 5),
        DPS.total_count: Reported(963, 5),
        DPS.raw: Reported(encode_raw(rooms_message()), 5),
        DPS.path: Reported(path, 5),
        999: Reported("x", 5),
    }
    session.cloud = cloud = FakeCloud(points)
    await session.catch_up_from_cloud()
    assert cloud.calls == [DEVICE]
    assert (session.robot.state, session.robot.room_mode, session.robot.rooms) == ("totaling", True, None)
    assert session.robot.path.position is None
    assert harness.client.last(session.topics.statistic("total_count")) == "963"
    assert session.reported_ms == {DPS.state: 5, DPS.room_mode: 5, DPS.total_count: 5}


async def test_a_cloud_value_replaces_only_an_older_local_one_or_none(harness):
    session = harness.session()
    await harness.dp(DPS.state, "totaling")
    await harness.dp(DPS.total_count, 964)
    local_ms = session.reported_ms[DPS.state]
    later = local_ms + 60_000
    session.cloud = FakeCloud(
        {
            DPS.state: Reported("chargring", local_ms - 1),
            DPS.total_count: Reported(965, later),
            DPS.room_mode: Reported(True, 0),
        }
    )
    await session.catch_up_from_cloud()
    assert session.robot.state == "totaling"
    assert session.robot.statistics["total_count"] == 965
    assert session.robot.room_mode is True
    assert session.reported_ms[DPS.total_count] == later
    session.cloud.points[DPS.total_count] = Reported(966, later)
    await session.catch_up_from_cloud()
    assert session.robot.statistics["total_count"] == 965


async def test_a_local_value_that_arrives_while_the_cloud_answers_is_kept(harness):
    session = harness.session()
    reported = int(time.time() * 1000) - 1000
    session.cloud = cloud = FakeCloud(
        {DPS.state: Reported("chargring", reported), DPS.total_count: Reported(963, reported)}
    )
    cloud.release = asyncio.Event()
    task = asyncio.create_task(session.catch_up_from_cloud())
    await eventually(lambda: cloud.calls == [DEVICE])
    await harness.dp(DPS.state, "totaling")
    cloud.release.set()
    await task
    assert session.robot.state == "totaling"
    assert session.robot.statistics["total_count"] == 963


async def test_a_local_data_point_counts_as_reported_when_it_arrives(harness):
    session = harness.session()
    before = int(time.time() * 1000)
    await harness.dp(DPS.state, "totaling")
    after = int(time.time() * 1000)
    assert before <= session.reported_ms[DPS.state] <= after


async def test_an_entity_back_from_unavailable_asks_for_a_catch_up(harness):
    session = harness.session()
    await session.on_available({"variables": {}})
    assert session._catch_up.is_set()


async def test_a_cloud_that_fails_leaves_the_robot_as_it_was(harness, caplog):
    session = harness.session()
    session.cloud = FakeCloud({DPS.state: Reported("totaling", 5)}, error=TuyaCloudError(1010, "token invalid"))
    await session.catch_up_from_cloud()
    assert session.robot.state is None
    assert session.reported_ms == {}
    assert "status is not available from the cloud: 1010: token invalid" in caplog.text


async def test_running_catches_up_each_time_it_is_asked_and_survives_an_error(harness, caplog):
    session = harness.session()
    session.cloud = cloud = FakeCloud(error=RuntimeError("unexpected"))
    task = asyncio.create_task(session.run())
    session.catch_up()
    await eventually(lambda: "could not catch up with the robot" in caplog.text)
    cloud.error = None
    cloud.points = {DPS.state: Reported("totaling", 5)}
    session.catch_up()
    await eventually(lambda: session.robot.state == "totaling")
    await stop(task)
    assert cloud.calls == [DEVICE, DEVICE]


async def test_local_only_never_asks_the_cloud_for_the_status(harness):
    session = harness.session()
    session.cloud = cloud = FakeCloud({DPS.state: Reported("totaling", 5)})
    await choose_the_local_map(harness)
    task = asyncio.create_task(session.run())
    session.catch_up()
    await asyncio.sleep(0.05)
    await stop(task)
    assert cloud.calls == []
    assert session.robot.state is None


async def test_without_a_cloud_nothing_catches_up(harness, caplog):
    session = harness.session()
    task = asyncio.create_task(session.run())
    session.catch_up()
    await asyncio.sleep(0.05)
    await stop(task)
    assert "catch up" not in caplog.text


def test_cloud_payload_replaces_the_map(harness):
    harness.session().on_cloud_payload({"data": {}})
    assert harness.session().map.cloud is None
    harness.session().on_cloud_payload(cloud_payload())
    assert harness.session().map.map_id == 42
    assert harness.session().dirty.is_set()


async def test_map_source_and_room_selects(harness):
    await harness.message(harness.session().topics.map_source_set, "Local only")
    assert harness.session().snapshot.source is MapSource.LOCAL
    assert harness.session().dirty.is_set()
    assert harness.client.last(harness.session().topics.map_source) == "Local only"
    await harness.message(harness.session().topics.room_settings_set, "Right")
    assert harness.client.last(harness.session().topics.room_settings) == "Right"
    await harness.message(harness.session().topics.room_settings_set, "None")
    assert harness.session().snapshot.selected_room is None
    await harness.message(harness.session().topics.map_source_set, harness.session().texts.source_auto)
    assert harness.session().snapshot.source is MapSource.AUTO
    assert harness.client.last(harness.session().topics.map_source) == harness.session().texts.source_auto


async def test_spot_tap_reaches_localtuya(harness):
    harness.session().on_cloud_payload(cloud_payload())
    await harness.tap(1500, -4750, "spot")
    scene = harness.session().scene()
    assert scene.preview_center == (1500, -4750)
    assert scene.preview_square == ((750, -5500), (2250, -5500), (2250, -4000), (750, -4000))
    assert harness.services == []
    await harness.tap(1500, -4750, "spot")
    assert harness.session().scene().preview_square is None
    [(domain, service, data)] = harness.services
    assert (domain, service, data["device_id"], data["dp"]) == ("localtuya", "set_dp", DEVICE, DPS.raw)
    assert decode_raw(data["value"])["data"]["cmds"][0]["data"]["extraAreas"][0]["mode"] == "point"


async def test_a_preview_that_goes_away_redraws_the_map(tmp_path):
    harness = Harness(tmp_path, timing=dataclasses.replace(TIMING, spot_preview=0.05))
    harness.session().on_cloud_payload(cloud_payload())
    await harness.message(harness.session().topics.auto_start_set, "OFF")
    await harness.tap(1500, -4750, "spot")
    harness.session().dirty.clear()
    await eventually(lambda: harness.session().dirty.is_set())
    assert harness.session().scene().preview_square is None


async def test_the_automatic_start_is_set_from_home_assistant(harness):
    session = harness.session()
    assert (session.snapshot.auto_start, session.snapshot.start_delay) == (True, 10.0)
    assert session.auto_start_delay() == 10.0
    await harness.message(session.topics.start_delay_set, "30")
    assert session.auto_start_delay() == 30.0
    assert harness.client.last(session.topics.start_delay) == "30"
    assert session.unsaved
    await harness.message(session.topics.auto_start_set, "OFF")
    assert session.auto_start_delay() is None
    assert harness.client.last(session.topics.auto_start) == "OFF"
    await harness.message(session.topics.auto_start_set, "ON")
    assert harness.client.last(session.topics.auto_start) == "ON"
    assert session.auto_start_delay() == 30.0


async def test_a_bad_automatic_start_setting_changes_nothing(harness, caplog):
    session = harness.session()
    await harness.message(session.topics.start_delay_set, "0")
    await harness.message(session.topics.auto_start_set, "on")
    assert (session.snapshot.auto_start, session.snapshot.start_delay) == (True, 10.0)
    assert not session.unsaved
    assert "start delay out of range" in caplog.text


async def test_turning_the_automatic_start_on_starts_a_waiting_preview(tmp_path):
    harness = Harness(tmp_path)
    session = harness.session()
    session.on_cloud_payload(cloud_payload())
    await harness.message(session.topics.auto_start_set, "OFF")
    await harness.tap(1500, -4750, "spot")
    session.snapshot.start_delay = 0.01
    await harness.message(session.topics.auto_start_set, "ON")
    await eventually(lambda: harness.services)
    assert session.spots.preview is None


async def test_a_tap_without_a_mode_lists_the_room(harness):
    harness.session().on_cloud_payload(cloud_payload())
    await harness.dp(DPS.raw, encode_raw(rooms_message()))
    harness.session().scene()
    x, y = cell_center_mm(30, 14)
    await harness.message(harness.session().topics.tap, json.dumps({"x": x, "y": y}))
    await eventually(lambda: harness.session().rooms.ids == [2])
    assert harness.services == []


async def room_list(harness: Harness, *points: tuple[int, int]) -> None:
    session = harness.session()
    session.on_cloud_payload(cloud_payload())
    await harness.dp(DPS.raw, encode_raw(rooms_message()))
    session.scene()
    for count, point in enumerate(points, 1):
        await harness.tap(*point)
        await eventually(lambda count=count: len(session.rooms.ids) == count)


def segments(harness: Harness) -> list[list[int]]:
    return [decode_raw(data["value"])["data"]["cmds"][0]["data"]["segmentId"] for _, _, data in harness.services]


async def test_discovery_announces_the_buttons_and_retires_the_room_tap(tmp_path):
    harness = Harness(tmp_path, language="pt-BR")
    await harness.session().publish_discovery()
    prefix = f"homeassistant/{{}}/tlvm_{DEVICE}_{{}}/config"
    assert harness.client.last(prefix.format("select", "room_tap")) == ""
    start = json.loads(harness.client.last(prefix.format("button", "start")))
    clear = json.loads(harness.client.last(prefix.format("button", "clear")))
    assert (start["name"], clear["name"]) == ("Iniciar seleção", "Descartar seleção")


async def test_a_room_list_is_numbered_on_the_map_and_started_from_home_assistant(harness):
    await harness.message(harness.session().topics.auto_start_set, "OFF")
    await room_list(harness, cell_center_mm(30, 14), cell_center_mm(10, 14))
    assert harness.session().scene().room_order == {2: 1, 1: 2}
    assert harness.session().dirty.is_set()
    assert harness.services == []
    await harness.message(harness.session().topics.start, "PRESS")
    assert segments(harness) == [[2, 1]]
    assert harness.session().scene().room_order == {}


async def test_the_clear_button_drops_the_list(harness):
    await harness.message(harness.session().topics.auto_start_set, "OFF")
    await room_list(harness, cell_center_mm(30, 14))
    await harness.message(harness.session().topics.clear, "PRESS")
    assert harness.session().scene().room_order == {}
    await harness.message(harness.session().topics.start, "PRESS")
    assert harness.services == []


def rooms_of_map(map_id: int) -> dict:
    message = rooms_message()
    message["data"]["mapId"] = map_id
    return message


async def test_a_room_list_of_another_map_drops_the_waiting_list(harness, caplog):
    caplog.set_level(logging.INFO, logger="tuya_lds_vacuum_map.gestures")
    session = harness.session()
    await harness.message(session.topics.auto_start_set, "OFF")
    await room_list(harness, cell_center_mm(30, 14))
    await harness.dp(DPS.raw, encode_raw(rooms_of_map(7)))
    assert session.rooms.ids == []
    assert session.scene().room_order == {}
    assert "the map changed from 42 to 7, so any waiting pick is dropped" in caplog.text
    await harness.message(session.topics.start, "PRESS")
    assert harness.services == []


async def test_a_cloud_map_of_another_map_drops_the_waiting_spot(harness):
    session = harness.session()
    await harness.message(session.topics.auto_start_set, "OFF")
    session.on_cloud_payload(cloud_payload())
    await harness.tap(1500, -4750, "spot")
    assert session.spots.preview is not None
    session.on_cloud_payload(cloud_payload(map_id=7))
    assert session.spots.preview is None
    assert session.scene().preview_square is None
    await harness.message(session.topics.start, "PRESS")
    assert harness.services == []


async def test_the_same_map_again_keeps_the_waiting_pick(harness):
    session = harness.session()
    await harness.message(session.topics.auto_start_set, "OFF")
    await room_list(harness, cell_center_mm(30, 14))
    renamed = rooms_message()
    renamed["data"]["autoAreaValue"][0]["name"] = encode_name("Hall")
    await harness.dp(DPS.raw, encode_raw(renamed))
    cells = build_cells()
    cells[0] = CODES.wall
    session.on_cloud_payload(cloud_payload(cells, map_id=42))
    assert session.map.cloud.cells[0] == CODES.wall
    assert session.rooms.ids == [2]
    await harness.message(session.topics.start, "PRESS")
    assert segments(harness) == [[2]]


async def test_a_tap_still_waiting_for_a_double_tap_is_dropped_with_the_old_map(harness):
    session = harness.session()
    await harness.message(session.topics.auto_start_set, "OFF")
    session.on_cloud_payload(cloud_payload())
    await harness.dp(DPS.raw, encode_raw(rooms_message()))
    session.scene()
    await harness.tap(*cell_center_mm(30, 14))
    await harness.dp(DPS.raw, encode_raw(rooms_of_map(7)))
    await asyncio.sleep(TIMING.double_tap * 3)
    assert session.rooms.ids == []


async def test_a_new_map_after_a_restart_drops_a_list_picked_on_the_saved_one(tmp_path):
    first = Harness(tmp_path)
    first.session().on_cloud_payload(cloud_payload())
    await first.dp(DPS.raw, encode_raw(rooms_message()))
    await first.message(first.session().topics.auto_start_set, "OFF")
    first.session().save()
    second = Harness(tmp_path)
    session = second.session()
    session.scene()
    await second.tap(*cell_center_mm(30, 14))
    await eventually(lambda: session.rooms.ids == [2])
    await second.dp(DPS.raw, encode_raw(rooms_of_map(7)))
    assert session.rooms.ids == []


async def test_turning_the_automatic_start_on_starts_a_waiting_list(harness):
    session = harness.session()
    await harness.message(session.topics.auto_start_set, "OFF")
    await room_list(harness, cell_center_mm(10, 14))
    session.snapshot.start_delay = 0.01
    await harness.message(session.topics.auto_start_set, "ON")
    await eventually(lambda: harness.services)
    assert segments(harness) == [[1]]


async def test_a_setting_changed_while_a_tap_waits_applies_after_it(tmp_path):
    harness = Harness(tmp_path, timing=dataclasses.replace(TIMING, double_tap=0.3))
    session = harness.session()
    await harness.message(session.topics.auto_start_set, "OFF")
    session.on_cloud_payload(cloud_payload())
    await harness.dp(DPS.raw, encode_raw(rooms_message()))
    session.scene()
    await harness.tap(*cell_center_mm(10, 14))
    await eventually(lambda: session.rooms.ids == [1])
    await harness.tap(*cell_center_mm(30, 14))
    session.snapshot.start_delay = 0.01
    await harness.message(session.topics.auto_start_set, "ON")
    await asyncio.sleep(0.1)
    assert harness.services == []
    await eventually(lambda: harness.services)
    assert segments(harness) == [[1, 2]]


async def test_double_tap_opens_the_room_settings(harness):
    harness.session().on_cloud_payload(cloud_payload())
    await harness.dp(DPS.raw, encode_raw(rooms_message()))
    harness.session().scene()
    await harness.tap(*cell_center_mm(30, 14))
    await harness.tap(*cell_center_mm(30, 14))
    assert harness.events == [(ROOM_SETTINGS_EVENT, {"device_id": DEVICE, "room_id": 2, "room_name": "Right"})]
    assert harness.client.last(harness.session().topics.room_settings) == "Right"
    await asyncio.sleep(0.15)
    assert harness.services == []


async def test_bad_tap_payload_is_logged(harness, caplog):
    await harness.message(harness.session().topics.tap, "{not json")
    assert "could not handle" in caplog.text


async def test_scene_needs_a_map(harness):
    assert harness.session().scene() is None
    harness.session().on_cloud_payload(cloud_payload())
    await harness.dp(DPS.raw, encode_raw(rooms_message()))
    assert not harness.session().scene().show_settings
    await harness.dp(DPS.room_mode, True)
    scene = harness.session().scene()
    assert scene.show_settings
    assert [room.name for room in scene.rooms] == ["Left", "Right"]
    assert scene.dock == (0, 0)


async def test_render_loop_publishes_the_map(harness):
    harness.session().on_cloud_payload(cloud_payload())
    task = asyncio.create_task(harness.session().render_loop())
    await eventually(lambda: harness.session().topics.map_attributes in harness.client.topics())
    await stop(task)
    assert harness.client.last(harness.session().topics.map).startswith(b"\x89PNG")
    attributes = json.loads(harness.client.last(harness.session().topics.map_attributes))
    assert len(attributes["calibration_points"]) == 3
    assert attributes["map_source"] == "auto"


async def test_running_a_session_draws_saves_and_syncs_the_cloud_map(harness, tmp_path):
    session = harness.session()
    session.cloud_sync = sync = FakeCloudSync()
    session.on_cloud_payload(cloud_payload())
    task = asyncio.create_task(session.run())
    await eventually(
        lambda: (
            session.topics.map in harness.client.topics()
            and (tmp_path / DEVICE / "state.json").exists()
            and "run" in sync.calls
        )
    )
    await stop(task)


async def choose_the_local_map(harness: Harness) -> None:
    await harness.message(harness.session().topics.map_source_set, "Local only")


async def choose_the_left_room(harness: Harness) -> None:
    await harness.message(harness.session().topics.room_settings_set, "Left")


async def color_the_room(harness: Harness) -> None:
    await harness.message(harness.session().topics.room_color_set, "#123456")


async def color_the_room_at_random(harness: Harness) -> None:
    await harness.message(harness.session().topics.room_color_random, "")


async def open_the_room_settings(harness: Harness) -> None:
    session = harness.session()
    await session.open_room_settings(session.robot.rooms.rooms[0])


@pytest.mark.parametrize(
    "change",
    [choose_the_local_map, choose_the_left_room, color_the_room, color_the_room_at_random, open_the_room_settings],
)
async def test_the_save_loop_writes_each_change_once(harness, change):
    await select_right(harness)
    session = harness.session()
    session.save()
    writes = []
    session.storage.save = writes.append
    task = asyncio.create_task(session.save_loop())
    await change(harness)
    await eventually(lambda: writes)
    await asyncio.sleep(0.05)
    await stop(task)
    assert len(writes) == 1


async def test_a_state_that_cannot_be_saved_is_logged_and_saved_later(tmp_path, caplog):
    harness = Harness(tmp_path)
    session = harness.session()
    session.unsaved = True
    (tmp_path / DEVICE).write_text("")
    session.save()
    assert session.unsaved
    assert "the state could not be saved" in caplog.text
    (tmp_path / DEVICE).unlink()
    session.save()
    assert not session.unsaved
    assert session.storage.path.exists()


async def test_a_state_too_deep_to_save_is_logged_and_the_save_loop_keeps_going(harness, caplog):
    message = json.dumps(rooms_message())[:-1] + ', "extra": ' + "[" * 2000 + "]" * 2000 + "}"
    await harness.dp(DPS.raw, base64.b64encode(message.encode()).decode())
    session = harness.session()
    session.save()
    assert session.unsaved
    assert "the state could not be saved" in caplog.text
    assert "RecursionError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    assert not session.storage.path.exists()


async def test_state_survives_a_restart(tmp_path):
    first = Harness(tmp_path)
    first.session().on_cloud_payload(cloud_payload())
    await first.dp(DPS.raw, encode_raw(rooms_message()))
    await first.dp(DPS.state, "totaling")
    await first.dp(
        DPS.path, encode_raw({"infoType": 21011, "data": {"pathID": 1, "startPos": 0, "posArray": [[0, 0]]}})
    )
    await first.message(first.session().topics.map_source_set, "Local only")
    await first.dp(DPS.room_mode, True)
    await first.message(first.session().topics.room_settings_set, "Right")
    await first.message(first.session().topics.auto_start_set, "OFF")
    await first.message(first.session().topics.start_delay_set, "45")
    first.session().save()
    second = Harness(tmp_path).session()
    assert (second.snapshot.auto_start, second.snapshot.start_delay) == (False, 45.0)
    assert second.robot.rooms.names == ["Left", "Right"]
    assert (second.robot.room_mode, second.snapshot.selected_room) == (True, "Right")
    assert (second.robot.state, second.snapshot.source, second.map.map_id) == ("totaling", MapSource.LOCAL, 42)
    assert second.map.coverage.cells == first.session().map.coverage.cells


def test_level_labels_follow_the_profile_and_language(tmp_path):
    english = Harness(tmp_path).session()
    assert english.fan_labels == {"quiet": "Quiet", "auto": "Standard", "strong": "Strong", "max": "Max", "mop": "Mop"}
    assert english.water_labels == {1: "Low", 2: "Medium", 3: "High"}
    assert Harness(tmp_path, language="pt-BR").session().fan_labels["quiet"] == "Eco"


def test_device_info(harness):
    info = harness.session().device_info()
    assert (info["model"], info["name"]) == ("lds-robot", "Vacuum map 123456")


async def select_right(harness: Harness) -> None:
    await harness.dp(DPS.raw, encode_raw(rooms_message()))
    await harness.message(harness.session().topics.room_settings_set, "Right")


async def test_every_room_gets_its_own_color(harness):
    await harness.dp(DPS.raw, encode_raw(rooms_message()))
    colors = harness.session().room_colors
    assert set(colors) == {1, 2}
    assert colors[1] != colors[2]
    harness.session().on_cloud_payload(cloud_payload())
    assert harness.session().scene().room_colors == colors


async def test_a_room_without_a_name_gets_a_color(harness):
    message = rooms_message()
    message["data"]["autoAreaValue"][0]["name"] = ""
    await harness.dp(DPS.raw, encode_raw(message))
    assert set(harness.session().room_colors) == {1, 2}


async def test_room_color_follows_the_selected_room(harness):
    await select_right(harness)
    session = harness.session()
    assert harness.client.last(session.topics.room_color) == format_color(session.room_colors[2])
    await harness.message(session.topics.room_settings_set, "None")
    assert harness.client.last(session.topics.room_color) == ""


async def test_the_selected_room_can_be_recolored(harness):
    await select_right(harness)
    session = harness.session()
    left = session.room_colors[1]
    session.dirty.clear()
    await harness.message(session.topics.room_color_set, "#123456")
    assert session.room_colors == {1: left, 2: (0x12, 0x34, 0x56)}
    assert harness.client.last(session.topics.room_color) == "#123456"
    assert session.dirty.is_set()
    await harness.message(session.topics.room_color_random, "PRESS")
    assert session.room_colors[2] != (0x12, 0x34, 0x56)
    assert session.room_colors[1] == left


async def test_a_random_color_moves_away_from_the_current_one(harness):
    await select_right(harness)
    session = harness.session()
    for seed in range(10):
        session.rng = random.Random(seed)
        before = hue(session.room_colors[2])
        await harness.message(session.topics.room_color_random, "PRESS")
        after = hue(session.room_colors[2])
        assert min(abs(after - before), 1 - abs(after - before)) > 0.1


async def test_bad_colors_and_no_selection_change_nothing(harness, caplog):
    await harness.dp(DPS.raw, encode_raw(rooms_message()))
    session = harness.session()
    before = dict(session.room_colors)
    await harness.message(session.topics.room_color_random, "PRESS")
    await select_right(harness)
    await harness.message(session.topics.room_color_set, "blue")
    assert session.room_colors == before
    assert "no room selected" in caplog.text
    assert "#rrggbb" in caplog.text


async def test_colors_survive_a_restart(tmp_path):
    first = Harness(tmp_path)
    await select_right(first)
    await first.message(first.session().topics.room_color_set, "#123456")
    first.session().save()
    second = Harness(tmp_path).session()
    assert second.room_colors == first.session().room_colors
    assert second.room_colors[2] == (0x12, 0x34, 0x56)


@pytest.mark.parametrize(("auto_start", "delay"), [("yes", 0), (1, "soon"), (None, 10**400)])
def test_unreadable_saved_start_settings_are_replaced(tmp_path, caplog, auto_start, delay):
    Storage(tmp_path / DEVICE / "state.json").save(Snapshot(auto_start=auto_start, start_delay=delay))
    session = Harness(tmp_path).session()
    assert (session.snapshot.auto_start, session.snapshot.start_delay) == (True, 10.0)
    assert "ignoring saved automatic start" in caplog.text
    assert "ignoring saved start delay" in caplog.text


def test_saved_start_settings_in_range_are_kept(tmp_path):
    Storage(tmp_path / DEVICE / "state.json").save(Snapshot(auto_start=False, start_delay=300))
    session = Harness(tmp_path).session()
    assert (session.snapshot.auto_start, session.snapshot.start_delay) == (False, 300.0)


def test_unreadable_saved_colors_are_replaced(tmp_path, caplog):
    Storage(tmp_path / DEVICE / "state.json").save(
        Snapshot(room_message=rooms_message(), room_colors={"1": "blue", "2": "#123456"})
    )
    session = Harness(tmp_path).session()
    assert session.room_colors[2] == (0x12, 0x34, 0x56)
    assert session.room_colors[1] != (0x12, 0x34, 0x56)
    assert "ignoring saved color" in caplog.text
    assert session.unsaved


def path_chunk(start: int, points: list[list[int]]) -> str:
    return encode_raw({"infoType": 21011, "data": {"pathID": 1, "startPos": start, "posArray": points}})


async def test_map_attributes_follow_the_robot(harness):
    session = harness.session()
    session.on_cloud_payload(cloud_payload())
    await harness.dp(DPS.path, path_chunk(0, [[0, 0], [100, 200]]))
    await session.redraw()
    first = json.loads(harness.client.last(session.topics.map_attributes))
    await harness.dp(DPS.path, path_chunk(2, [[300, 200]]))
    await session.redraw()
    second = json.loads(harness.client.last(session.topics.map_attributes))
    assert (first["robot_position"], second["robot_position"]) == ({"x": 100, "y": 200}, {"x": 300, "y": 200})


async def test_map_attributes_change_with_the_image(harness):
    session = harness.session()
    await harness.message(session.topics.auto_start_set, "OFF")
    await room_list(harness, cell_center_mm(30, 14), cell_center_mm(10, 14))
    await session.redraw()
    listed = json.loads(harness.client.last(session.topics.map_attributes))
    await session.redraw()
    again = json.loads(harness.client.last(session.topics.map_attributes))
    session.rooms.clear()
    session.rooms.toggle(42, 1)
    session.rooms.toggle(42, 2)
    await session.redraw()
    reordered = json.loads(harness.client.last(session.topics.map_attributes))
    assert again == listed
    assert reordered.pop("image_digest") != listed.pop("image_digest")
    assert reordered == listed


async def test_map_attributes_tell_what_is_selected(harness):
    session = harness.session()
    await harness.message(session.topics.auto_start_set, "OFF")
    await room_list(harness, cell_center_mm(30, 14))
    await session.redraw()
    assert json.loads(harness.client.last(session.topics.map_attributes))["selection"] == "rooms"
    await harness.message(session.topics.tap, json.dumps({"mode": "spot", "x": 0, "y": 0}))
    await session.redraw()
    assert json.loads(harness.client.last(session.topics.map_attributes))["selection"] == "spot"
    await harness.message(session.topics.clear, "PRESS")
    await session.redraw()
    assert json.loads(harness.client.last(session.topics.map_attributes))["selection"] == "none"


async def test_map_attributes_have_no_position_before_the_path(harness):
    harness.session().on_cloud_payload(cloud_payload())
    await harness.session().redraw()
    assert json.loads(harness.client.last(harness.session().topics.map_attributes))["robot_position"] is None


async def test_map_attributes_name_the_robot_state(harness):
    harness.session().on_cloud_payload(cloud_payload())
    await harness.session().redraw()
    assert json.loads(harness.client.last(harness.session().topics.map_attributes))["robot_state_name"] is None
    await harness.dp(DPS.state, "dustcenterworking")
    await harness.session().redraw()
    attributes = json.loads(harness.client.last(harness.session().topics.map_attributes))
    assert (attributes["robot_state"], attributes["robot_state_name"]) == ("dustcenterworking", "Emptying the bin")
    await harness.dp(DPS.state, "somersault")
    await harness.session().redraw()
    assert json.loads(harness.client.last(harness.session().topics.map_attributes))["robot_state_name"] == "somersault"


def test_robot_state_names_follow_the_language(tmp_path):
    assert Harness(tmp_path, language="pt-BR").session().state_labels["chargring"] == "Carregando"


def test_a_state_name_with_no_text_shows_the_code(tmp_path):
    names = {**PROFILE.states.names, "stairs": "climbing"}
    profile = dataclasses.replace(PROFILE, states=dataclasses.replace(PROFILE.states, names=names))
    labels = Harness(tmp_path, profiles={DEFAULT_PROFILE: profile}).session().state_labels
    assert (labels["stairs"], labels["mop"]) == ("stairs", "Mopping")


async def test_map_attributes_list_room_colors(harness):
    harness.session().on_cloud_payload(cloud_payload())
    await harness.dp(DPS.raw, encode_raw(rooms_message()))
    task = asyncio.create_task(harness.session().render_loop())
    await eventually(lambda: harness.session().topics.map_attributes in harness.client.topics())
    await stop(task)
    rooms = json.loads(harness.client.last(harness.session().topics.map_attributes))["rooms"]
    assert rooms == [
        {"id": 1, "name": "Left", "color": format_color(harness.session().room_colors[1])},
        {"id": 2, "name": "Right", "color": format_color(harness.session().room_colors[2])},
    ]


def hue(color: tuple[int, int, int]) -> float:
    return colorsys.rgb_to_hls(*(channel / 255 for channel in color))[0]


TOPIC_KINDS = (
    "tap",
    "map_source_set",
    "room_settings_set",
    "room_color_set",
    "room_color_random",
    "auto_start_set",
    "start_delay_set",
    "start",
    "clear",
)
taps = st.fixed_dictionaries({"x": json_scalars, "y": json_scalars}, optional={"mode": json_scalars})


async def handle(kind: str, payload: str) -> None:
    with tempfile.TemporaryDirectory() as folder:
        harness = Harness(folder)
        session = harness.session()
        session.on_cloud_payload(cloud_payload())
        await harness.dp(DPS.raw, encode_raw(rooms_message()))
        await harness.message(session.topics.room_settings_set, "Right")
        await harness.message(getattr(session.topics, kind), payload)


@given(st.sampled_from(TOPIC_KINDS), st.text() | json_values.map(json.dumps) | taps.map(json.dumps))
def test_no_mqtt_payload_breaks_the_session(kind, payload):
    asyncio.run(handle(kind, payload))


async def test_a_tap_out_of_range_is_logged(harness, caplog):
    harness.session().on_cloud_payload(cloud_payload())
    await harness.tap(10**9, 0, "spot")
    await harness.message(harness.session().topics.tap, "[1, 2]")
    assert harness.services == []
    assert "point out of range" in caplog.text
    assert "not a number: None" in caplog.text


async def test_a_failed_drawing_is_logged_and_the_next_one_still_runs(harness, caplog):
    session = harness.session()
    session.on_cloud_payload(cloud_payload())
    render = session.renderer.render
    calls = []

    def flaky(scene):
        calls.append(scene)
        if len(calls) == 1:
            raise RuntimeError("broken font")
        return render(scene)

    session.renderer.render = flaky
    task = asyncio.create_task(session.render_loop())
    await eventually(lambda: "RuntimeError: broken font" in caplog.text)
    session.dirty.set()
    await eventually(lambda: session.topics.map in harness.client.topics())
    await stop(task)


accepted = st.lists(
    st.tuples(st.just(DPS.raw), room_lists | spot_commands) | st.tuples(st.just(DPS.path), path_chunks),
    max_size=4,
)


async def draw(updates: list[tuple[int, dict]]) -> list[str]:
    with tempfile.TemporaryDirectory() as folder:
        harness = Harness(folder)
        session = harness.session()
        session.on_cloud_payload(cloud_payload())
        await session.on_data_points({DPS.room_mode: True})
        for dp, message in updates:
            await session.on_data_points({dp: encode_raw(message)})
        await session.redraw()
        return [session.topics.map, *harness.client.topics()]


@given(accepted)
@example(
    [(DPS.raw, {"infoType": 21004, "data": {"autoAreaValue": [{"id": 0, "name": "1s4", "pos": [1, 0]}], "value": []}})]
)
def test_whatever_the_robot_accepts_can_be_drawn(updates):
    topic, *published = asyncio.run(draw(updates))
    assert topic in published
