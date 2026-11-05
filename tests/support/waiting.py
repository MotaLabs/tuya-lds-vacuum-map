import asyncio
import contextlib
from collections.abc import Callable

POLL_SECONDS = 0.005
ATTEMPTS = 400


async def eventually(check: Callable[[], bool]) -> None:
    for _ in range(ATTEMPTS):
        if check():
            return
        await asyncio.sleep(POLL_SECONDS)
    assert check()


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
