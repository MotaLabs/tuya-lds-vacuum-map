import re
import tempfile
from pathlib import Path

import pytest
from hypothesis import example, given

from support.strategies import profile_overrides
from support.toml import toml_document
from tuya_lds_vacuum_map.i18n import load_texts
from tuya_lds_vacuum_map.profiles import DEFAULT_PROFILE, Profile, ProfileError, builtin_profiles, load_profile


def test_the_default_profile_is_built_in():
    assert DEFAULT_PROFILE in builtin_profiles()
    profile = load_profile()
    assert profile.name == "lds-robot"
    assert (
        profile.data_points.state,
        profile.data_points.path,
        profile.data_points.raw,
        profile.data_points.room_mode,
    ) == (105, 123, 127, 144)
    assert "totaling" in profile.states.moving
    assert profile.states.spot <= profile.states.moving
    assert profile.levels.water == {1: "low", 2: "medium", 3: "high"}
    assert profile.states.names["dustcenterworking"] == "emptying"
    assert profile.states.moving <= set(profile.states.names)
    statistics = profile.data_points
    assert (
        statistics.clean_time,
        statistics.clean_area,
        statistics.total_time,
        statistics.total_area,
        statistics.total_count,
    ) == (107, 108, 116, 117, 118)
    assert (profile.units.time, profile.units.area) == ("s", "m²")


@pytest.mark.parametrize("name", builtin_profiles())
def test_every_state_a_built_in_profile_names_has_a_text(name):
    assert set(load_profile(name).states.names.values()) <= set(load_texts().states)


def test_cell_codes_know_the_room_range():
    cells = load_profile().cells
    assert cells.is_room(1)
    assert cells.is_room(126)
    assert not cells.is_room(127)
    assert not cells.is_room(cells.wall)
    assert not cells.is_room(cells.unknown)
    assert not cells.is_room(None)


def test_a_file_can_extend_a_built_in_profile(tmp_path):
    custom = tmp_path / "custom.toml"
    custom.write_text('extends = "lds-robot"\nname = "custom"\n[data_points]\nraw = 200\n[levels.water]\n4 = "high"\n')
    profile = load_profile(str(custom))
    base = load_profile()
    assert profile.name == "custom"
    assert profile.data_points.raw == 200
    assert profile.data_points.state == base.data_points.state
    assert profile.geometry == base.geometry
    assert profile.levels.water == {**base.levels.water, 4: "high"}


def test_profiles_can_chain(tmp_path):
    (tmp_path / "middle.toml").write_text('extends = "lds-robot"\nname = "middle"\n[geometry]\nspot_size_mm = 1000\n')
    (tmp_path / "leaf.toml").write_text(f'extends = "{tmp_path / "middle.toml"}"\nname = "leaf"\n')
    profile = load_profile(str(tmp_path / "leaf.toml"))
    assert (profile.name, profile.geometry.spot_size_mm) == ("leaf", 1000)


def test_a_cycle_is_an_error(tmp_path):
    first, second = tmp_path / "first.toml", tmp_path / "second.toml"
    first.write_text(f'extends = "{second}"\nname = "first"\n')
    second.write_text(f'extends = "{first}"\nname = "second"\n')
    with pytest.raises(ProfileError, match="extends itself"):
        load_profile(str(first))


def test_unknown_profiles_are_errors(tmp_path):
    with pytest.raises(ProfileError, match="lds-robot"):
        load_profile("no-such-robot")
    with pytest.raises(ProfileError, match="not found"):
        load_profile(str(tmp_path / "missing.toml"))


def test_an_incomplete_profile_is_an_error(tmp_path):
    broken = tmp_path / "broken.toml"
    broken.write_text('name = "broken"\n[data_points]\nstate = 1\n')
    with pytest.raises(ProfileError, match="broken"):
        load_profile(str(broken))


@pytest.mark.parametrize("table", ["[states.names]\nmop = 1\n", "[states.names.mop]\nwet = true\n"])
def test_a_state_name_must_be_text(tmp_path, table):
    path = tmp_path / "robot.toml"
    path.write_text(f'extends = "lds-robot"\nname = "custom"\n{table}')
    with pytest.raises(ProfileError, match="state names of mop are not text"):
        load_profile(str(path))


@pytest.mark.parametrize(
    ("table", "reason"),
    [
        ('[states]\nnames = "emptying"\n', "states.names must be a table"),
        ("[states]\nnames = [1, 2]\n", "states.names must be a table"),
        ('[levels]\nwater = "high"\n', "levels.water must be a table"),
        ('[levels]\nfan = ["quiet"]\n', "levels.fan must be a table"),
        ('levels = "high"\n', "levels must be a table"),
    ],
)
def test_a_table_of_another_type_is_a_profile_error(tmp_path, table, reason):
    path = tmp_path / "robot.toml"
    path.write_text(f'extends = "lds-robot"\nname = "custom"\n{table}')
    with pytest.raises(ProfileError, match=f"invalid robot profile .*{reason}"):
        load_profile(str(path))


@pytest.mark.parametrize(
    ("table", "reason"),
    [
        ('[states]\nmoving = "totaling"\n', "states.moving must be a list of state codes"),
        ("[states]\nmoving = [105]\n", "states.moving must be a list of state codes"),
        ('[states]\nspot = "pointing"\n', "states.spot must be a list of state codes"),
        ('[states]\nspot = ["pointing", true]\n', "states.spot must be a list of state codes"),
    ],
)
def test_state_codes_must_be_a_list_of_text(tmp_path, table, reason):
    path = tmp_path / "robot.toml"
    path.write_text(f'extends = "lds-robot"\nname = "custom"\n{table}')
    with pytest.raises(ProfileError, match=reason):
        load_profile(str(path))


@pytest.mark.parametrize(
    ("table", "reason"),
    [
        ('[data_points]\nraw = "128"\n', "data_points.raw must be a whole number, not '128'"),
        ("[data_points]\nroom_mode = true\n", "data_points.room_mode must be a whole number"),
        ("[cells]\nwall = 0.5\n", "cells.wall must be a whole number"),
        ('[geometry]\nspot_size_mm = "1000"\n', "geometry.spot_size_mm must be a whole number"),
        ("data_points = [123]\n", "data_points must be a table"),
        ("name = 5\n", "name must be text, not 5"),
        ("description = []\n", "description must be text"),
    ],
)
def test_a_value_of_another_type_is_a_profile_error(tmp_path, table, reason):
    path = tmp_path / "robot.toml"
    path.write_text(f'extends = "lds-robot"\n{table}')
    with pytest.raises(ProfileError, match=reason):
        load_profile(str(path))


MINIMAL_PROFILE = """
name = "custom"

[data_points]
state = 1
path = 2
raw = 3

[states]
moving = []

[cells]
wall = 1
unknown = 2
room_min = 3
room_max = 4
floor = 5

[geometry]
cleaning_radius_mm = 1
spot_size_mm = 2
coverage_cell_mm = 3
"""


def test_a_data_point_left_out_stays_out(tmp_path):
    path = tmp_path / "robot.toml"
    path.write_text(MINIMAL_PROFILE)
    profile = load_profile(str(path))
    assert profile.data_points.room_mode is None
    assert profile.data_points.total_count is None
    assert (profile.units.time, profile.units.area) == ("s", "m²")


def test_a_profile_can_change_the_units(tmp_path):
    path = tmp_path / "robot.toml"
    path.write_text('extends = "lds-robot"\nname = "custom"\n[units]\ntime = "min"\n')
    assert (load_profile(str(path)).units.time, load_profile(str(path)).units.area) == ("min", "m²")


@pytest.mark.parametrize(
    ("table", "reason"),
    [
        ('units = "s"\n', "units must be a table"),
        ("[units]\ntime = 60\n", "units.time must be text"),
        ('[units]\nspeed = "m/s"\n', "speed"),
    ],
)
def test_units_of_another_kind_are_a_profile_error(tmp_path, table, reason):
    path = tmp_path / "robot.toml"
    path.write_text(f'extends = "lds-robot"\nname = "custom"\n{table}')
    with pytest.raises(ProfileError, match=reason):
        load_profile(str(path))


def test_states_of_another_type_is_a_profile_error(tmp_path):
    path = tmp_path / "robot.toml"
    path.write_text('name = "custom"\nstates = "idle"\n')
    with pytest.raises(ProfileError, match="states must be a table"):
        load_profile(str(path))


@pytest.mark.parametrize(
    ("table", "reason"),
    [
        ("[levels.fan]\nquiet = 1\n", "fan level names of quiet are not text"),
        ("[levels.water]\n2 = 2\n", "water level names of 2 are not text"),
        ("[levels.water.3]\nwet = true\n", "water level names of 3 are not text"),
        ("[levels.fan]\nquiet = 1\nmax = 2\n", "fan level names of max, quiet are not text"),
    ],
)
def test_a_level_name_must_be_text(tmp_path, table, reason):
    path = tmp_path / "robot.toml"
    path.write_text(f'extends = "lds-robot"\nname = "custom"\n{table}')
    with pytest.raises(ProfileError, match=reason):
        load_profile(str(path))


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b'name = "broken', "not valid TOML"),
        (b"extends = 5", "which is not a profile name"),
        (b"name = '\xff'", "cannot be read"),
    ],
)
def test_an_unreadable_profile_file_is_a_profile_error(tmp_path, content, reason):
    path = tmp_path / "robot.toml"
    path.write_bytes(content)
    with pytest.raises(ProfileError, match=reason):
        load_profile(str(path))


@pytest.mark.parametrize("key", ["high", "1.5", "", "+1", "-1", "\u0663", " 2"])
def test_a_water_level_key_must_be_a_whole_number(tmp_path, key):
    path = tmp_path / "robot.toml"
    path.write_text(f'extends = "lds-robot"\nname = "custom"\n[levels.water]\n"{key}" = "high"\n')
    with pytest.raises(ProfileError, match=re.escape(f"levels.water keys must be whole numbers, not {key!r}")):
        load_profile(str(path))


def whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def texts(values: object) -> bool:
    return all(isinstance(value, str) for value in values)


def well_typed(profile: Profile) -> bool:
    numbers = [*vars(profile.data_points).values(), *vars(profile.cells).values(), *vars(profile.geometry).values()]
    return (
        isinstance(profile.name, str)
        and isinstance(profile.description, str)
        and all(whole(number) or number is None for number in vars(profile.data_points).values())
        and all(whole(number) for number in numbers if number is not None)
        and texts(profile.states.moving)
        and texts(profile.states.spot)
        and texts(profile.states.names)
        and texts(profile.states.names.values())
        and texts(profile.levels.fan.values())
        and all(whole(key) for key in profile.levels.water)
        and texts(profile.levels.water.values())
        and texts(vars(profile.units).values())
    )


@given(profile_overrides)
@example({"levels": {"water": {"high": "high"}}})
@example({"data_points": {"raw": "128"}})
@example({"states": {"names": "emptying"}})
def test_any_profile_is_read_or_refused(override):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "robot.toml"
        path.write_text(toml_document({"extends": "lds-robot", **override}), encoding="utf-8")
        try:
            profile = load_profile(str(path))
        except ProfileError:
            return
    assert well_typed(profile)


@pytest.mark.parametrize(("keys", "message"), [(("1", "01"), "level 1 twice, as '1' and '01'"), (("02",), "level 2")])
def test_a_water_level_named_twice_is_a_profile_error(tmp_path, keys, message):
    lines = "".join(f'"{key}" = "wet"\n' for key in keys)
    path = tmp_path / "robot.toml"
    path.write_text(f'extends = "lds-robot"\nname = "custom"\n[levels.water]\n{lines}')
    with pytest.raises(ProfileError, match=message):
        load_profile(str(path))


def test_a_water_level_key_with_a_leading_zero_alone_is_read(tmp_path):
    path = tmp_path / "robot.toml"
    path.write_text(MINIMAL_PROFILE + '[levels.water]\n"07" = "high"\n')
    assert load_profile(str(path)).levels.water == {7: "high"}
