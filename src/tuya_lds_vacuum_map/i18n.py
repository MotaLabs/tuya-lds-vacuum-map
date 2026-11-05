import tomllib
from dataclasses import dataclass, fields
from importlib import resources
from pathlib import Path
from typing import Any

DEFAULT_LANGUAGE = "en"
_FOLDER = resources.files(__package__) / "translations"


class TranslationError(Exception):
    pass


@dataclass(frozen=True)
class Texts:
    map: str
    map_source: str
    source_auto: str
    source_local: str
    room_settings: str
    room_color: str
    random_room_color: str
    no_room: str
    levels: dict[str, str]


def builtin_languages() -> list[str]:
    return sorted(item.name.removesuffix(".toml") for item in _FOLDER.iterdir() if item.name.endswith(".toml"))


def load_texts(language: str = DEFAULT_LANGUAGE) -> Texts:
    base = _read(DEFAULT_LANGUAGE)
    data = base if language == DEFAULT_LANGUAGE else _read(language)
    unknown = sorted(set(data) - {field.name for field in fields(Texts)})
    if unknown:
        raise TranslationError(f"unknown texts in {language!r}: {', '.join(unknown)}")
    levels = data.get("levels", {})
    unknown_levels = sorted(set(levels) - set(base["levels"]))
    if unknown_levels:
        raise TranslationError(f"unknown levels in {language!r}: {', '.join(unknown_levels)}")
    return Texts(**{**base, **data, "levels": {**base["levels"], **levels}})


def _read(language: str) -> dict[str, Any]:
    if language.endswith(".toml"):
        path = Path(language)
        if not path.is_file():
            raise TranslationError(f"translation file not found: {language}")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise TranslationError(f"translation file {language} cannot be read: {error}") from error
    else:
        resource = _FOLDER / f"{language}.toml"
        if not resource.is_file():
            raise TranslationError(f"unknown language {language!r}, built-in ones: {', '.join(builtin_languages())}")
        text = resource.read_text(encoding="utf-8")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise TranslationError(f"invalid translation {language!r}: {error}") from error
