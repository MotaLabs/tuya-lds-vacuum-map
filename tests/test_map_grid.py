import base64

import lz4.block
import pytest

from conftest import CODES, HEIGHT, RESOLUTION, WIDTH, X_MIN, Y_MIN, build_cells, cell_center_mm, cloud_payload
from tuya_lds_vacuum_map.map.grid import GridMap


def test_from_cloud_reads_the_layout(payload):
    grid = GridMap.from_cloud(payload)
    assert (grid.width, grid.height, grid.resolution, grid.x_min, grid.y_min) == (
        WIDTH,
        HEIGHT,
        RESOLUTION,
        X_MIN,
        Y_MIN,
    )
    assert grid.map_id == 42
    assert grid.dock == (0, 0)
    assert grid.cell_mm == 50


def test_from_cloud_accepts_the_bare_data_object(payload):
    assert GridMap.from_cloud(payload["data"]).cells == GridMap.from_cloud(payload).cells


def test_cells_are_addressed_by_column_and_row(payload):
    grid = GridMap.from_cloud(payload)
    assert grid.value_at(*cell_center_mm(10, 14)) == 1
    assert grid.value_at(*cell_center_mm(30, 14)) == 2
    assert grid.value_at(*cell_center_mm(20, 14)) == CODES.wall
    assert grid.value_at(*cell_center_mm(0, 0)) == CODES.unknown
    assert grid.cell_of(*cell_center_mm(7, 23)) == (7, 23)


def test_cells_outside_the_grid_are_none(payload):
    grid = GridMap.from_cloud(payload)
    assert grid.get((WIDTH, 0)) is None
    assert grid.get((0, -1)) is None
    grid.set((-1, 0), 9)
    assert grid.cells == GridMap.from_cloud(payload).cells


def test_copy_does_not_share_cells(payload):
    grid = GridMap.from_cloud(payload)
    copy = grid.copy()
    copy.set((10, 14), 2)
    assert grid.get((10, 14)) == 1
    assert (copy.map_id, copy.dock) == (grid.map_id, grid.dock)


def test_missing_dock_and_map_id():
    data = cloud_payload()["data"]
    del data["chargeHandlePos"], data["mapId"]
    grid = GridMap.from_cloud(data)
    assert (grid.dock, grid.map_id) == (None, None)


def test_wrong_cell_count_is_rejected():
    payload = cloud_payload(build_cells()[:-WIDTH])
    with pytest.raises(ValueError, match="cells"):
        GridMap.from_cloud(payload)


def test_corrupt_cells_are_rejected():
    payload = cloud_payload()
    payload["data"]["map"] = base64.b64encode(lz4.block.compress(b"\x01" * 10, store_size=False)[:3]).decode()
    with pytest.raises(ValueError, match="decompressed"):
        GridMap.from_cloud(payload)
