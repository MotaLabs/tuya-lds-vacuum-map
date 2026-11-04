import asyncio
import colorsys
import json
import random
import tempfile

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from support.fakes import FakeCloudSync
from support.harness import DEVICE, Harness
from support.maps import PROFILE, cell_center_mm, cloud_payload, rooms_message
from support.strategies import json_scalars, json_values, path_chunks, room_lists, spot_commands
from support.waiting import eventually, stop
from tuya_lds_vacuum_map.integrations.localtuya import DP_EVENT, STATUS_EVENT
from tuya_lds_vacuum_map.lds.codec import decode_raw, encode_raw
from tuya_lds_vacuum_map.map.colors import format_color
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
    [(domain, service, data)] = harness.services
    assert (domain, service, data["device_id"], data["dp"]) == ("localtuya", "set_dp", DEVICE, DPS.raw)
    assert decode_raw(data["value"])["data"]["cmds"][0]["data"]["extraAreas"][0]["mode"] == "point"


async def test_a_tap_without_a_mode_cleans_the_room(harness):
    harness.session().on_cloud_payload(cloud_payload())
    await harness.dp(DPS.raw, encode_raw(rooms_message()))
    harness.session().scene()
    x, y = cell_center_mm(30, 14)
    await harness.message(harness.session().topics.tap, json.dumps({"x": x, "y": y}))
    await eventually(lambda: harness.services)
    [(_, _, data)] = harness.services
    assert decode_raw(data["value"])["data"]["cmds"][0]["data"]["segmentId"] == [2]


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
    first.session().save()
    second = Harness(tmp_path).session()
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


async def test_map_attributes_have_no_position_before_the_path(harness):
    harness.session().on_cloud_payload(cloud_payload())
    await harness.session().redraw()
    assert json.loads(harness.client.last(harness.session().topics.map_attributes))["robot_position"] is None


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


TOPIC_KINDS = ("tap", "map_source_set", "room_settings_set", "room_color_set", "room_color_random")
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
