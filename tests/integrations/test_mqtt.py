import asyncio
import json
import re
from types import SimpleNamespace

import aiomqtt

from support.waiting import eventually, stop
from tuya_lds_vacuum_map.integrations.mqtt import Broker, MqttBridge, Topics, discovery_configs

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
    )


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
        },
        ["Auto", "Local"],
        ["None", "Left"],
    )
    assert sorted(configs) == [
        "button/tlvm_dev_random_room_color",
        "camera/tlvm_dev_map",
        "select/tlvm_dev_map_source",
        "select/tlvm_dev_room_settings",
        "text/tlvm_dev_room_color",
    ]
    color = configs["text/tlvm_dev_room_color"]
    assert (color["state_topic"], color["command_topic"]) == (TOPICS.room_color, TOPICS.room_color_set)
    assert re.match(color["pattern"], "#a0B1c2")
    assert re.match(color["pattern"], "")
    assert not re.match(color["pattern"], "#a0b1c")
    assert configs["button/tlvm_dev_random_room_color"]["command_topic"] == TOPICS.room_color_random
    camera = configs["camera/tlvm_dev_map"]
    assert (camera["topic"], camera["json_attributes_topic"], camera["unique_id"]) == (
        TOPICS.map,
        TOPICS.map_attributes,
        "tlvm_dev_map",
    )
    room = configs["select/tlvm_dev_room_settings"]
    assert (room["command_topic"], room["options"]) == (TOPICS.room_settings_set, ["None", "Left"])
    assert configs["select/tlvm_dev_map_source"]["options"] == ["Auto", "Local"]
    assert all(
        config["availability_topic"] == AVAILABILITY and config["device"] == DEVICE for config in configs.values()
    )


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
    await bridge(client).publish_discovery({"camera/x": {"name": "Mapa"}})
    assert client.published == [
        ("homeassistant/camera/x/config", json.dumps({"name": "Mapa"}, ensure_ascii=False), True)
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
    assert (connection.host, connection.options["port"], connection.options["username"]) == ("broker", 1884, "user")
    assert (will.topic, will.payload, will.retain) == (AVAILABILITY, "offline", True)
    assert connection.subscribed == ["maps/dev/tap", "maps/dev/room_color/set"]
    assert seen_connected == [True]
    assert connection.published == [(AVAILABILITY, "online", True)]
    connection.inbox.put_nowait(SimpleNamespace(topic=SimpleNamespace(value="maps/dev/tap"), payload=b'{"x": 1}'))
    await eventually(lambda: received)
    assert received == [("maps/dev/tap", '{"x": 1}')]
    await stop(task)
    assert not target.connected
