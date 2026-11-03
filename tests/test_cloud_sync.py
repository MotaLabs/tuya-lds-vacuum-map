import asyncio

from tuya_lds_vacuum_map.cloud_sync import CloudMapSync
from tuya_lds_vacuum_map.config import Timing
from tuya_lds_vacuum_map.integrations.tuya_cloud import TuyaCloudError

TIMING = Timing(cloud_refresh_moving=0.02, cloud_refresh_idle=10, cloud_after_cleaning=0.02)


class FakeCloud:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    async def realtime_map(self, device_id: str) -> dict:
        self.calls += 1
        if self.error:
            raise self.error
        return {"device": device_id, "call": self.calls}


def sync(cloud: FakeCloud, received: list, enabled: bool = True, moving: bool = False) -> CloudMapSync:
    return CloudMapSync(cloud, "dev", TIMING, lambda: enabled, lambda: moving, received.append)


async def run_for(target: CloudMapSync, seconds: float, before_stop=None) -> None:
    task = asyncio.create_task(target.run())
    await asyncio.sleep(seconds)
    if before_stop:
        before_stop()
        await asyncio.sleep(seconds)
    task.cancel()


async def test_fetch_hands_the_payload_over():
    received = []
    await sync(FakeCloud(), received).fetch()
    assert received == [{"device": "dev", "call": 1}]


async def test_cloud_errors_are_logged(caplog):
    received = []
    await sync(FakeCloud(TuyaCloudError(1106, "permission deny")), received).fetch()
    assert received == []
    assert "permission deny" in caplog.text


async def test_moving_robot_refreshes_often():
    cloud = FakeCloud()
    await run_for(sync(cloud, [], moving=True), 0.1)
    assert cloud.calls >= 3


async def test_idle_robot_waits_for_a_wake():
    cloud = FakeCloud()
    target = sync(cloud, [])
    await run_for(target, 0.05, target.wake)
    assert cloud.calls == 2


async def test_wake_after_cleaning_waits_for_the_upload():
    cloud = FakeCloud()
    target = sync(cloud, [])
    task = asyncio.create_task(target.run())
    await asyncio.sleep(0.005)
    target.wake_after_cleaning()
    await asyncio.sleep(0.005)
    assert cloud.calls == 1
    await asyncio.sleep(0.05)
    task.cancel()
    assert cloud.calls == 2


async def test_local_only_never_calls_the_cloud():
    cloud = FakeCloud()
    target = sync(cloud, [], enabled=False)
    await run_for(target, 0.02, target.wake)
    assert cloud.calls == 0
