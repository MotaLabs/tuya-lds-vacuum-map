import base64
import contextlib

import lz4.block
import pytest
from hypothesis import given
from hypothesis import strategies as st

from support.maps import CODES, HEIGHT, RESOLUTION, WIDTH, X_MIN, Y_MIN, build_cells, cell_center_mm, cloud_payload
from support.strategies import json_values
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


CLOUD_KEYS = ("width", "height", "resolution", "x_min", "y_min", "map", "mapId", "chargeHandlePos")


@given(st.dictionaries(st.sampled_from(CLOUD_KEYS), json_values))
def test_a_broken_cloud_payload_is_a_value_error(data):
    with contextlib.suppress(ValueError):
        GridMap.from_cloud({"data": {**cloud_payload()["data"], **data}})


@given(st.integers(1, 60), st.integers(1, 60), st.binary(max_size=4000))
def test_any_cells_decode_or_fail_with_a_value_error(width, height, raw):
    data = {**cloud_payload()["data"], "width": width, "height": height}
    data["map"] = base64.b64encode(raw).decode()
    with contextlib.suppress(ValueError):
        assert len(GridMap.from_cloud(data).cells) == width * height


def test_an_oversized_map_is_refused_before_decoding():
    with pytest.raises(ValueError, match="too large"):
        GridMap.from_cloud({**cloud_payload()["data"], "width": 1_000_000, "height": 1_000_000})


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"map": None}, "cells are missing"),
        ({"resolution": 0}, "not positive"),
        ({"resolution": "1e999"}, "not a finite number"),
        ({"x_min": 10**400}, "not a finite number"),
        ({"y_min": True}, "not a number"),
        ({"width": 0}, "empty or too large"),
    ],
)
def test_each_broken_field_is_named(change, reason):
    with pytest.raises(ValueError, match=reason):
        GridMap.from_cloud({**cloud_payload()["data"], **change})
