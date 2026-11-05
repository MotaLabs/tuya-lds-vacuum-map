from typing import Any

from ..lds.messages import as_object, as_text
from .homeassistant import HomeAssistant

DP_EVENT = "localtuya_device_dp_triggered"
STATUS_EVENT = "localtuya_status_update"
EVENT_TYPES = (DP_EVENT, STATUS_EVENT)


def data_points(event_type: str, data: Any) -> tuple[str | None, dict[int, Any]]:
    data = as_object(data)
    if event_type == DP_EVENT:
        raw = {str(data.get("dp")): data.get("value")}
    elif event_type == STATUS_EVENT:
        raw = as_object(data.get("new_status"))
    else:
        raw = {}
    return as_text(data.get("device_id")), {int(dp): value for dp, value in raw.items() if str(dp).isdecimal()}


class LocalTuyaLink:
    def __init__(self, ha: HomeAssistant, device_id: str, raw_dp: int) -> None:
        self.ha = ha
        self.device_id = device_id
        self.raw_dp = raw_dp

    async def send_raw(self, value: str) -> None:
        await self.ha.call_service(
            "localtuya", "set_dp", {"device_id": self.device_id, "dp": self.raw_dp, "value": value}
        )
