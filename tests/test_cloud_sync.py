import asyncio
import http.client

import pytest

from support.waiting import eventually, stop
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


async def test_fetch_hands_the_payload_over():
    received = []
    await sync(FakeCloud(), received).fetch()
    assert received == [{"device": "dev", "call": 1}]


async def test_cloud_errors_are_logged(caplog):
    received = []
    await sync(FakeCloud(TuyaCloudError(1106, "permission deny")), received).fetch()
    assert received == []
    assert "permission deny" in caplog.text


async def test_a_cut_download_is_logged(caplog):
    received = []
    await sync(FakeCloud(http.client.IncompleteRead(b"{")), received).fetch()
    assert received == []
    assert "cloud map for dev unavailable: IncompleteRead" in caplog.text


@pytest.mark.parametrize("error", [TimeoutError("timed out"), ConnectionResetError("reset by peer")])
async def test_a_cloud_that_does_not_answer_is_logged(caplog, error):
    received = []
    await sync(FakeCloud(error), received).fetch()
    assert received == []
    assert f"cloud map for dev unavailable: {error}" in caplog.text


async def test_an_unexpected_error_is_logged_and_the_next_fetch_still_runs(caplog):
    cloud = FakeCloud()

    def broken(payload: dict) -> None:
        raise RuntimeError("bad payload")

    target = CloudMapSync(cloud, "dev", TIMING, lambda: True, lambda: True, broken)
    task = asyncio.create_task(target.run())
    await eventually(lambda: cloud.calls >= 2)
    await stop(task)
    assert "RuntimeError: bad payload" in caplog.text


async def test_moving_robot_refreshes_often():
    cloud = FakeCloud()
    task = asyncio.create_task(sync(cloud, [], moving=True).run())
    await eventually(lambda: cloud.calls >= 3)
    await stop(task)


async def test_idle_robot_waits_for_a_wake():
    cloud = FakeCloud()
    target = sync(cloud, [])
    task = asyncio.create_task(target.run())
    await eventually(lambda: cloud.calls == 1)
    await asyncio.sleep(0.05)
    assert cloud.calls == 1
    target.wake()
    await eventually(lambda: cloud.calls == 2)
    await stop(task)


async def test_wake_after_cleaning_waits_for_the_upload():
    cloud = FakeCloud()
    target = sync(cloud, [])
    task = asyncio.create_task(target.run())
    await eventually(lambda: cloud.calls == 1)
    target.wake_after_cleaning()
    await asyncio.sleep(TIMING.cloud_after_cleaning / 4)
    assert cloud.calls == 1
    await eventually(lambda: cloud.calls == 2)
    await stop(task)


async def test_local_only_never_calls_the_cloud():
    cloud = FakeCloud()
    target = sync(cloud, [], enabled=False)
    task = asyncio.create_task(target.run())
    await asyncio.sleep(0.02)
    target.wake()
    await asyncio.sleep(0.02)
    await stop(task)
    assert cloud.calls == 0
