import tomllib
from importlib import resources

import pytest

from tuya_lds_vacuum_map.i18n import DEFAULT_LANGUAGE, TranslationError, builtin_languages, load_texts

FOLDER = resources.files("tuya_lds_vacuum_map") / "translations"


def keys(language: str) -> tuple[set[str], set[str], set[str]]:
    data = tomllib.loads((FOLDER / f"{language}.toml").read_text(encoding="utf-8"))
    return set(data), set(data["levels"]), set(data["states"])


def test_built_in_languages():
    assert {"en", "pt-BR"} <= set(builtin_languages())
    assert load_texts().map == "Map"
    assert load_texts("pt-BR").levels["quiet"] == "Eco"
    assert load_texts("pt-BR").states["emptying"] == "Esvaziando na base"


@pytest.mark.parametrize("language", builtin_languages())
def test_every_built_in_language_is_complete(language):
    assert keys(language) == keys(DEFAULT_LANGUAGE)


def test_a_file_can_translate_part_of_the_texts(tmp_path):
    path = tmp_path / "mine.toml"
    path.write_text('map = "Karte"\n[levels]\nquiet = "Leise"\n[states]\ncharging = "Lädt"\n')
    texts = load_texts(str(path))
    assert (texts.map, texts.map_source) == ("Karte", "Map source")
    assert (texts.levels["quiet"], texts.levels["high"]) == ("Leise", "High")
    assert (texts.states["charging"], texts.states["emptying"]) == ("Lädt", "Emptying the bin")


def test_a_file_with_the_texts_of_a_retired_entity_still_loads(tmp_path):
    path = tmp_path / "mine.toml"
    path.write_text('map = "Karte"\nroom_tap = "Tippen"\nroom_tap_clean = "Putzen"\nroom_tap_list = "Liste"\n')
    texts = load_texts(str(path))
    assert texts.map == "Karte"
    assert not hasattr(texts, "room_tap")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('mapp = "Karte"\n', "unknown texts in .*: mapp"),
        ('[levels]\nturbo = "Turbo"\n', "unknown levels in .*: turbo"),
        ('[states]\nflying = "Fliegt"\n', "unknown states in .*: flying"),
        ("map = \n", "invalid translation"),
        ("levels = []\n", "levels in .* must be a table, not \\[\\]"),
        ("states = 5\n", "states in .* must be a table, not 5"),
        ("[levels]\nquiet = 1\n", "levels in .* that are not text: quiet"),
        ("[levels]\nquiet = 1\nmax = 2\n", "levels in .* that are not text: max, quiet"),
        ("[states.charging]\nshort = 'Lädt'\n", "states in .* that are not text: charging"),
        ("map = 5\n", "texts in .* that are not text: map"),
        ("map = ['Karte']\nno_room = true\n", "texts in .* that are not text: map, no_room"),
    ],
)
def test_bad_translation_files(tmp_path, text, message):
    path = tmp_path / "mine.toml"
    path.write_text(text)
    with pytest.raises(TranslationError, match=message):
        load_texts(str(path))


def test_a_translation_file_that_is_not_utf8_is_an_error(tmp_path):
    path = tmp_path / "mine.toml"
    path.write_bytes(b'map = "\xff"\n')
    with pytest.raises(TranslationError, match="cannot be read"):
        load_texts(str(path))


def test_unknown_languages_are_errors(tmp_path):
    with pytest.raises(TranslationError, match=r"built-in ones: .*pt-BR"):
        load_texts("xx")
    with pytest.raises(TranslationError, match="not found"):
        load_texts(str(tmp_path / "missing.toml"))
