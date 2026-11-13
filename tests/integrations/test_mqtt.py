import asyncio
import json
import re
from types import SimpleNamespace

import aiomqtt

from support.waiting import RecordedSleeps, eventually, stop
from tuya_lds_vacuum_map.integrations import mqtt
from tuya_lds_vacuum_map.integrations.mqtt import Broker, MqttBridge, Topics, discovery_configs, statistic_configs

TOPICS = Topics("maps/dev")
AVAILABILITY = "maps/availability"
DEVICE = {"identifiers": ["dev"], "name": "Vacuum"}


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.published: list[tuple] = []
        self.fail = fail

    async def publish(self, topic: str, payload, retain: bool = False) -> None:
        if self.fail:
            raise aiomqtt.MqttError("gone")
        self.published.append((topic, payload, retain))


def bridge(client: FakeClient | None = None) -> MqttBridge:
    bridge = MqttBridge(Broker("broker"), AVAILABILITY, "homeassistant")
    bridge._client = client
    return bridge


def test_topics():
    assert TOPICS.map == "maps/dev/map"
    assert TOPICS.map_attributes == "maps/dev/map/attributes"
    assert TOPICS.inbound == (
        "maps/dev/tap",
        "maps/dev/map_source/set",
        "maps/dev/room_settings/set",
        "maps/dev/room_color/set",
        "maps/dev/room_color/random",
        "maps/dev/auto_start/set",
        "maps/dev/start_delay/set",
        "maps/dev/start",
        "maps/dev/clear",
    )
    assert (TOPICS.auto_start, TOPICS.start_delay) == ("maps/dev/auto_start", "maps/dev/start_delay")


def test_discovery_configs():
    configs = discovery_configs(
        TOPICS,
        AVAILABILITY,
        "tlvm_dev",
        DEVICE,
        {
            "map": "Map",
            "map_source": "Source",
            "room_settings": "Room",
            "room_color": "Color",
            "random_room_color": "Dice",
            "auto_start": "Auto start",
            "start_delay": "Delay",
            "start": "Go",
            "clear": "Drop",
        },
        ["Auto", "Local"],
        ["None", "Left"],
        (1.0, 300.0),
    )
    common = {"availability_topic": AVAILABILITY, "device": DEVICE}
    color = configs["text/tlvm_dev_room_color"]
    assert re.match(color["pattern"], "#a0B1c2")
    assert re.match(color["pattern"], "")
    assert not re.match(color["pattern"], "#a0b1c")
    assert configs == {
        "select/tlvm_dev_room_tap": None,
        "camera/tlvm_dev_map": {
            **common,
            "name": "Map",
            "unique_id": "tlvm_dev_map",
            "topic": "maps/dev/map",
            "json_attributes_topic": "maps/dev/map/attributes",
        },
        "select/tlvm_dev_map_source": {
            **common,
            "name": "Source",
            "unique_id": "tlvm_dev_map_source",
            "state_topic": "maps/dev/map_source",
            "command_topic": "maps/dev/map_source/set",
            "options": ["Auto", "Local"],
            "entity_category": "config",
        },
        "select/tlvm_dev_room_settings": {
            **common,
            "name": "Room",
            "unique_id": "tlvm_dev_room_settings",
            "state_topic": "maps/dev/room_settings",
            "command_topic": "maps/dev/room_settings/set",
            "options": ["None", "Left"],
        },
        "text/tlvm_dev_room_color": {
            **common,
            "name": "Color",
            "unique_id": "tlvm_dev_room_color",
            "state_topic": "maps/dev/room_color",
            "command_topic": "maps/dev/room_color/set",
            "pattern": color["pattern"],
            "max": 7,
            "icon": "mdi:palette",
        },
        "button/tlvm_dev_random_room_color": {
            **common,
            "name": "Dice",
            "unique_id": "tlvm_dev_random_room_color",
            "command_topic": "maps/dev/room_color/random",
            "icon": "mdi:dice-multiple",
        },
        "switch/tlvm_dev_auto_start": {
            **common,
            "name": "Auto start",
            "unique_id": "tlvm_dev_auto_start",
            "state_topic": "maps/dev/auto_start",
            "command_topic": "maps/dev/auto_start/set",
            "entity_category": "config",
            "icon": "mdi:timer-play-outline",
        },
        "number/tlvm_dev_start_delay": {
            **common,
            "name": "Delay",
            "unique_id": "tlvm_dev_start_delay",
            "state_topic": "maps/dev/start_delay",
            "command_topic": "maps/dev/start_delay/set",
            "min": 1.0,
            "max": 300.0,
            "step": 1,
            "mode": "box",
            "unit_of_measurement": "s",
            "device_class": "duration",
            "entity_category": "config",
        },
        "button/tlvm_dev_start": {
            **common,
            "name": "Go",
            "unique_id": "tlvm_dev_start",
            "command_topic": "maps/dev/start",
            "icon": "mdi:play",
        },
        "button/tlvm_dev_clear": {
            **common,
            "name": "Drop",
            "unique_id": "tlvm_dev_clear",
            "command_topic": "maps/dev/clear",
            "icon": "mdi:selection-remove",
        },
    }


def test_statistic_configs():
    names = {"clean_time": "Time", "clean_area": "Area", "total_time": "Total time", "total_count": "Count"}
    configs = statistic_configs(
        TOPICS,
        AVAILABILITY,
        "tlvm_dev",
        DEVICE,
        names,
        {"time": "s", "area": "m²"},
        ["clean_time", "clean_area", "total_time", "total_count"],
    )
    assert sorted(configs) == [
        "sensor/tlvm_dev_clean_area",
        "sensor/tlvm_dev_clean_time",
        "sensor/tlvm_dev_total_count",
        "sensor/tlvm_dev_total_time",
    ]
    assert TOPICS.statistic("clean_time") == "maps/dev/statistics/clean_time"
    assert configs["sensor/tlvm_dev_clean_time"] == {
        "availability_topic": AVAILABILITY,
        "device": DEVICE,
        "name": "Time",
        "unique_id": "tlvm_dev_clean_time",
        "state_topic": "maps/dev/statistics/clean_time",
        "state_class": "measurement",
        "unit_of_measurement": "s",
        "device_class": "duration",
    }
    area = configs["sensor/tlvm_dev_clean_area"]
    assert (area["unit_of_measurement"], area["state_class"]) == ("m²", "measurement")
    assert "device_class" not in area
    assert "entity_category" not in area
    total = configs["sensor/tlvm_dev_total_time"]
    assert (total["state_class"], total["entity_category"], total["device_class"]) == (
        "total_increasing",
        "diagnostic",
        "duration",
    )
    count = configs["sensor/tlvm_dev_total_count"]
    assert "unit_of_measurement" not in count
    assert (count["state_class"], count["entity_category"], count["icon"]) == (
        "total_increasing",
        "diagnostic",
        "mdi:counter",
    )


def test_no_statistic_gives_no_sensor():
    assert statistic_configs(TOPICS, AVAILABILITY, "tlvm_dev", DEVICE, {}, {}, []) == {}


async def test_publish_needs_a_connection():
    assert not bridge().connected
    assert await bridge().publish("t", "x") is False
    assert await bridge(FakeClient(fail=True)).publish("t", "x") is False


async def test_publish_map_sends_image_and_attributes():
    client = FakeClient()
    assert await bridge(client).publish_map(TOPICS, b"png", {"rooms": ["Suíte"]})
    assert client.published == [(TOPICS.map, b"png", True), (TOPICS.map_attributes, '{"rooms": ["Suíte"]}', True)]


async def test_publish_discovery_uses_the_prefix():
    client = FakeClient()
    await bridge(client).publish_discovery({"camera/x": {"name": "Mapa"}, "select/y": None})
    assert client.published == [
        ("homeassistant/camera/x/config", json.dumps({"name": "Mapa"}, ensure_ascii=False), True),
        ("homeassistant/select/y/config", "", True),
    ]


async def test_a_failed_image_skips_the_attributes():
    client = FakeClient(fail=True)
    assert await bridge(client).publish_map(TOPICS, b"png", {}) is False


class FakeConnection:
    def __init__(self, host: str, options: dict) -> None:
        self.host = host
        self.options = options
        self.subscribed: list[str] = []
        self.published: list[tuple] = []
        self.inbox: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self) -> "FakeConnection":
        return self

    async def __aexit__(self, *_) -> None:
        return None

    async def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    async def publish(self, topic: str, payload, retain: bool = False) -> None:
        self.published.append((topic, payload, retain))

    @property
    async def messages(self):
        while True:
            yield await self.inbox.get()


async def test_run_subscribes_announces_and_delivers(monkeypatch):
    connections: list[FakeConnection] = []

    def connect(host: str, **options) -> FakeConnection:
        connections.append(FakeConnection(host, options))
        return connections[-1]

    monkeypatch.setattr(aiomqtt, "Client", connect)
    target = MqttBridge(Broker("broker", 1884, "user", "secret"), AVAILABILITY, "homeassistant")
    seen_connected, received = [], []

    async def on_connect() -> None:
        seen_connected.append(target.connected)

    async def on_message(topic: str, payload: str) -> None:
        received.append((topic, payload))

    task = asyncio.create_task(target.run(["maps/dev/tap", "maps/dev/room_color/set"], on_connect, on_message))
    await eventually(lambda: connections and connections[0].published)
    [connection] = connections
    will = connection.options["will"]
    assert (connection.host, connection.options["port"]) == ("broker", 1884)
    assert (connection.options["username"], connection.options["password"]) == ("user", "secret")
    assert (will.topic, will.payload, will.retain) == (AVAILABILITY, "offline", True)
    assert connection.subscribed == ["maps/dev/tap", "maps/dev/room_color/set"]
    assert seen_connected == [True]
    assert connection.published == [(AVAILABILITY, "online", True)]
    connection.inbox.put_nowait(SimpleNamespace(topic=SimpleNamespace(value="maps/dev/tap"), payload=b'{"x": 1}'))
    connection.inbox.put_nowait(SimpleNamespace(topic=SimpleNamespace(value="maps/dev/start"), payload="go"))
    await eventually(lambda: len(received) == 2)
    assert received == [("maps/dev/tap", '{"x": 1}'), ("maps/dev/start", "go")]
    await stop(task)
    assert not target.connected


async def test_a_message_that_is_not_utf8_is_skipped(monkeypatch, caplog):
    connections: list[FakeConnection] = []

    def connect(host: str, **options) -> FakeConnection:
        connections.append(FakeConnection(host, options))
        return connections[-1]

    async def on_message(topic: str, payload: str) -> None:
        received.append(payload)

    monkeypatch.setattr(aiomqtt, "Client", connect)
    received: list[str] = []
    target = MqttBridge(Broker("broker"), AVAILABILITY, "homeassistant")
    task = asyncio.create_task(target.run(["maps/dev/tap"], nothing, on_message))
    await eventually(lambda: connections and connections[0].published)
    for payload in (b"\xff\xfe", b"after"):
        connections[0].inbox.put_nowait(SimpleNamespace(topic=SimpleNamespace(value="maps/dev/tap"), payload=payload))
    await eventually(lambda: received)
    await stop(task)
    assert received == ["after"]
    assert "maps/dev/tap that is not UTF-8" in caplog.text


class RefusedConnection(FakeConnection):
    async def __aenter__(self) -> "FakeConnection":
        raise aiomqtt.MqttError("refused")


class LostConnection(FakeConnection):
    @property
    async def messages(self):
        raise aiomqtt.MqttError("lost")
        yield


async def nothing() -> None:
    pass


async def ignore(topic: str, payload: str) -> None:
    pass


async def test_a_refused_connection_retries_ever_more_slowly(monkeypatch, caplog):
    sleeps = RecordedSleeps()
    monkeypatch.setattr(mqtt, "asyncio", sleeps)
    monkeypatch.setattr(aiomqtt, "Client", lambda host, **options: RefusedConnection(host, options))
    target = MqttBridge(Broker("broker"), AVAILABILITY, "homeassistant")
    task = asyncio.create_task(target.run([], nothing, ignore))
    await eventually(lambda: len(sleeps.delays) >= 8)
    await stop(task)
    assert sleeps.delays[:8] == [1, 2, 4, 8, 16, 32, 60, 60]
    assert "MQTT connection lost: refused" in caplog.text


async def test_a_connection_that_was_up_starts_the_wait_over(monkeypatch):
    sleeps = RecordedSleeps()
    monkeypatch.setattr(mqtt, "asyncio", sleeps)
    monkeypatch.setattr(aiomqtt, "Client", lambda host, **options: LostConnection(host, options))
    target = MqttBridge(Broker("broker"), AVAILABILITY, "homeassistant")
    task = asyncio.create_task(target.run([], nothing, ignore))
    await eventually(lambda: len(sleeps.delays) >= 3)
    await stop(task)
    assert sleeps.delays[:3] == [1, 1, 1]
