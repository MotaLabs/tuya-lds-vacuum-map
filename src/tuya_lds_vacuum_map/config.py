import dataclasses
import os
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .integrations.tuya_cloud import ENDPOINTS
from .profiles import DEFAULT_PROFILE

Parser = Callable[[str], Any]

REQUIRED = {"ha_url": "HA_URL", "ha_token": "HA_TOKEN", "mqtt_host": "MQTT_HOST"}
CLOUD = ("TUYA_CLOUD_CLIENT_ID", "TUYA_CLOUD_CLIENT_SECRET", "TUYA_CLOUD_REGION")
OPTIONAL: dict[str, tuple[str, Parser]] = {
    "mqtt_port": ("MQTT_PORT", int),
    "mqtt_username": ("MQTT_USERNAME", str),
    "mqtt_password": ("MQTT_PASSWORD", str),
    "language": ("LANGUAGE", str),
    "data_dir": ("DATA_DIR", str),
    "font_path": ("FONT_PATH", str),
    "map_scale": ("MAP_SCALE", int),
    "topic_prefix": ("TOPIC_PREFIX", str),
    "discovery_prefix": ("DISCOVERY_PREFIX", str),
}
TIMING: dict[str, tuple[str, Parser]] = {
    "cloud_refresh_moving": ("CLOUD_REFRESH_SECONDS", float),
    "cloud_refresh_idle": ("CLOUD_IDLE_REFRESH_SECONDS", float),
    "cloud_after_cleaning": ("CLOUD_AFTER_CLEANING_SECONDS", float),
    "double_tap": ("DOUBLE_TAP_SECONDS", float),
    "render_interval": ("RENDER_INTERVAL_SECONDS", float),
    "save_interval": ("SAVE_INTERVAL_SECONDS", float),
}
ROBOTS_FILE = "ROBOTS_FILE"
SINGLE_ROBOT = {"device_id": "TUYA_DEVICE_ID", "name": "DEVICE_NAME", "profile": "ROBOT_PROFILE"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Timing:
    cloud_refresh_moving: float = 60
    cloud_refresh_idle: float = 6 * 3600
    cloud_after_cleaning: float = 20
    double_tap: float = 0.6
    render_interval: float = 1.5
    save_interval: float = 30


@dataclass(frozen=True)
class CloudCredentials:
    client_id: str
    client_secret: str
    region: str


@dataclass(frozen=True)
class RobotSettings:
    device_id: str
    name: str | None = None
    profile: str = DEFAULT_PROFILE


@dataclass(frozen=True)
class Settings:
    ha_url: str
    ha_token: str
    mqtt_host: str
    robots: tuple[RobotSettings, ...]
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    cloud: CloudCredentials | None = None
    language: str = "en"
    data_dir: str = "/data"
    font_path: str | None = None
    map_scale: int = 4
    topic_prefix: str = "tuya_lds_vacuum_map"
    discovery_prefix: str = "homeassistant"
    timing: Timing = field(default_factory=Timing)

    @property
    def availability_topic(self) -> str:
        return f"{self.topic_prefix}/availability"

    def robot_topic(self, device_id: str) -> str:
        return f"{self.topic_prefix}/{device_id}"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        missing = [name for name in REQUIRED.values() if not env.get(name)]
        if missing:
            raise ConfigError("missing required settings: " + ", ".join(missing))
        cloud_values = [env.get(name, "") for name in CLOUD]
        if any(cloud_values) and not all(cloud_values):
            raise ConfigError("the cloud map needs all of " + ", ".join(CLOUD))
        if all(cloud_values) and cloud_values[2] not in ENDPOINTS:
            raise ConfigError(f"unknown {CLOUD[2]} {cloud_values[2]!r}, use one of: {', '.join(ENDPOINTS)}")
        try:
            optional = _parse(env, OPTIONAL)
            timing = Timing(**_parse(env, TIMING))
        except ValueError as error:
            raise ConfigError(f"invalid setting: {error}") from error
        return cls(
            **{field_name: env[name] for field_name, name in REQUIRED.items()},
            robots=robots_from_env(env),
            cloud=CloudCredentials(*cloud_values) if all(cloud_values) else None,
            timing=timing,
            **optional,
        )


def _parse(env: Mapping[str, str], table: dict[str, tuple[str, Parser]]) -> dict[str, Any]:
    return {field_name: parse(env[name]) for field_name, (name, parse) in table.items() if env.get(name)}


def robots_from_env(env: Mapping[str, str]) -> tuple[RobotSettings, ...]:
    single_id = SINGLE_ROBOT["device_id"]
    path = env.get(ROBOTS_FILE)
    if path and env.get(single_id):
        raise ConfigError(f"set {single_id} for one robot or {ROBOTS_FILE} for several, not both")
    if path:
        return robots_from_file(Path(path))
    if not env.get(single_id):
        raise ConfigError(f"no robot configured: set {single_id} for one robot or {ROBOTS_FILE} for several")
    return (RobotSettings(**{field_name: env[name] for field_name, name in SINGLE_ROBOT.items() if env.get(name)}),)


def robots_from_file(path: Path) -> tuple[RobotSettings, ...]:
    try:
        entries = tomllib.loads(path.read_text(encoding="utf-8"))["robots"]
        robots = tuple(_robot_entry(entry, path.parent) for entry in entries)
    except OSError as error:
        raise ConfigError(f"robots file unreadable: {error}") from error
    except (tomllib.TOMLDecodeError, KeyError, TypeError, ValueError) as error:
        raise ConfigError(f"invalid robots file {path}: {error}") from error
    if not robots:
        raise ConfigError(f"no robots in {path}")
    ids = [robot.device_id for robot in robots]
    repeated = sorted({device_id for device_id in ids if ids.count(device_id) > 1})
    if repeated:
        raise ConfigError("robot listed more than once: " + ", ".join(repeated))
    return robots


def _robot_entry(entry: dict[str, Any], folder: Path) -> RobotSettings:
    robot = RobotSettings(**entry)
    if not isinstance(robot.device_id, str) or not robot.device_id:
        raise ValueError("every robot needs a device_id")
    if not isinstance(robot.profile, str):
        raise ValueError(f"the profile of {robot.device_id} must be a profile name or a .toml file")
    if robot.profile.endswith(".toml") and not Path(robot.profile).is_absolute():
        return dataclasses.replace(robot, profile=str(folder / robot.profile))
    return robot
