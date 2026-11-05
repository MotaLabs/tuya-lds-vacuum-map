import pytest

from support.maps import cell_center_mm, rooms_message
from tuya_lds_vacuum_map.lds.messages import Area, parse_room_list
from tuya_lds_vacuum_map.map.grid import GridMap
from tuya_lds_vacuum_map.map.hit import area_at, point_in_polygon, room_at

SQUARE = ((0, 0), (100, 0), (100, 100), (0, 100))
TRIANGLE = ((0, 0), (100, 0), (0, 100))


def test_point_in_polygon():
    assert point_in_polygon(50, 50, SQUARE)
    assert not point_in_polygon(150, 50, SQUARE)
    assert not point_in_polygon(50, -1, SQUARE)


@pytest.mark.parametrize(("x", "y", "inside"), [(40, 40, True), (49, 50, True), (51, 50, False), (60, 60, False)])
def test_a_slanted_edge_splits_inside_from_outside(x, y, inside):
    assert point_in_polygon(x, y, TRIANGLE) is inside


def test_a_point_level_with_a_flat_edge_is_answered():
    assert isinstance(point_in_polygon(10, 0, TRIANGLE), bool)


def test_a_triangle_area_can_be_tapped():
    corner = Area(6, "area", "normal", "Corner", TRIANGLE)
    assert area_at((corner,), 20, 20) is corner


def test_area_at_finds_the_cleanable_area():
    areas = parse_room_list(rooms_message()).areas
    assert area_at(areas, *cell_center_mm(26, 7)).name == "Cats"
    assert area_at(areas, *cell_center_mm(26, 20)) is None


def test_forbidden_and_degenerate_areas_are_not_tapped():
    forbid = Area(4, "", "forbid", "", SQUARE)
    line = Area(5, "area", "normal", "Line", ((0, 0), (100, 100)))
    assert area_at((forbid, line), 50, 50) is None


def test_room_at_reads_the_grid(payload):
    grid = GridMap.from_cloud(payload)
    rooms = parse_room_list(rooms_message()).rooms
    assert room_at(grid, rooms, *cell_center_mm(10, 14)).name == "Left"
    assert room_at(grid, rooms, *cell_center_mm(30, 14)).name == "Right"
    assert room_at(grid, rooms, *cell_center_mm(20, 14)) is None
    assert room_at(None, rooms, 0, 0) is None
