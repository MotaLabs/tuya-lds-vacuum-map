import asyncio
import gc
import json

import pytest
from websockets.asyncio.server import serve

from support.closing_server import ClosingServer
from support.fakes import ClosedSocket
from support.waiting import eventually, stop
from tuya_lds_vacuum_map.integrations import homeassistant
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


async def test_a_call_on_a_closed_connection_is_a_home_assistant_error():
    ha = HomeAssistant("http://127.0.0.1:1", "secret")
    ha._ws = ClosedSocket()
    ha.connected.set()
    with pytest.raises(HomeAssistantError, match="websocket closed"):
        await ha.call_service("localtuya", "set_dp", {"dp": 127})
    with pytest.raises(HomeAssistantError, match="websocket closed"):
        await ha.fire_event("custom", {})
    assert ha._pending == {}


@pytest.mark.parametrize("close_code", [1001, 1011])
async def test_a_call_while_the_connection_closes_is_a_home_assistant_error(close_code):
    async with ClosingServer(close_code) as server:
        ha = HomeAssistant(f"http://127.0.0.1:{server.port}", "secret")

        async def on_event(event_type: str, data: dict) -> None:
            raise AssertionError(event_type)

        task = asyncio.create_task(ha.run([], on_event))
        await server.authenticated.wait()
        await ha.connected.wait()
        server.close.set()
        await server.closing.wait()
        assert ha.connected.is_set()
        call = asyncio.create_task(ha.call_service("localtuya", "set_dp", {"dp": 127}))
        await asyncio.sleep(0.05)
        assert not call.done()
        [reply] = ha._pending.values()
        server.drop.set()
        with pytest.raises(HomeAssistantError, match=f"websocket closed: received {close_code}"):
            await call
        assert ha._pending == {}
        await stop(task)
    assert reply.done()
    assert not reply._log_traceback


class RecordedSleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __getattr__(self, name: str):
        return getattr(asyncio, name)

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.sleep(0)


async def test_a_refused_subscription_reconnects_ever_more_slowly(monkeypatch, caplog):
    sleeps = RecordedSleeps()
    monkeypatch.setattr(homeassistant, "asyncio", sleeps)

    async def server(ws) -> None:
        await ws.send(json.dumps({"type": "auth_required"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_ok"}))
        message = json.loads(await ws.recv())
        await ws.send(json.dumps({"id": message["id"], "type": "result", "success": False, "error": "unknown"}))
        await ws.wait_closed()

    async def on_event(event_type: str, data: dict) -> None:
        raise AssertionError(event_type)

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run(["localtuya_status_update"], on_event))
        await eventually(lambda: len(sleeps.delays) >= 4)
        await stop(task)
    assert sleeps.delays[:4] == [1, 2, 4, 8]
    assert "connected to Home Assistant" not in caplog.text
    assert "Home Assistant connection lost: subscription failed: unknown" in caplog.text


async def test_an_accepted_subscription_resets_the_wait(monkeypatch):
    sleeps = RecordedSleeps()
    monkeypatch.setattr(homeassistant, "asyncio", sleeps)

    async def server(ws) -> None:
        await ws.send(json.dumps({"type": "auth_required"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_ok"}))
        message = json.loads(await ws.recv())
        await ws.send(json.dumps({"id": message["id"], "type": "result", "success": True}))
        await ws.close(1001)

    async def on_event(event_type: str, data: dict) -> None:
        raise AssertionError(event_type)

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run(["localtuya_status_update"], on_event))
        await eventually(lambda: len(sleeps.delays) >= 4)
        await stop(task)
    assert sleeps.delays[:4] == [1, 1, 1, 1]


async def test_the_connection_counts_once_every_subscription_is_accepted():
    answer = asyncio.Event()

    async def server(ws) -> None:
        await ws.send(json.dumps({"type": "auth_required"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_ok"}))
        first = json.loads(await ws.recv())
        second = json.loads(await ws.recv())
        await ws.send(json.dumps({"id": first["id"], "type": "result", "success": True}))
        await answer.wait()
        await ws.send(json.dumps({"id": second["id"], "type": "result", "success": True}))
        await ws.wait_closed()

    async def on_event(event_type: str, data: dict) -> None:
        raise AssertionError(event_type)

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run(["localtuya_device_dp_triggered", "localtuya_status_update"], on_event))
        try:
            await asyncio.sleep(0.1)
            assert not ha.connected.is_set()
        finally:
            answer.set()
        await eventually(ha.connected.is_set)
        await stop(task)


async def test_a_subscription_left_unanswered_leaves_no_unread_reply(caplog):
    gc.collect()
    caplog.clear()
    connections = []

    async def server(ws) -> None:
        connections.append(ws)
        await ws.send(json.dumps({"type": "auth_required"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_ok"}))
        await ws.recv()
        await ws.close(1001)

    async def on_event(event_type: str, data: dict) -> None:
        raise AssertionError(event_type)

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run(["localtuya_status_update"], on_event))
        await eventually(lambda: len(connections) >= 2)
        await stop(task)
    gc.collect()
    assert "Future exception was never retrieved" not in caplog.text


async def test_a_rejected_token_is_reported(caplog):
    async def server(ws) -> None:
        await ws.send(json.dumps({"type": "auth_required"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_invalid", "message": "Invalid access token"}))

    async def on_event(event_type: str, data: dict) -> None:
        raise AssertionError(event_type)

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "wrong")
        task = asyncio.create_task(ha.run([], on_event))
        await eventually(lambda: "authentication failed: auth_invalid" in caplog.text)
        assert not ha.connected.is_set()
        await stop(task)
