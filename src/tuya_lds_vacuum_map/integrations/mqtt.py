import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import aiomqtt

from ..map.colors import COLOR_PATTERN

log = logging.getLogger(__name__)

MessageHandler = Callable[[str, str], Awaitable[None]]
ConnectHandler = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class Topics:
    base: str

    @property
    def map(self) -> str:
        return f"{self.base}/map"

    @property
    def map_attributes(self) -> str:
        return f"{self.base}/map/attributes"

    @property
    def map_source(self) -> str:
        return f"{self.base}/map_source"

    @property
    def map_source_set(self) -> str:
        return f"{self.base}/map_source/set"

    @property
    def room_settings(self) -> str:
        return f"{self.base}/room_settings"

    @property
    def room_settings_set(self) -> str:
        return f"{self.base}/room_settings/set"

    @property
    def room_color(self) -> str:
        return f"{self.base}/room_color"

    @property
    def room_color_set(self) -> str:
        return f"{self.base}/room_color/set"

    @property
    def room_color_random(self) -> str:
        return f"{self.base}/room_color/random"

    @property
    def tap(self) -> str:
        return f"{self.base}/tap"

    @property
    def inbound(self) -> tuple[str, ...]:
        return (self.tap, self.map_source_set, self.room_settings_set, self.room_color_set, self.room_color_random)


@dataclass(frozen=True)
class Broker:
    host: str
    port: int = 1883
    username: str | None = None
    password: str | None = None


def discovery_configs(
    topics: Topics,
    availability: str,
    unique_prefix: str,
    device: dict[str, Any],
    names: dict[str, str],
    source_options: list[str],
    room_options: list[str],
) -> dict[str, dict[str, Any]]:
    common = {"availability_topic": availability, "device": device}
    return {
        f"camera/{unique_prefix}_map": {
            **common,
            "name": names["map"],
            "unique_id": f"{unique_prefix}_map",
            "topic": topics.map,
            "json_attributes_topic": topics.map_attributes,
        },
        f"select/{unique_prefix}_map_source": {
            **common,
            "name": names["map_source"],
            "unique_id": f"{unique_prefix}_map_source",
            "state_topic": topics.map_source,
            "command_topic": topics.map_source_set,
            "options": source_options,
            "entity_category": "config",
        },
        f"select/{unique_prefix}_room_settings": {
            **common,
            "name": names["room_settings"],
            "unique_id": f"{unique_prefix}_room_settings",
            "state_topic": topics.room_settings,
            "command_topic": topics.room_settings_set,
            "options": room_options,
        },
        f"text/{unique_prefix}_room_color": {
            **common,
            "name": names["room_color"],
            "unique_id": f"{unique_prefix}_room_color",
            "state_topic": topics.room_color,
            "command_topic": topics.room_color_set,
            "pattern": f"^({COLOR_PATTERN})?$",
            "max": 7,
            "icon": "mdi:palette",
        },
        f"button/{unique_prefix}_random_room_color": {
            **common,
            "name": names["random_room_color"],
            "unique_id": f"{unique_prefix}_random_room_color",
            "command_topic": topics.room_color_random,
            "icon": "mdi:dice-multiple",
        },
    }


class MqttBridge:
    def __init__(self, broker: Broker, availability: str, discovery_prefix: str) -> None:
        self.broker = broker
        self.availability = availability
        self.discovery_prefix = discovery_prefix
        self._client: aiomqtt.Client | None = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    async def run(self, subscriptions: Sequence[str], on_connect: ConnectHandler, on_message: MessageHandler) -> None:
        backoff = 1
        while True:
            try:
                will = aiomqtt.Will(self.availability, "offline", retain=True)
                async with aiomqtt.Client(
                    self.broker.host,
                    port=self.broker.port,
                    username=self.broker.username,
                    password=self.broker.password,
                    will=will,
                ) as client:
                    self._client = client
                    for topic in subscriptions:
                        await client.subscribe(topic)
                    await on_connect()
                    await client.publish(self.availability, "online", retain=True)
                    backoff = 1
                    log.info("connected to MQTT")
                    async for message in client.messages:
                        payload = (
                            message.payload.decode()
                            if isinstance(message.payload, bytes | bytearray)
                            else str(message.payload)
                        )
                        await on_message(message.topic.value, payload)
            except aiomqtt.MqttError as error:
                log.warning("MQTT connection lost: %s", error)
            finally:
                self._client = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def publish(self, topic: str, payload: str | bytes, retain: bool = True) -> bool:
        client = self._client
        if client is None:
            return False
        try:
            await client.publish(topic, payload, retain=retain)
        except aiomqtt.MqttError as error:
            log.warning("could not publish %s: %s", topic, error)
            return False
        return True

    async def publish_discovery(self, configs: dict[str, dict[str, Any]]) -> None:
        for path, config in configs.items():
            await self.publish(f"{self.discovery_prefix}/{path}/config", json.dumps(config, ensure_ascii=False))

    async def publish_map(self, topics: Topics, image: bytes, attributes: dict[str, Any]) -> bool:
        if not await self.publish(topics.map, image):
            return False
        return await self.publish(topics.map_attributes, json.dumps(attributes, ensure_ascii=False))
