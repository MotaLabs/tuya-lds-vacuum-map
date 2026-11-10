import tomllib
from dataclasses import dataclass, fields
from importlib import resources
from pathlib import Path
from typing import Any

DEFAULT_LANGUAGE = "en"
TABLES = ("levels", "states")
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
    auto_start: str
    start_delay: str
    room_tap: str
    room_tap_clean: str
    room_tap_list: str
    start: str
    clear: str
    clean_time: str
    clean_area: str
    total_time: str
    total_area: str
    total_count: str
    levels: dict[str, str]
    states: dict[str, str]


def builtin_languages() -> list[str]:
    return sorted(item.name.removesuffix(".toml") for item in _FOLDER.iterdir() if item.name.endswith(".toml"))


def load_texts(language: str = DEFAULT_LANGUAGE) -> Texts:
    base = _read(DEFAULT_LANGUAGE)
    data = base if language == DEFAULT_LANGUAGE else _read(language)
    unknown = sorted(set(data) - {field.name for field in fields(Texts)})
    if unknown:
        raise TranslationError(f"unknown texts in {language!r}: {', '.join(unknown)}")
    _check_text({key: value for key, value in data.items() if key not in TABLES}, "texts", language)
    tables = {}
    for table in TABLES:
        values = data.get(table, {})
        if not isinstance(values, dict):
            raise TranslationError(f"{table} in {language!r} must be a table, not {values!r}")
        unknown_values = sorted(set(values) - set(base[table]))
        if unknown_values:
            raise TranslationError(f"unknown {table} in {language!r}: {', '.join(unknown_values)}")
        _check_text(values, table, language)
        tables[table] = {**base[table], **values}
    return Texts(**{**base, **data, **tables})


def _check_text(values: dict[str, Any], kind: str, language: str) -> None:
    wrong = sorted(key for key, value in values.items() if not isinstance(value, str))
    if wrong:
        raise TranslationError(f"{kind} in {language!r} that are not text: {', '.join(wrong)}")


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
