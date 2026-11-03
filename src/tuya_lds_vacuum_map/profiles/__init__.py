import tomllib
from dataclasses import dataclass
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


@dataclass(frozen=True)
class States:
    moving: frozenset[str]
    spot: frozenset[str]


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
class Profile:
    name: str
    description: str
    data_points: DataPoints
    states: States
    cells: CellCodes
    geometry: Geometry
    levels: Levels


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
        text = path.read_text(encoding="utf-8")
    else:
        resource = resources.files(__package__) / f"{reference.replace('-', '_')}.toml"
        if not resource.is_file():
            raise ProfileError(f"unknown robot profile {reference!r}, built-in ones: {', '.join(builtin_profiles())}")
        text = resource.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    parent = data.pop("extends", None)
    return _merge(_read(parent, seen | {reference}), data) if parent else data


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        nested = isinstance(value, dict) and isinstance(merged.get(key), dict)
        merged[key] = _merge(merged[key], value) if nested else value
    return merged


def _build(data: dict[str, Any]) -> Profile:
    states = data["states"]
    levels = data.get("levels", {})
    return Profile(
        name=data["name"],
        description=data.get("description", ""),
        data_points=DataPoints(**data["data_points"]),
        states=States(moving=frozenset(states["moving"]), spot=frozenset(states.get("spot", []))),
        cells=CellCodes(**data["cells"]),
        geometry=Geometry(**data["geometry"]),
        levels=Levels(
            fan=dict(levels.get("fan", {})), water={int(key): value for key, value in levels.get("water", {}).items()}
        ),
    )
