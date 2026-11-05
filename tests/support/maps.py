import base64

import lz4.block

from tuya_lds_vacuum_map.lds.codec import encode_name
from tuya_lds_vacuum_map.profiles import load_profile

PROFILE = load_profile()
CODES = PROFILE.cells
WIDTH, HEIGHT = 40, 30
RESOLUTION = 0.05
X_MIN, Y_MIN = -1.0, -0.5


def build_cells() -> bytearray:
    cells = bytearray([CODES.unknown]) * (WIDTH * HEIGHT)
    for row in range(HEIGHT):
        for col in range(WIDTH):
            border = (row in (2, HEIGHT - 3) and 2 <= col <= WIDTH - 3) or (
                col in (2, WIDTH - 3, 20) and 2 <= row <= HEIGHT - 3
            )
            if border:
                cells[row * WIDTH + col] = CODES.wall
            elif 2 < row < HEIGHT - 3 and 2 < col < 20:
                cells[row * WIDTH + col] = 1
            elif 2 < row < HEIGHT - 3 and 20 < col < WIDTH - 3:
                cells[row * WIDTH + col] = 2
    return cells


def cloud_payload(cells: bytearray | None = None, map_id: int = 42) -> dict:
    raw = lz4.block.compress(bytes(cells if cells is not None else build_cells()), store_size=False)
    return {
        "data": {
            "width": WIDTH,
            "height": HEIGHT,
            "resolution": RESOLUTION,
            "x_min": X_MIN,
            "y_min": Y_MIN,
            "map": base64.b64encode(raw).decode(),
            "mapId": map_id,
            "chargeHandlePos": [0, 0],
        },
        "infoType": 20002,
    }


def cell_center_mm(col: int, row: int) -> tuple[int, int]:
    return round((X_MIN + (col + 0.5) * RESOLUTION) * 1000), round((Y_MIN + (row + 0.5) * RESOLUTION) * 1000)


def rooms_message() -> dict:
    return {
        "infoType": 21004,
        "data": {
            "mapId": 42,
            "autoAreaValue": [
                {"id": 1, "name": encode_name("Left"), "pos": list(cell_center_mm(10, 14)), "fanLevel": "quiet"},
                {"id": 2, "name": encode_name("Right"), "pos": list(cell_center_mm(30, 14)), "waterPump": 3},
            ],
            "value": [
                {
                    "active": "normal",
                    "id": 101,
                    "mode": "area",
                    "tag": encode_name("Cats"),
                    "vertexs": [
                        list(cell_center_mm(22, 4)),
                        list(cell_center_mm(30, 4)),
                        list(cell_center_mm(30, 10)),
                        list(cell_center_mm(22, 10)),
                    ],
                },
                {
                    "active": "forbid",
                    "id": 4,
                    "isWall": True,
                    "tag": "",
                    "vertexs": [[0, 0], [10, 0], [10, 10], [0, 10]],
                },
            ],
        },
    }
