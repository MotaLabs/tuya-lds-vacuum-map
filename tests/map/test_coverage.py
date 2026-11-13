import pytest
from hypothesis import given
from hypothesis import strategies as st

from support.maps import CODES, PROFILE, build_cells, cell_center_mm, cloud_payload
from tuya_lds_vacuum_map.lds.messages import Room
from tuya_lds_vacuum_map.map import coverage as coverage_module
from tuya_lds_vacuum_map.map.coverage import Coverage
from tuya_lds_vacuum_map.map.grid import GridMap

LEFT = Room(1, "Left", pos=cell_center_mm(10, 14))
RIGHT = Room(2, "Right", pos=cell_center_mm(30, 14))


def coverage() -> Coverage:
    return Coverage(CODES, PROFILE.geometry.coverage_cell_mm, PROFILE.geometry.cleaning_radius_mm)


def test_a_point_paints_the_cleaning_disc():
    cov = coverage()
    assert cov.paint([(25, 25)]) is True
    assert len(cov.cells) == 29
    assert {(3, 0), (-3, 0), (0, 3), (0, -3), (2, 2)} <= cov.cells
    assert (3, 1) not in cov.cells


def test_painting_the_same_place_again_changes_nothing():
    cov = coverage()
    cov.paint([(25, 25)])
    assert cov.paint([(25, 25)]) is False
    assert cov.paint([]) is False


def test_a_segment_is_painted_end_to_end():
    cov = coverage()
    cov.paint([(25, 25), (1025, 25)])
    assert all((col, 0) in cov.cells for col in range(-3, 24))
    assert (24, 0) not in cov.cells


@pytest.mark.parametrize(("start", "end"), [((-1000, 25), (1000, 25)), ((25, -1000), (25, 1000))])
def test_a_straight_segment_paints_what_each_of_its_millimetres_paints(start, end):
    cov = coverage()
    cov.paint([start, end])
    every_millimetre = coverage()
    for step in range(2001):
        every_millimetre.paint(
            [(start[0] + (end[0] - start[0]) * step // 2000, start[1] + (end[1] - start[1]) * step // 2000)]
        )
    assert cov.cells == every_millimetre.cells


def test_a_point_below_and_left_of_the_origin_paints_around_its_own_cell():
    cov = coverage()
    cov.paint([(-25, -25)])
    disc = coverage()
    disc.paint([(25, 25)])
    assert cov.cells == {(col - 1, row - 1) for col, row in disc.cells}


def test_cells_round_trip_through_a_flat_list():
    cov = coverage()
    cov.paint([(25, 25), (525, 25)])
    restored = coverage()
    restored.load_list(cov.to_list())
    assert restored.cells == cov.cells


def test_clear_forgets_the_cells():
    cov = coverage()
    cov.paint([(25, 25)])
    cov.clear()
    assert cov.cells == set()
    assert cov.apply(None, (LEFT,)) is None


def test_cleaned_cells_over_the_cloud_map_become_the_nearest_room():
    cells = build_cells()
    for row in (14, 15):
        for col in range(24, 28):
            cells[row * 40 + col] = CODES.unknown
    base = GridMap.from_cloud(cloud_payload(cells))
    cov = coverage()
    cov.paint([cell_center_mm(25, 14)])
    grid = cov.apply(base, (LEFT, RIGHT))
    assert all(grid.get((col, row)) == 2 for row in (14, 15) for col in range(24, 28))
    assert grid.get((10, 14)) == 1
    assert base.get((25, 14)) == CODES.unknown


def test_room_cells_keep_their_room():
    base = GridMap.from_cloud(cloud_payload())
    cov = coverage()
    cov.paint([cell_center_mm(18, 14)])
    grid = cov.apply(base, (RIGHT,))
    assert grid.get((18, 14)) == 1


def test_the_coverage_lands_on_the_cloud_cells_under_it():
    base = GridMap.from_cloud(cloud_payload(bytearray([CODES.unknown]) * (40 * 30)))
    cov = coverage()
    cov.paint([cell_center_mm(25, 14)])
    grid = cov.apply(base, ())
    disc = coverage()
    disc.paint([(25, 25)])
    painted = {(col, row) for row in range(30) for col in range(40) if grid.get((col, row)) != CODES.unknown}
    assert painted == {(25 + col, 14 + row) for col, row in disc.cells}
    assert {grid.get(cell) for cell in painted} == {CODES.floor}


def test_without_a_cloud_map_the_coverage_draws_its_own_map():
    cov = coverage()
    cov.paint([(25, 25)])
    grid = cov.apply(None, ())
    assert (grid.width, grid.height) == (11, 11)
    assert (grid.resolution, grid.x_min, grid.y_min) == (0.05, -0.25, -0.25)
    assert grid.value_at(25, 25) == CODES.floor
    assert grid.get((1, 5)) == CODES.wall
    assert grid.get((0, 0)) == CODES.unknown


def test_without_a_cloud_map_walls_ring_the_covered_floor_and_nothing_else():
    cov = coverage()
    cov.paint([(25, 25), (525, 275)])
    grid = cov.apply(None, ())
    col0, row0 = round(grid.x_min / grid.resolution), round(grid.y_min / grid.resolution)
    drawn = {(col + col0, row + row0): grid.get((col, row)) for row in range(grid.height) for col in range(grid.width)}
    sides = ((1, 0), (-1, 0), (0, 1), (0, -1))
    ring = {(col + dc, row + dr) for col, row in cov.cells for dc, dr in sides} - cov.cells
    assert {cell for cell, value in drawn.items() if value == CODES.floor} == cov.cells
    assert {cell for cell, value in drawn.items() if value == CODES.wall} == ring


def test_moved_rooms_recolor_the_coverage():
    cov = coverage()
    cov.paint([(25, 25)])
    assert cov.apply(None, (Room(1, "A", pos=(25, 25)),)).value_at(25, 25) == 1
    assert cov.apply(None, (Room(2, "B", pos=(25, 25)),)).value_at(25, 25) == 2


def test_a_room_that_is_not_a_cell_code_paints_plain_floor():
    cov = coverage()
    cov.paint([(25, 25)])
    for room_id in (CODES.room_max + 1, CODES.room_min - 1, CODES.floor):
        assert cov.apply(None, (Room(room_id, "Odd", pos=(25, 25)),)).value_at(25, 25) == CODES.floor


def test_a_covered_floor_too_large_to_draw_is_left_out_and_reported_once(monkeypatch, caplog):
    monkeypatch.setattr(coverage_module, "MAX_CELLS", 400)
    cov = coverage()
    cov.paint([(0, 0)])
    assert cov.apply(None, ()) is not None
    cov.paint([(5_000, 0)])
    assert cov.apply(None, ()) is None
    assert cov.apply(None, ()) is None
    assert caplog.text.count("too large to draw") == 1


def test_a_covered_floor_exactly_at_the_size_limit_is_still_drawn(monkeypatch):
    cov = coverage()
    cov.paint([(0, 0)])
    drawn = cov.apply(None, ())
    monkeypatch.setattr(coverage_module, "MAX_CELLS", drawn.width * drawn.height)
    assert cov.apply(None, ()) is not None
    monkeypatch.setattr(coverage_module, "MAX_CELLS", drawn.width * drawn.height - 1)
    assert cov.apply(None, ()) is None


millimetres = st.integers(-20_000, 20_000)


@given(st.lists(st.tuples(millimetres, millimetres), min_size=1, max_size=8))
def test_painting_covers_every_point_and_repeats_are_free(points):
    cov = coverage()
    cov.paint(points)
    assert all((x // 50, y // 50) in cov.cells for x, y in points)
    assert cov.paint(points) is False
