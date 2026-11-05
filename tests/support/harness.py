import json

from support.fakes import FakeClient
from support.maps import PROFILE
from tuya_lds_vacuum_map.app import App
from tuya_lds_vacuum_map.config import RobotSettings, Settings, Timing
from tuya_lds_vacuum_map.i18n import load_texts
from tuya_lds_vacuum_map.integrations.localtuya import DP_EVENT
from tuya_lds_vacuum_map.profiles import DEFAULT_PROFILE, Profile
from tuya_lds_vacuum_map.session import RobotSession

DEVICE = "dev123456"


class Harness:
    def __init__(
        self,
        data_dir,
        robots: tuple[RobotSettings, ...] = (RobotSettings(DEVICE),),
        language: str = "en",
        profiles: dict[str, Profile] | None = None,
    ) -> None:
        settings = Settings(
            ha_url="http://ha.local:8123",
            ha_token="token",
            mqtt_host="broker",
            robots=robots,
            language=language,
            data_dir=str(data_dir),
            timing=Timing(double_tap=0.05, render_interval=0.01, save_interval=0.01),
        )
        self.app = App(settings, profiles or {DEFAULT_PROFILE: PROFILE}, load_texts(language))
        self.client = FakeClient()
        self.app.mqtt._client = self.client
        self.services: list[tuple] = []
        self.events: list[tuple] = []
        self.app.ha.call_service = self.call_service
        self.app.ha.fire_event = self.fire_event

    def session(self, device_id: str = DEVICE) -> RobotSession:
        return self.app.sessions[device_id]

    async def call_service(self, domain: str, service: str, data: dict) -> None:
        self.services.append((domain, service, data))

    async def fire_event(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))

    async def dp(self, dp: int, value, device_id: str = DEVICE) -> None:
        await self.app.on_ha_event(DP_EVENT, {"device_id": device_id, "dp": dp, "value": value})

    async def message(self, topic: str, payload: str) -> None:
        await self.app.on_mqtt_message(topic, payload)

    async def tap(self, x: int, y: int, mode: str = "room", device_id: str = DEVICE) -> None:
        await self.message(self.session(device_id).topics.tap, json.dumps({"x": x, "y": y, "mode": mode}))
