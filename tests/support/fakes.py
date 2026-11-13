import asyncio

from websockets.exceptions import ConnectionClosedError

from tuya_lds_vacuum_map.integrations.tuya_cloud import Reported


class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    async def publish(self, topic: str, payload, retain: bool = False) -> None:
        self.published.append((topic, payload))

    def last(self, topic: str):
        return next(payload for published, payload in reversed(self.published) if published == topic)

    def topics(self) -> list[str]:
        return [topic for topic, _ in self.published]


class ClosedSocket:
    async def send(self, message: str) -> None:
        raise ConnectionClosedError(None, None)


class FakeCloudSync:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def wake(self) -> None:
        self.calls.append("wake")

    def wake_after_cleaning(self) -> None:
        self.calls.append("after_cleaning")

    async def run(self) -> None:
        self.calls.append("run")
        await asyncio.Event().wait()


class FakeCloud:
    def __init__(self, points: dict[int, Reported] | None = None, error: Exception | None = None) -> None:
        self.points = points or {}
        self.error = error
        self.calls: list[str] = []
        self.release: asyncio.Event | None = None

    async def data_points(self, device_id: str) -> dict[int, Reported]:
        self.calls.append(device_id)
        if self.release:
            await self.release.wait()
        if self.error:
            raise self.error
        return dict(self.points)
