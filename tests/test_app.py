import asyncio
import dataclasses
import json

import pytest

from support.harness import DEVICE, Harness
from support.maps import PROFILE, cloud_payload, rooms_message
from support.waiting import eventually, stop
from tuya_lds_vacuum_map.app import App
from tuya_lds_vacuum_map.config import CloudCredentials, RobotSettings, Settings
from tuya_lds_vacuum_map.i18n import load_texts
from tuya_lds_vacuum_map.integrations import tuya_cloud
from tuya_lds_vacuum_map.integrations.homeassistant import HomeAssistantError
from tuya_lds_vacuum_map.integrations.localtuya import DP_EVENT, available_trigger
from tuya_lds_vacuum_map.integrations.tuya_cloud import TuyaCloud
from tuya_lds_vacuum_map.lds.codec import decode_raw, encode_raw
from tuya_lds_vacuum_map.profiles import DEFAULT_PROFILE, DataPoints
from tuya_lds_vacuum_map.storage import MapSource, Storage

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
    await harness.tap(0, 0, "spot", device_id=OTHER)
    await harness.tap(0, 0, "spot")
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
    configs = [
        json.loads(payload) for topic, payload in harness.client.published if topic.endswith("/config") and payload
    ]
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


async def test_running_feeds_every_robot_from_home_assistant_and_mqtt(harness):
    started = []

    def starter(device_id: str):
        async def run() -> None:
            started.append(device_id)
            await asyncio.Event().wait()

        return run

    async def ha_run(event_types, on_event, on_ready) -> None:
        assert DP_EVENT in event_types
        assert on_ready == harness.app.on_ha_ready
        await on_event(DP_EVENT, {"device_id": DEVICE, "dp": DPS.state, "value": "totaling"})
        await asyncio.Event().wait()

    async def mqtt_run(topics, on_connect, on_message) -> None:
        source_set = harness.session().topics.map_source_set
        assert source_set in topics
        await on_connect()
        await on_message(source_set, "Local only")
        await asyncio.Event().wait()

    for device_id, session in harness.app.sessions.items():
        session.run = starter(device_id)
    harness.app.ha.run, harness.app.mqtt.run = ha_run, mqtt_run
    task = asyncio.create_task(harness.app.run())
    await eventually(
        lambda: harness.session().snapshot.source is MapSource.LOCAL and sorted(started) == sorted(harness.app.sessions)
    )
    await stop(task)
    assert harness.session().robot.state == "totaling"
    assert any(topic.endswith("/config") for topic in harness.client.topics())


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


async def test_home_assistant_ready_makes_every_robot_catch_up_and_starts_watching(harness):
    caught_up: list[str] = []
    for device_id, session in harness.app.sessions.items():
        session.catch_up = lambda device_id=device_id: caught_up.append(device_id)
    await harness.app.on_ha_ready()
    assert sorted(caught_up) == sorted([DEVICE, OTHER])
    assert harness.app._watch.is_set()


async def test_watching_subscribes_to_each_robots_localtuya_entities(harness, caplog):
    entries = [
        {"entity_id": "vacuum.hall", "platform": "localtuya", "unique_id": f"local_{DEVICE}_1"},
        {"entity_id": "sensor.hall_battery", "platform": "localtuya", "unique_id": f"local_{DEVICE}_106"},
    ]
    requests: list[dict] = []
    subscriptions: list[tuple] = []

    async def request(payload: dict):
        requests.append(payload)
        return entries

    async def subscribe(payload: dict, handler) -> None:
        subscriptions.append((payload, handler))

    harness.app.ha.request, harness.app.ha.subscribe = request, subscribe
    await harness.app.watch_availability()
    assert requests == [{"type": "config/entity_registry/list"}]
    assert subscriptions == [
        (available_trigger(["sensor.hall_battery", "vacuum.hall"]), harness.session().on_available)
    ]
    assert "Upstairs: no LocalTuya entity" in caplog.text


async def test_the_watch_runs_on_each_ready_and_survives_a_refusal_or_a_timeout(harness, caplog):
    answers: list = [HomeAssistantError("unauthorized"), TimeoutError(), []]

    async def request(payload: dict):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    harness.app.ha.request = request
    task = asyncio.create_task(harness.app.watch_loop())
    await harness.app.on_ha_ready()
    await eventually(lambda: "could not watch the robots' LocalTuya entities: unauthorized" in caplog.text)
    await harness.app.on_ha_ready()
    await eventually(lambda: "could not watch the robots' LocalTuya entities: TimeoutError" in caplog.text)
    await harness.app.on_ha_ready()
    await eventually(lambda: answers == [])
    await stop(task)


async def test_the_watch_runs_only_with_a_cloud_project(tmp_path):
    started: list[bool] = []

    async def idle(*_) -> None:
        await asyncio.Event().wait()

    async def watch_loop() -> None:
        started.append(True)
        await asyncio.Event().wait()

    for cloud in (CloudCredentials("id", "secret", "eu"), None):
        settings = Settings(
            ha_url="http://ha.local:8123",
            ha_token="token",
            mqtt_host="broker",
            robots=(RobotSettings(DEVICE),),
            data_dir=str(tmp_path),
            cloud=cloud,
        )
        app = App(settings, {DEFAULT_PROFILE: PROFILE}, load_texts("en"))
        app.ha.run = app.mqtt.run = idle
        for session in app.sessions.values():
            session.run = idle
        app.watch_loop = watch_loop
        task = asyncio.create_task(app.run())
        if cloud:
            await eventually(lambda: started == [True])
        else:
            await asyncio.sleep(0.02)
        await stop(task)
    assert started == [True]


def test_every_robot_shares_the_cloud_project(tmp_path):
    settings = Settings(
        ha_url="http://ha.local:8123",
        ha_token="token",
        mqtt_host="broker",
        robots=(RobotSettings(DEVICE), RobotSettings(OTHER)),
        data_dir=str(tmp_path),
        cloud=CloudCredentials("id", "secret", "eu"),
    )
    app = App(settings, {DEFAULT_PROFILE: PROFILE}, load_texts("en"))
    [first, second] = app.sessions.values()
    assert isinstance(first.cloud, TuyaCloud)
    assert first.cloud is second.cloud
    assert first.cloud.base == tuya_cloud.ENDPOINTS["eu"]
