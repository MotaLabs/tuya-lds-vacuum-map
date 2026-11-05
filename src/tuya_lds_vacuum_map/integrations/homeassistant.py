import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import websockets

log = logging.getLogger(__name__)

CALL_TIMEOUT_SECONDS = 30

EventHandler = Callable[[str, dict], Awaitable[None]]


class HomeAssistantError(Exception):
    pass


class HomeAssistant:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/").replace("http", "ws", 1) + "/api/websocket"
        self.token = token
        self.connected = asyncio.Event()
        self._ws = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}

    async def run(self, event_types: list[str], on_event: EventHandler) -> None:
        backoff = 1
        while True:
            try:
                async with websockets.connect(self.url, max_size=None) as ws:
                    await ws.recv()
                    await ws.send(json.dumps({"type": "auth", "access_token": self.token}))
                    reply = json.loads(await ws.recv())
                    if reply.get("type") != "auth_ok":
                        raise HomeAssistantError(f"authentication failed: {reply.get('type')}")
                    self._ws = ws
                    for event_type in event_types:
                        await self._send({"type": "subscribe_events", "event_type": event_type})
                    self.connected.set()
                    backoff = 1
                    log.info("connected to Home Assistant")
                    async for raw in ws:
                        message = json.loads(raw)
                        if message.get("type") == "event":
                            event = message["event"]
                            await on_event(event["event_type"], event.get("data") or {})
                        elif message.get("type") == "result":
                            future = self._pending.pop(message.get("id"), None)
                            if future and not future.done():
                                future.set_result(message)
            except (OSError, websockets.WebSocketException, HomeAssistantError) as error:
                log.warning("Home Assistant connection lost: %s", error)
            finally:
                self.connected.clear()
                self._ws = None
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(HomeAssistantError("connection lost"))
                self._pending.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def call_service(self, domain: str, service: str, data: dict) -> None:
        reply = await self._call({"type": "call_service", "domain": domain, "service": service, "service_data": data})
        if not reply.get("success"):
            raise HomeAssistantError(f"{domain}.{service} failed: {reply.get('error')}")

    async def fire_event(self, event_type: str, data: dict) -> None:
        reply = await self._call({"type": "fire_event", "event_type": event_type, "event_data": data})
        if not reply.get("success"):
            raise HomeAssistantError(f"fire_event {event_type} failed: {reply.get('error')}")

    async def _call(self, payload: dict) -> dict:
        async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
            await self.connected.wait()
            return await (await self._send(payload))

    async def _send(self, payload: dict) -> asyncio.Future:
        message_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        await self._ws.send(json.dumps({**payload, "id": message_id}))
        return future
