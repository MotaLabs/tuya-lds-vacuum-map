import asyncio
import dataclasses
import json

import pytest

from support.harness import DEVICE, Harness
from support.maps import PROFILE, cloud_payload, rooms_message
from tuya_lds_vacuum_map.config import RobotSettings
from tuya_lds_vacuum_map.lds.codec import decode_raw, encode_raw
from tuya_lds_vacuum_map.profiles import DEFAULT_PROFILE, DataPoints
from tuya_lds_vacuum_map.storage import Storage

OTHER = "other987654"
CUSTOM = dataclasses.replace(PROFILE, name="custom", data_points=DataPoints(state=1, path=2, raw=200))
DPS = PROFILE.data_points


@pytest.fixture
def harness(tmp_path) -> Harness:
    robots = (RobotSettings(DEVICE, "Hall"), RobotSettings(OTHER, "Upstairs", "custom.toml"))
    return Harness(tmp_path, robots, profiles={DEFAULT_PROFILE: PROFILE, "custom.toml": CUSTOM})


async def test_events_reach_only_their_robot(harness):
    await harness.dp(DPS.state, "totaling")
    await harness.dp(DPS.state, "sweep", device_id="unknown")
    assert harness.session().robot.state == "totaling"
    assert harness.session(OTHER).robot.state is None


async def test_each_robot_reads_its_own_profile(harness):
    await harness.dp(DPS.raw, encode_raw(rooms_message()), device_id=OTHER)
    assert harness.session(OTHER).robot.rooms is None
    await harness.dp(200, encode_raw(rooms_message()), device_id=OTHER)
    assert harness.session(OTHER).robot.rooms.names == ["Left", "Right"]
    assert harness.session().robot.rooms is None


async def test_taps_go_to_the_robot_of_the_topic(harness):
    harness.session(OTHER).on_cloud_payload(cloud_payload())
    await harness.tap(0, 0, "spot", device_id=OTHER)
    await harness.tap(0, 0, "spot")
    [(_, _, data)] = harness.services
    assert (data["device_id"], data["dp"]) == (OTHER, 200)
    assert decode_raw(data["value"])["infoType"] == 30000


async def test_unknown_topics_are_ignored(harness):
    await harness.message("tuya_lds_vacuum_map/unknown/tap", json.dumps({"x": 0, "y": 0, "mode": "spot"}))
    assert harness.services == []


def test_every_inbound_topic_is_routed(harness):
    assert set(harness.app.routes) == {
        topic for device in (DEVICE, OTHER) for topic in harness.session(device).topics.inbound
    }


async def test_connect_announces_every_robot_with_one_availability(harness):
    await harness.app.on_mqtt_connect()
    configs = [json.loads(payload) for topic, payload in harness.client.published if topic.endswith("/config")]
    assert {config["device"]["name"] for config in configs} == {"Hall", "Upstairs"}
    assert {config["availability_topic"] for config in configs} == {"tuya_lds_vacuum_map/availability"}
    assert {config["device"]["model"] for config in configs} == {"lds-robot", "custom"}


async def test_robots_keep_separate_state(harness, tmp_path):
    await harness.dp(DPS.state, "totaling")
    await harness.dp(1, "chargring", device_id=OTHER)
    for session in harness.app.sessions.values():
        session.save()
    assert Storage(tmp_path / DEVICE / "state.json").load().robot_state == "totaling"
    assert Storage(tmp_path / OTHER / "state.json").load().robot_state == "chargring"


async def test_stopping_saves_every_robot(harness, tmp_path):
    async def broken(*_) -> None:
        raise RuntimeError("websocket gone")

    async def idle(*_) -> None:
        await asyncio.Event().wait()

    harness.app.ha.run = broken
    harness.app.mqtt.run = idle
    await harness.dp(DPS.state, "totaling")
    with pytest.raises(ExceptionGroup):
        await harness.app.run()
    assert (tmp_path / DEVICE / "state.json").exists()
    assert (tmp_path / OTHER / "state.json").exists()


async def test_an_unexpected_error_in_one_event_is_logged_and_contained(harness, caplog):
    async def broken(*_) -> None:
        raise RuntimeError("unexpected")

    harness.session().on_data_points = broken
    harness.session().on_message = broken
    await harness.dp(DPS.state, "totaling")
    await harness.tap(0, 0)
    assert caplog.text.count("RuntimeError: unexpected") == 2
    await harness.dp(DPS.state, "totaling", device_id=OTHER)
    assert harness.session(OTHER).robot.state is None
