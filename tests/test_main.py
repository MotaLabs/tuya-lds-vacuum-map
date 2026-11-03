import asyncio
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


def test_main_builds_the_app_from_the_environment(env):
    env.setenv("LANGUAGE", "pt-BR")
    started = []

    async def run(app) -> None:
        started.append(app)

    env.setattr(entry, "run", run)
    entry.main()
    [app] = started
    session = app.sessions["dev123456"]
    assert (session.profile.name, session.texts.map) == ("lds-robot", "Mapa")


def test_a_stop_signal_ends_the_app():
    class Forever:
        async def run(self) -> None:
            await asyncio.Event().wait()

    async def scenario() -> None:
        asyncio.get_running_loop().call_later(0.01, os.kill, os.getpid(), signal.SIGINT)
        await entry.run(Forever())

    asyncio.run(scenario())
