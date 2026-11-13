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
        await eventually(lambda: len(sleeps.delays) >= 8)
        await stop(task)
    assert sleeps.delays[:8] == [1, 2, 4, 8, 16, 32, 60, 60]
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


async def test_a_connection_without_subscriptions_resets_the_wait(monkeypatch):
    sleeps = RecordedSleeps()
    monkeypatch.setattr(homeassistant, "asyncio", sleeps)

    async def server(ws) -> None:
        await ws.send(json.dumps({"type": "auth_required"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_ok"}))
        await ws.close(1001)

    async def on_event(event_type: str, data: dict) -> None:
        raise AssertionError(event_type)

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run([], on_event))
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


async def test_a_subscription_left_unanswered_leaves_no_unread_reply(monkeypatch, caplog):
    monkeypatch.setattr(homeassistant, "asyncio", RecordedSleeps())
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


async def test_ready_runs_on_every_connection(monkeypatch):
    monkeypatch.setattr(homeassistant, "asyncio", RecordedSleeps())
    readies: list[bool] = []

    async def server(ws) -> None:
        await ws.send(json.dumps({"type": "auth_required"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_ok"}))
        message = json.loads(await ws.recv())
        await ws.send(json.dumps({"id": message["id"], "type": "result", "success": True}))
        await ws.close(1001)

    async def on_event(event_type: str, data: dict) -> None:
        raise AssertionError(event_type)

    async def on_ready() -> None:
        readies.append(ha.connected.is_set())

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run(["localtuya_status_update"], on_event, on_ready))
        await eventually(lambda: len(readies) >= 2)
        await stop(task)
    assert all(readies)


async def test_ready_runs_on_a_connection_without_subscriptions(monkeypatch):
    monkeypatch.setattr(homeassistant, "asyncio", RecordedSleeps())
    readies: list[bool] = []

    async def server(ws) -> None:
        await ws.send(json.dumps({"type": "auth_required"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_ok"}))
        await ws.wait_closed()

    async def on_event(event_type: str, data: dict) -> None:
        raise AssertionError(event_type)

    async def on_ready() -> None:
        readies.append(ha.connected.is_set())

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run([], on_event, on_ready))
        await eventually(lambda: readies == [True])
        await stop(task)


def answering_server(answers: dict[str, dict], events: dict[str, list] | None = None, received: list | None = None):
    async def server(ws) -> None:
        await ws.send(json.dumps({"type": "auth_required"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_ok"}))
        async for raw in ws:
            message = json.loads(raw)
            if received is not None:
                received.append(message)
            await ws.send(json.dumps({"id": message["id"], "type": "result", **answers[message["type"]]}))
            for event in (events or {}).get(message["type"], []):
                await ws.send(json.dumps({"id": message["id"], "type": "event", "event": event}))

    return server


async def no_event(event_type: str, data: dict) -> None:
    raise AssertionError(event_type)


async def test_a_request_returns_its_result_or_raises_its_error():
    answers = {
        "config/entity_registry/list": {"success": True, "result": [{"entity_id": "vacuum.robot"}]},
        "config/device_registry/list": {"success": False, "error": {"code": "unauthorized"}},
    }
    async with serve(answering_server(answers), "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run([], no_event))
        assert await ha.request({"type": "config/entity_registry/list"}) == [{"entity_id": "vacuum.robot"}]
        with pytest.raises(HomeAssistantError, match=r"config/device_registry/list failed: .*unauthorized"):
            await ha.request({"type": "config/device_registry/list"})
        await stop(task)


async def test_a_subscription_sends_its_events_to_its_handler_and_bus_events_go_on():
    received: list[dict] = []
    events: list[tuple] = []
    triggers: list = []
    answers = {"subscribe_events": {"success": True}, "subscribe_trigger": {"success": True}}
    sent = {
        "subscribe_events": [{"event_type": "localtuya_status_update", "data": {"dp": 105}}],
        "subscribe_trigger": [{"variables": {"trigger": {"platform": "state"}}}, None],
    }

    async def on_event(event_type: str, data: dict) -> None:
        events.append((event_type, data))

    async def on_trigger(message) -> None:
        triggers.append(message)

    async with serve(answering_server(answers, sent, received), "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run(["localtuya_status_update"], on_event))
        await ha.subscribe({"type": "subscribe_trigger", "trigger": {"platform": "state"}}, on_trigger)
        await eventually(lambda: len(triggers) == 2 and len(events) == 1)
        await stop(task)
    assert [message["type"] for message in received] == ["subscribe_events", "subscribe_trigger"]
    assert triggers == [{"variables": {"trigger": {"platform": "state"}}}, None]
    assert events == [("localtuya_status_update", {"dp": 105})]


async def test_a_refused_subscription_raises_and_keeps_no_handler():
    answers = {"subscribe_trigger": {"success": False, "error": {"code": "invalid_format"}}}

    async def on_trigger(message) -> None:
        raise AssertionError(message)

    async with serve(answering_server(answers), "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run([], no_event))
        with pytest.raises(HomeAssistantError, match=r"subscribe_trigger failed: .*invalid_format"):
            await ha.subscribe({"type": "subscribe_trigger"}, on_trigger)
        assert ha._handlers == {}
        await stop(task)


async def test_a_lost_connection_drops_every_subscription_handler(monkeypatch):
    monkeypatch.setattr(homeassistant, "asyncio", RecordedSleeps())
    connections: list = []

    async def server(ws) -> None:
        connections.append(ws)
        await ws.send(json.dumps({"type": "auth_required"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_ok"}))
        message = json.loads(await ws.recv())
        await ws.send(json.dumps({"id": message["id"], "type": "result", "success": True}))
        await ws.close(1001)

    async def on_trigger(message) -> None:
        raise AssertionError(message)

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run([], no_event))
        await ha.subscribe({"type": "subscribe_trigger"}, on_trigger)
        await eventually(lambda: len(connections) >= 2)
        assert ha._handlers == {}
        await stop(task)


async def test_a_subscription_on_a_closed_connection_keeps_no_handler():
    ha = HomeAssistant("http://127.0.0.1:1", "secret")
    ha._ws = ClosedSocket()
    ha.connected.set()

    async def on_trigger(message) -> None:
        raise AssertionError(message)

    with pytest.raises(HomeAssistantError, match="websocket closed"):
        await ha.subscribe({"type": "subscribe_trigger"}, on_trigger)
    assert ha._handlers == {}


async def test_a_refused_subscription_whose_handler_is_gone_still_raises_its_error():
    ha = HomeAssistant("http://127.0.0.1:1", "secret")

    async def refused(payload: dict, handler=None) -> dict:
        return {"id": 7, "type": "result", "success": False, "error": "gone"}

    async def on_trigger(message) -> None:
        raise AssertionError(message)

    ha._call = refused
    with pytest.raises(HomeAssistantError, match="subscribe_trigger failed: gone"):
        await ha.subscribe({"type": "subscribe_trigger"}, on_trigger)


@pytest.mark.parametrize(
    ("url", "websocket"),
    [
        ("http://ha.local:8123/", "ws://ha.local:8123/api/websocket"),
        ("https://http-proxy.local/", "wss://http-proxy.local/api/websocket"),
        ("http://NAS-BOX/", "ws://NAS-BOX/api/websocket"),
    ],
)
def test_the_websocket_url_comes_from_the_home_assistant_url(url, websocket):
    assert HomeAssistant(url, "secret").url == websocket


async def test_services_and_events_go_out_with_their_fields_and_ids_that_grow():
    received: list[dict] = []
    answers = {"call_service": {"success": True}, "fire_event": {"success": True}}
    async with serve(answering_server(answers, received=received), "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run([], no_event))
        await ha.call_service("localtuya", "set_dp", {"dp": 127})
        await ha.fire_event("custom", {"room": 1})
        await stop(task)
    assert [{key: value for key, value in message.items() if key != "id"} for message in received] == [
        {"type": "call_service", "domain": "localtuya", "service": "set_dp", "service_data": {"dp": 127}},
        {"type": "fire_event", "event_type": "custom", "event_data": {"room": 1}},
    ]
    ids = [message["id"] for message in received]
    assert all(isinstance(message_id, int) for message_id in ids)
    assert ids[0] < ids[1]


async def test_a_refused_service_names_itself_and_the_error():
    answers = {"call_service": {"success": False, "error": "unknown service"}}
    async with serve(answering_server(answers), "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run([], no_event))
        with pytest.raises(HomeAssistantError, match=r"^localtuya\.set_dp failed: unknown service$"):
            await ha.call_service("localtuya", "set_dp", {"dp": 127})
        await stop(task)


async def test_an_answer_larger_than_a_mebibyte_is_read():
    big = ["x" * 2_000_000]
    answers = {"config/entity_registry/list": {"success": True, "result": big}}
    async with serve(answering_server(answers), "127.0.0.1", 0, max_size=None) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run([], no_event))
        assert await ha.request({"type": "config/entity_registry/list"}) == big
        await stop(task)


async def test_a_result_for_an_id_nobody_waits_for_is_skipped():
    async def server(ws) -> None:
        await ws.send(json.dumps({"type": "auth_required"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_ok"}))
        async for raw in ws:
            message = json.loads(raw)
            await ws.send(json.dumps({"id": 999, "type": "result", "success": True, "result": None}))
            await ws.send(json.dumps({"id": message["id"], "type": "result", "success": True, "result": "ok"}))

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run([], no_event))
        async with asyncio.timeout(5):
            assert await ha.request({"type": "ping"}) == "ok"
        await stop(task)


async def test_a_call_left_unanswered_when_the_connection_drops_is_lost():
    async def server(ws) -> None:
        await ws.send(json.dumps({"type": "auth_required"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_ok"}))
        await ws.recv()

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run([], no_event))
        with pytest.raises(HomeAssistantError, match=r"^connection lost$"):
            await ha.call_service("localtuya", "set_dp", {"dp": 127})
        await stop(task)


async def test_a_call_with_no_answer_gives_up_after_the_call_timeout(monkeypatch):
    monkeypatch.setattr(homeassistant, "CALL_TIMEOUT_SECONDS", 0.1)

    async def server(ws) -> None:
        await ws.send(json.dumps({"type": "auth_required"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "auth_ok"}))
        await ws.wait_closed()

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        ha = HomeAssistant(f"http://127.0.0.1:{port}", "secret")
        task = asyncio.create_task(ha.run([], no_event))
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ha.call_service("localtuya", "set_dp", {"dp": 127}), 3)
        assert loop.time() - started < 1
        await stop(task)
