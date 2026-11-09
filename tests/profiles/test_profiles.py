import pytest

from tuya_lds_vacuum_map.i18n import load_texts
from tuya_lds_vacuum_map.profiles import DEFAULT_PROFILE, ProfileError, builtin_profiles, load_profile


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
