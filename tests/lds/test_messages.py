from support.maps import rooms_message
from tuya_lds_vacuum_map.lds import commands
from tuya_lds_vacuum_map.lds.codec import decode_raw
from tuya_lds_vacuum_map.lds.messages import parse_room_list, path_chunk, spot_in_commands


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
    assert path_chunk({"infoType": 21011, "data": {"pathID": 1}}) == {"pathID": 1}
    assert path_chunk({"infoType": 21004, "data": {}}) is None
