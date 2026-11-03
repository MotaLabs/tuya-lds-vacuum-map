import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from .config import Timing
from .integrations.tuya_cloud import TuyaCloud, TuyaCloudError

log = logging.getLogger(__name__)


class CloudMapSync:
    def __init__(
        self,
        cloud: TuyaCloud,
        device_id: str,
        timing: Timing,
        enabled: Callable[[], bool],
        moving: Callable[[], bool],
        on_payload: Callable[[dict[str, Any]], None],
    ) -> None:
        self.cloud = cloud
        self.device_id = device_id
        self.timing = timing
        self.enabled = enabled
        self.moving = moving
        self.on_payload = on_payload
        self._wake = asyncio.Event()

    def wake(self) -> None:
        self._wake.set()

    def wake_after_cleaning(self) -> None:
        asyncio.get_running_loop().call_later(self.timing.cloud_after_cleaning, self._wake.set)

    async def run(self) -> None:
        while True:
            if self.enabled():
                await self.fetch()
            timeout = self.timing.cloud_refresh_moving if self.moving() else self.timing.cloud_refresh_idle
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout)
            self._wake.clear()

    async def fetch(self) -> None:
        try:
            payload = await self.cloud.realtime_map(self.device_id)
        except (TuyaCloudError, OSError, ValueError) as error:
            log.warning("cloud map for %s unavailable: %s", self.device_id, error)
            return
        self.on_payload(payload)
