import pytest

from support.maps import rooms_message
from tuya_lds_vacuum_map.lds import commands
from tuya_lds_vacuum_map.lds.codec import decode_raw, encode_name
from tuya_lds_vacuum_map.lds.messages import (
    MAX_COORDINATE_MM,
    PathChunk,
    as_label,
    parse_room_list,
    path_chunk,
    point_at,
    spot_in_commands,
)


def test_parse_room_list_reads_rooms_and_areas():
    message = rooms_message()
    parsed = parse_room_list(message)
    assert parsed.map_id == 42
    assert [(room.id, room.name, room.fan, room.water) for room in parsed.rooms] == [
        (1, "Left", "quiet", None),
        (2, "Right", None, 3),
    ]
    assert [(area.id, area.name, area.cleanable, area.forbidden, area.is_wall) for area in parsed.areas] == [
        (101, "Cats", True, False, False),
        (4, "", False, True, True),
    ]
    assert parsed.raw_areas == message["data"]["value"]
    assert parsed.names == ["Left", "Right"]
    assert parsed.room_named("Right").id == 2
    assert parsed.room_named("Kitchen") is None


def test_rooms_without_position_keep_none():
    parsed = parse_room_list({"infoType": 21004, "data": {"autoAreaValue": [{"id": 3, "name": "2b+2p+30+2p"}]}})
    assert parsed.rooms[0].pos is None
    assert parsed.map_id is None


def test_parse_room_list_ignores_other_messages():
    assert parse_room_list({"infoType": 30000, "data": {}}) is None


def test_spot_in_commands_finds_the_point_area():
    message = decode_raw(commands.clean_spot(7, 0, 0, 1500, now_ms=1))
    assert spot_in_commands(message) == ((-750, -750), (750, -750), (750, 750), (-750, 750))
    assert spot_in_commands(decode_raw(commands.clean_rooms(7, [1], now_ms=1))) is None


def test_path_chunk_only_for_path_messages():
    data = {"pathID": 1, "startPos": 2, "posArray": [[0, 0], [10, 5]], "totalPoints": 4}
    assert path_chunk({"infoType": 21011, "data": data}) == PathChunk(1, 2, ((0, 0), (10, 5)), 4)
    clamped = {"pathID": 1, "startPos": -3, "totalPoints": -1}
    assert path_chunk({"infoType": 21011, "data": clamped}) == PathChunk(1, 0, ())
    assert path_chunk({"infoType": 21011, "data": [1]}) is None
    assert path_chunk({"infoType": 21004, "data": {}}) is None


def test_an_empty_name_finds_no_room_even_one_without_a_name():
    parsed = parse_room_list({"infoType": 21004, "data": {"autoAreaValue": [{"id": 3, "name": ""}]}})
    assert parsed.room_named("") is None


def test_a_room_keeps_its_clean_type():
    room = {"id": 3, "name": "2b+2p+30+2p", "cleanType": "sweepOnly"}
    parsed = parse_room_list({"infoType": 21004, "data": {"autoAreaValue": [room]}})
    assert parsed.rooms[0].clean_type == "sweepOnly"


def test_an_area_without_id_mode_or_state_gets_empty_ones():
    area = {"vertexs": [[0, 0], [100, 0], [100, 100]]}
    [parsed] = parse_room_list({"infoType": 21004, "data": {"value": [area]}}).areas
    assert (parsed.id, parsed.mode, parsed.active) == (0, "", "")


def test_the_spot_is_found_after_other_commands():
    message = decode_raw(commands.clean_spot(7, 0, 0, 1500, now_ms=1))
    message["data"]["cmds"].insert(0, {"infoType": 1, "data": {}})
    assert spot_in_commands(message) == ((-750, -750), (750, -750), (750, 750), (-750, 750))


def test_a_chunk_without_a_start_starts_at_0_and_a_total_of_0_is_kept():
    data = {"pathID": 1, "posArray": [[0, 0]], "totalPoints": 0}
    assert path_chunk({"infoType": 21011, "data": data}) == PathChunk(1, 0, ((0, 0),), 0)


def test_fields_of_the_wrong_kind_are_dropped():
    message = rooms_message()
    message["data"]["autoAreaValue"][0].update(fanLevel=["quiet"], waterPump=None, cleanType=3)
    message["data"]["autoAreaValue"].append("not a room")
    message["data"]["value"].append(7)
    message["data"]["value"][0]["isWall"] = "false"
    parsed = parse_room_list(message)
    assert (parsed.rooms[0].fan, parsed.rooms[0].water, parsed.rooms[0].clean_type) == (None, None, None)
    assert len(parsed.rooms) == 2
    assert len(parsed.raw_areas) == 2
    assert not parsed.areas[0].is_wall


@pytest.mark.parametrize(
    "room",
    [
        {"id": None},
        {"id": [1]},
        {"id": True},
        {"id": 1, "pos": [1]},
        {"id": 1, "pos": [float("inf"), 0]},
        {"id": 1, "pos": [MAX_COORDINATE_MM + 1, 0]},
        {"id": 1, "name": 5},
    ],
)
def test_a_malformed_room_is_a_value_error(room):
    with pytest.raises(ValueError, match=r"not |out of range"):
        parse_room_list({"infoType": 21004, "data": {"autoAreaValue": [room]}})


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Sala\x1e", "Sala"),
        ("Su\ud800íte", "Suíte"),
        ("A\u2028B\u2029C\nD\tE", "ABCDE"),
        ("👨\u200d👩 \ue000", "👨\u200d👩 \ue000"),
    ],
)
def test_labels_keep_only_what_fits_on_one_line(text, label):
    assert as_label(text) == label


def test_room_and_area_names_are_labels():
    message = rooms_message()
    message["data"]["autoAreaValue"][0]["name"] = encode_name("Left\x1e")
    message["data"]["value"][1]["name"] = "Wall\u2029"
    parsed = parse_room_list(message)
    assert parsed.rooms[0].name == "Left"
    assert parsed.areas[1].name == "Wall"


@pytest.mark.parametrize(
    ("x", "y"), [(None, 0), (0, "1.5"), (float("nan"), 0), (0, 10**400), (MAX_COORDINATE_MM + 1, 0), (True, 0)]
)
def test_a_point_needs_two_numbers_in_range(x, y):
    with pytest.raises(ValueError, match=r"not a|out of range|cannot convert|invalid literal"):
        point_at(x, y)


def test_a_point_truncates_to_whole_millimetres():
    assert point_at(12.7, "-3") == (12, -3)
    assert point_at(-MAX_COORDINATE_MM, MAX_COORDINATE_MM) == (-MAX_COORDINATE_MM, MAX_COORDINATE_MM)
