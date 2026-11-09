import tomllib
from dataclasses import dataclass, fields
from importlib import resources
from pathlib import Path
from typing import Any

DEFAULT_PROFILE = "lds-robot"


class ProfileError(Exception):
    pass


@dataclass(frozen=True)
class DataPoints:
    state: int
    path: int
    raw: int
    room_mode: int | None = None
    clean_time: int | None = None
    clean_area: int | None = None
    total_time: int | None = None
    total_area: int | None = None
    total_count: int | None = None


@dataclass(frozen=True)
class States:
    moving: frozenset[str]
    spot: frozenset[str]
    names: dict[str, str]


@dataclass(frozen=True)
class CellCodes:
    wall: int
    unknown: int
    room_min: int
    room_max: int
    floor: int

    def is_room(self, value: int | None) -> bool:
        return value is not None and self.room_min <= value <= self.room_max


@dataclass(frozen=True)
class Geometry:
    cleaning_radius_mm: int
    spot_size_mm: int
    coverage_cell_mm: int


@dataclass(frozen=True)
class Levels:
    fan: dict[str, str]
    water: dict[int, str]


@dataclass(frozen=True)
class Units:
    time: str = "s"
    area: str = "m²"


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    data_points: DataPoints
    states: States
    cells: CellCodes
    geometry: Geometry
    levels: Levels
    units: Units = Units()


def builtin_profiles() -> list[str]:
    folder = resources.files(__package__)
    return sorted(
        item.name.removesuffix(".toml").replace("_", "-") for item in folder.iterdir() if item.name.endswith(".toml")
    )


def load_profile(reference: str = DEFAULT_PROFILE) -> Profile:
    data = _read(reference, frozenset())
    try:
        return _build(data)
    except (KeyError, TypeError, ValueError) as error:
        raise ProfileError(f"invalid robot profile {reference!r}: {error}") from error


def _read(reference: str, seen: frozenset[str]) -> dict[str, Any]:
    if reference in seen:
        raise ProfileError(f"robot profile {reference!r} extends itself")
    if reference.endswith(".toml"):
        path = Path(reference)
        if not path.is_file():
            raise ProfileError(f"robot profile file not found: {reference}")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ProfileError(f"robot profile file {reference} cannot be read: {error}") from error
    else:
        resource = resources.files(__package__) / f"{reference.replace('-', '_')}.toml"
        if not resource.is_file():
            raise ProfileError(f"unknown robot profile {reference!r}, built-in ones: {', '.join(builtin_profiles())}")
        text = resource.read_text(encoding="utf-8")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ProfileError(f"robot profile {reference!r} is not valid TOML: {error}") from error
    parent = data.pop("extends", None)
    if parent is not None and not isinstance(parent, str):
        raise ProfileError(f"robot profile {reference!r} extends {parent!r}, which is not a profile name")
    return _merge(_read(parent, seen | {reference}), data) if parent else data


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        nested = isinstance(value, dict) and isinstance(merged.get(key), dict)
        merged[key] = _merge(merged[key], value) if nested else value
    return merged


def _table(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a table, not {value!r}")
    return value


def _numbers[T](cls: type[T], value: Any, name: str) -> T:
    numbers = cls(**_table(value, name))
    for field in fields(numbers):
        number = getattr(numbers, field.name)
        if number is None and field.default is None:
            continue
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValueError(f"{name}.{field.name} must be a whole number, not {number!r}")
    return numbers


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text, not {value!r}")
    return value


def _codes(value: Any, name: str) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(code, str) for code in value):
        raise ValueError(f"{name} must be a list of state codes, not {value!r}")
    return frozenset(value)


def _names(table: dict[str, Any], kind: str) -> dict[str, str]:
    wrong = sorted(key for key, name in table.items() if not isinstance(name, str))
    if wrong:
        raise ValueError(f"the {kind} names of {', '.join(wrong)} are not text")
    return dict(table)


def _build(data: dict[str, Any]) -> Profile:
    states = _table(data["states"], "states")
    levels = _table(data.get("levels", {}), "levels")
    units = _table(data.get("units", {}), "units")
    water = _names(_table(levels.get("water", {}), "levels.water"), "water level")
    return Profile(
        name=_text(data["name"], "name"),
        description=_text(data.get("description", ""), "description"),
        data_points=_numbers(DataPoints, data["data_points"], "data_points"),
        states=States(
            moving=_codes(states["moving"], "states.moving"),
            spot=_codes(states.get("spot", []), "states.spot"),
            names=_names(_table(states.get("names", {}), "states.names"), "state"),
        ),
        cells=_numbers(CellCodes, data["cells"], "cells"),
        geometry=_numbers(Geometry, data["geometry"], "geometry"),
        levels=Levels(
            fan=_names(_table(levels.get("fan", {}), "levels.fan"), "fan level"),
            water={int(key): value for key, value in water.items()},
        ),
        units=Units(**{key: _text(value, f"units.{key}") for key, value in units.items()}),
    )
