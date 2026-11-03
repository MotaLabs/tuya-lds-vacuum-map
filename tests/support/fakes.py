class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    async def publish(self, topic: str, payload, retain: bool = False) -> None:
        self.published.append((topic, payload))

    def last(self, topic: str):
        return next(payload for published, payload in reversed(self.published) if published == topic)

    def topics(self) -> list[str]:
        return [topic for topic, _ in self.published]


class FakeCloudSync:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def wake(self) -> None:
        self.calls.append("wake")

    def wake_after_cleaning(self) -> None:
        self.calls.append("after_cleaning")
