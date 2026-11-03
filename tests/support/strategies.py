from hypothesis import strategies as st

from tuya_lds_vacuum_map.lds.codec import encode_name
from tuya_lds_vacuum_map.lds.messages import CLEAN_TARGET, COMMANDS, MAX_COORDINATE_MM, PATH, ROOM_LIST

INFO_TYPES = (20002, 21003, 21004, 21005, 21011, 21023, 30000)
KEYS = (
    "active",
    "autoAreaValue",
    "chargeHandlePos",
    "cleanId",
    "cmds",
    "data",
    "extraAreas",
    "fanLevel",
    "height",
    "id",
    "infoType",
    "isWall",
    "map",
    "mapId",
    "mode",
    "name",
    "pathID",
    "pos",
    "posArray",
    "resolution",
    "segmentId",
    "startPos",
    "tag",
    "totalPoints",
    "value",
    "vertexs",
    "waterPump",
    "width",
    "x_min",
    "y_min",
)
WORDS = ("point", "area", "forbid", "normal", "depth", "2b+2p+30+2p", "zz+zz", "+", "-1")

json_scalars = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats()
    | st.text(max_size=12)
    | st.sampled_from(INFO_TYPES)
    | st.sampled_from(WORDS)
)
json_values = st.recursive(
    json_scalars,
    lambda children: (
        st.lists(children, max_size=5)
        | st.dictionaries(st.sampled_from(KEYS) | st.text(max_size=4), children, max_size=6)
    ),
    max_leaves=30,
)
messages = json_values | st.fixed_dictionaries(
    {"infoType": st.sampled_from(INFO_TYPES)}, optional={"data": json_values}
)

coordinates = st.integers(-MAX_COORDINATE_MM, MAX_COORDINATE_MM)
points = st.lists(coordinates, min_size=2, max_size=2)
labels = st.text(st.characters(exclude_categories=()), max_size=8)
names = labels.map(encode_name)
rooms = st.fixed_dictionaries(
    {"id": st.integers(), "name": names},
    optional={
        "pos": points,
        "fanLevel": st.text(max_size=8),
        "waterPump": st.integers(),
        "cleanType": st.text(max_size=8),
    },
)
areas = st.fixed_dictionaries(
    {"id": st.integers(), "vertexs": st.lists(points, max_size=6)},
    optional={
        "mode": st.sampled_from(WORDS) | st.text(max_size=4),
        "active": st.sampled_from(WORDS),
        "tag": names,
        "name": labels,
        "isWall": st.booleans(),
    },
)
room_lists = st.fixed_dictionaries(
    {
        "infoType": st.just(ROOM_LIST),
        "data": st.fixed_dictionaries(
            {"autoAreaValue": st.lists(rooms, max_size=4), "value": st.lists(areas, max_size=4)},
            optional={"mapId": st.integers()},
        ),
    }
)
path_chunks = st.fixed_dictionaries(
    {
        "infoType": st.just(PATH),
        "data": st.fixed_dictionaries(
            {"posArray": st.lists(points, max_size=20)},
            optional={"pathID": json_scalars, "startPos": st.integers(), "totalPoints": st.integers()},
        ),
    }
)
spot_commands = st.lists(points, max_size=4).map(
    lambda vertexs: {
        "infoType": COMMANDS,
        "data": {"cmds": [{"infoType": CLEAN_TARGET, "data": {"extraAreas": [{"mode": "point", "vertexs": vertexs}]}}]},
    }
)
