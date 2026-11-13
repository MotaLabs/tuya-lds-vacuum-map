import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from .config import Settings
from .i18n import Texts
from .integrations import localtuya
from .integrations.homeassistant import HomeAssistant, HomeAssistantError
from .integrations.localtuya import EVENT_TYPES, data_points
from .integrations.mqtt import Broker, MqttBridge
from .integrations.tuya_cloud import TuyaCloud
from .profiles import Profile
from .session import RobotSession

log = logging.getLogger(__name__)


class App:
    def __init__(self, settings: Settings, profiles: Mapping[str, Profile], texts: Texts) -> None:
        self.settings = settings
        self.ha = HomeAssistant(settings.ha_url, settings.ha_token)
        broker = Broker(settings.mqtt_host, settings.mqtt_port, settings.mqtt_username, settings.mqtt_password)
        self.mqtt = MqttBridge(broker, settings.availability_topic, settings.discovery_prefix)
        credentials = settings.cloud
        cloud = TuyaCloud(credentials.client_id, credentials.client_secret, credentials.region) if credentials else None
        self.cloud = cloud
        self.sessions = {
            robot.device_id: RobotSession(robot, profiles[robot.profile], settings, texts, self.ha, self.mqtt, cloud)
            for robot in settings.robots
        }
        self.routes = {topic: session for session in self.sessions.values() for topic in session.topics.inbound}
        self._watch = asyncio.Event()

    async def run(self) -> None:
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self.ha.run(list(EVENT_TYPES), self.on_ha_event, self.on_ha_ready))
                if self.cloud:
                    group.create_task(self.watch_loop())
                group.create_task(self.mqtt.run(list(self.routes), self.on_mqtt_connect, self.on_mqtt_message))
                for session in self.sessions.values():
                    group.create_task(session.run())
        finally:
            for session in self.sessions.values():
                session.save()

    async def on_ha_event(self, event_type: str, data: dict[str, Any]) -> None:
        device_id, points = data_points(event_type, data)
        session = self.sessions.get(device_id)
        if session and points:
            try:
                await session.on_data_points(points)
            except Exception:
                log.exception("%s: could not handle %s", session.label, event_type)

    async def on_ha_ready(self) -> None:
        for session in self.sessions.values():
            session.catch_up()
        self._watch.set()

    async def watch_loop(self) -> None:
        while True:
            await self._watch.wait()
            self._watch.clear()
            try:
                await self.watch_availability()
            except (HomeAssistantError, TimeoutError) as error:
                log.warning("could not watch the robots' LocalTuya entities: %s", str(error) or type(error).__name__)

    async def watch_availability(self) -> None:
        entries = await self.ha.request({"type": "config/entity_registry/list"})
        for device_id, session in self.sessions.items():
            entity_ids = localtuya.entity_ids(entries, device_id)
            if entity_ids:
                await self.ha.subscribe(localtuya.available_trigger(entity_ids), session.on_available)
            else:
                log.warning("%s: no LocalTuya entity, so a reconnection is not caught up", session.label)

    async def on_mqtt_connect(self) -> None:
        for session in self.sessions.values():
            await session.on_connect()

    async def on_mqtt_message(self, topic: str, payload: str) -> None:
        session = self.routes.get(topic)
        if session:
            try:
                await session.on_message(topic, payload)
            except Exception:
                log.exception("%s: could not handle %s", session.label, topic)
