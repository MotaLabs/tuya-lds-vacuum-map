import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from tuya_lds_vacuum_map.integrations.homeassistant import HomeAssistant, HomeAssistantError


async def test_subscribes_delivers_events_and_calls_services():
    received: list[dict] = []
    events: list[tuple] = []

    async def server(ws) -> None:
        await ws.send(json.dumps({"type": "auth_required"}))
        received.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"type": "auth_ok"}))
        async for raw in ws:
            message = json.loads(raw)
            received.append(message)
            await ws.send(
                json.dumps(
                    {"id": message["id"], "type": "result", "success": message["type"] != "fire_event", "error": "nope"}
                )
            )
            if message["type"] == "subscribe_events":
                await ws.send(
                    json.dumps({"type": "event", "event": {"event_type": message["event_type"], "data": {"dp": 105}}})
                )

    async def on_event(event_type: str, data: dict) -> None:
        events.append((event_type, data))

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}/", "secret")
        task = asyncio.create_task(ha.run(["localtuya_device_dp_triggered"], on_event))
        await ha.call_service("localtuya", "set_dp", {"dp": 127})
        with pytest.raises(HomeAssistantError, match="nope"):
            await ha.fire_event("custom", {})
        task.cancel()

    assert received[0] == {"type": "auth", "access_token": "secret"}
    assert [message["type"] for message in received[1:]] == ["subscribe_events", "call_service", "fire_event"]
    assert received[2]["service_data"] == {"dp": 127}
    assert events == [("localtuya_device_dp_triggered", {"dp": 105})]
