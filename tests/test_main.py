import asyncio
import logging
import os
import signal

import pytest

from tuya_lds_vacuum_map import __main__ as entry

ENV = {"HA_URL": "http://ha.local:8123", "HA_TOKEN": "token", "MQTT_HOST": "broker", "TUYA_DEVICE_ID": "dev123456"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ("ROBOTS_FILE", "TUYA_CLOUD_CLIENT_ID", "TUYA_CLOUD_CLIENT_SECRET", "TUYA_CLOUD_REGION", "LANGUAGE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {**ENV, "DATA_DIR": str(tmp_path)}.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_settings_errors_stop_the_start(env):
    env.delenv("HA_TOKEN")
    with pytest.raises(SystemExit, match="missing required settings: HA_TOKEN"):
        entry.main()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [("ROBOT_PROFILE", "no-such-robot", "unknown robot profile"), ("LANGUAGE", "xx", "unknown language")],
)
def test_profile_and_language_errors_stop_the_start(env, name, value, message):
    env.setenv(name, value)
    with pytest.raises(SystemExit, match=message):
        entry.main()


def test_main_builds_the_app_from_the_environment(env, caplog):
    caplog.set_level(logging.INFO, logger=entry.__name__)
    env.setattr(entry, "__version__", "9.9.9")
    env.setenv("LANGUAGE", "pt-BR")
    started = []

    async def run(app) -> None:
        started.append(app)

    env.setattr(entry, "run", run)
    entry.main()
    [app] = started
    session = app.sessions["dev123456"]
    assert (session.profile.name, session.texts.map) == ("lds-robot", "Mapa")
    assert "tuya-lds-vacuum-map 9.9.9" in caplog.text


def test_the_log_level_comes_from_the_environment(env):
    env.delenv("LOG_LEVEL", raising=False)
    configured = []
    env.setattr("logging.basicConfig", lambda **options: configured.append(options))

    async def run(app) -> None:
        pass

    env.setattr(entry, "run", run)
    entry.main()
    env.setenv("LOG_LEVEL", "DEBUG")
    entry.main()
    line = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    assert configured == [{"level": "INFO", "format": line}, {"level": "DEBUG", "format": line}]


@pytest.mark.parametrize("stop", [signal.SIGTERM, signal.SIGINT])
def test_a_stop_signal_ends_the_app(stop):
    class Forever:
        async def run(self) -> None:
            await asyncio.Event().wait()

    outcome = []

    def give_up(task: asyncio.Task) -> None:
        outcome.append("still running")
        task.cancel()

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        loop.call_later(0.01, os.kill, os.getpid(), stop)
        loop.call_later(1, give_up, asyncio.current_task())
        await entry.run(Forever())

    previous = signal.signal(stop, lambda *_: outcome.append("default handler"))
    try:
        asyncio.run(scenario())
    finally:
        signal.signal(stop, previous)
    assert outcome == []
