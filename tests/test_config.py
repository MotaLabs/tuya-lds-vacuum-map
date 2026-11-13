import pytest

from support.ascii_locale import run_in_an_ascii_locale
from tuya_lds_vacuum_map.config import CloudCredentials, ConfigError, RobotSettings, Settings, Timing

SHARED = {"HA_URL": "http://ha.local:8123", "HA_TOKEN": "token", "MQTT_HOST": "broker"}
REQUIRED = {**SHARED, "TUYA_DEVICE_ID": "abc123"}
CLOUD = {"TUYA_CLOUD_CLIENT_ID": "id", "TUYA_CLOUD_CLIENT_SECRET": "secret", "TUYA_CLOUD_REGION": "us"}


def robots_file(tmp_path, text: str) -> dict[str, str]:
    path = tmp_path / "robots.toml"
    path.write_text(text)
    return {**SHARED, "ROBOTS_FILE": str(path)}


def test_defaults():
    settings = Settings.from_env(REQUIRED)
    assert settings.robots == (RobotSettings("abc123"),)
    assert settings.robots[0].profile == "lds-robot"
    assert (settings.mqtt_host, settings.mqtt_port) == ("broker", 1883)
    assert settings.cloud is None
    assert settings.timing == Timing()
    assert settings.robot_topic("abc123") == "tuya_lds_vacuum_map/abc123"
    assert settings.availability_topic == "tuya_lds_vacuum_map/availability"


def test_one_robot_from_the_environment():
    env = {**REQUIRED, "DEVICE_NAME": "Hall", "ROBOT_PROFILE": "/profiles/mine.toml"}
    assert Settings.from_env(env).robots == (RobotSettings("abc123", "Hall", "/profiles/mine.toml"),)


def test_several_robots_from_a_file(tmp_path):
    env = robots_file(
        tmp_path,
        '[[robots]]\ndevice_id = "one"\nname = "Hall"\n\n'
        '[[robots]]\ndevice_id = "two"\nprofile = "mine.toml"\n\n'
        '[[robots]]\ndevice_id = "three"\nprofile = "/abs/other.toml"\n',
    )
    assert Settings.from_env(env).robots == (
        RobotSettings("one", "Hall"),
        RobotSettings("two", None, str(tmp_path / "mine.toml")),
        RobotSettings("three", None, "/abs/other.toml"),
    )


def test_robots_must_be_configured_once(tmp_path):
    with pytest.raises(ConfigError, match="no robot configured"):
        Settings.from_env(SHARED)
    with pytest.raises(ConfigError, match="not both"):
        Settings.from_env({**robots_file(tmp_path, '[[robots]]\ndevice_id = "one"\n'), "TUYA_DEVICE_ID": "two"})


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            '[[robots]]\ndevice_id = "one"\n[[robots]]\ndevice_id = "one"\n'
            + '[[robots]]\ndevice_id = "two"\n[[robots]]\ndevice_id = "two"\n',
            "^robot listed more than once: one, two$",
        ),
        ("robots = []\n", "no robots"),
        ('[[robots]]\nname = "Hall"\n', "invalid robots file"),
        ('[[robots]]\ndevice_id = ""\n', ": every robot needs a device_id$"),
        ('[[robots]]\ndevice_id = "one"\nprofile = 3\n', "profile of one must be a profile name"),
        ('[[robots]]\ndevice_id = "one"\ncolour = "red"\n', "colour"),
        ("[[robots]\n", "invalid robots file"),
        ('[robot]\ndevice_id = "one"\n', "invalid robots file"),
    ],
)
def test_bad_robots_files(tmp_path, text, message):
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(robots_file(tmp_path, text))


def test_missing_robots_file(tmp_path):
    with pytest.raises(ConfigError, match="unreadable"):
        Settings.from_env({**SHARED, "ROBOTS_FILE": str(tmp_path / "nope.toml")})


def test_missing_settings_are_listed():
    with pytest.raises(ConfigError, match="HA_TOKEN, MQTT_HOST"):
        Settings.from_env({"TUYA_DEVICE_ID": "abc123", "HA_URL": "http://ha.local:8123"})


def test_the_cloud_needs_every_credential():
    with pytest.raises(
        ConfigError,
        match=r"^the cloud map needs all of TUYA_CLOUD_CLIENT_ID, TUYA_CLOUD_CLIENT_SECRET, TUYA_CLOUD_REGION$",
    ):
        Settings.from_env({**REQUIRED, "TUYA_CLOUD_CLIENT_ID": "id", "TUYA_CLOUD_CLIENT_SECRET": "secret"})
    assert Settings.from_env({**REQUIRED, **CLOUD}).cloud == CloudCredentials("id", "secret", "us")
    with pytest.raises(ConfigError, match="unknown TUYA_CLOUD_REGION 'br', use one of: us, eu"):
        Settings.from_env({**REQUIRED, **CLOUD, "TUYA_CLOUD_REGION": "br"})


def test_optional_settings_and_timings_are_parsed():
    env = {
        **REQUIRED,
        "MQTT_PORT": "8883",
        "MAP_SCALE": "3",
        "TOPIC_PREFIX": "maps",
        "DOUBLE_TAP_SECONDS": "0.4",
        "SPOT_PREVIEW_SECONDS": "90",
        "CLOUD_IDLE_REFRESH_SECONDS": "600",
    }
    settings = Settings.from_env(env)
    assert (settings.mqtt_port, settings.map_scale) == (8883, 3)
    assert settings.robot_topic("abc123") == "maps/abc123"
    assert settings.timing.double_tap == 0.4
    assert settings.timing.spot_preview == 90
    assert settings.timing.cloud_refresh_idle == 600
    assert settings.timing.render_interval == Timing().render_interval


def test_invalid_numbers_are_config_errors():
    with pytest.raises(ConfigError, match="invalid setting"):
        Settings.from_env({**REQUIRED, "MQTT_PORT": "one"})
    with pytest.raises(ConfigError, match="invalid setting"):
        Settings.from_env({**REQUIRED, "RENDER_INTERVAL_SECONDS": "fast"})


def test_the_robots_file_is_read_as_utf8_whatever_the_locale(tmp_path):
    env = robots_file(tmp_path, '[[robots]]\ndevice_id = "one"\nname = "Suíte"\n')
    code = f"from tuya_lds_vacuum_map.config import Settings\nprint(Settings.from_env({env!r}).robots[0].name)"
    assert (Settings.from_env(env).robots[0].name, run_in_an_ascii_locale(code)) == ("Suíte", ["Suíte"])
