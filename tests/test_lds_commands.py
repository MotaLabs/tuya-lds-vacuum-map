from tuya_lds_vacuum_map.lds import commands
from tuya_lds_vacuum_map.lds.codec import decode_raw

START = {"data": {"mode": "reAppointClean"}, "infoType": 21005}


def test_clean_rooms_matches_the_room_command():
    assert decode_raw(commands.clean_rooms(1700000000, [8], now_ms=1)) == {
        "dInfo": {"ts": "1", "userId": "0"},
        "data": {
            "cmds": [
                {"data": {"cleanId": [-3], "extraAreas": [], "mapId": 1700000000, "segmentId": [8]}, "infoType": 21023},
                START,
            ],
            "mainCmds": [21005],
        },
        "infoType": 30000,
    }


def test_clean_spot_is_a_square_point_area_around_the_tap():
    cmds = decode_raw(commands.clean_spot(7, 1500, -4750, 1500, now_ms=1))["data"]["cmds"]
    area = cmds[0]["data"]["extraAreas"][0]
    assert cmds[0]["data"]["cleanId"] == [-3]
    assert (area["mode"], area["active"], area["id"]) == ("point", "depth", 100)
    assert area["vertexs"] == [[750, -5500], [2250, -5500], [2250, -4000], [750, -4000]]
    assert cmds[1] == START


def test_spot_size_comes_from_the_caller():
    assert commands.spot_square(0, 0, 1000) == [[-500, -500], [500, -500], [500, 500], [-500, 500]]


def test_clean_areas_resends_the_areas_unchanged():
    areas = [
        {
            "active": "normal",
            "id": 101,
            "mode": "area",
            "tag": "1z+2p+38+33+37",
            "vertexs": [[0, 0], [10, 0], [10, 10], [0, 10]],
        }
    ]
    cmds = decode_raw(commands.clean_areas(7, areas, now_ms=1))["data"]["cmds"]
    assert cmds[0] == {"data": {"mapId": 7, "value": areas}, "infoType": 21003}
    assert cmds[1]["data"]["cleanId"] == [-2]
    assert cmds[2] == START
