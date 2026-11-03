from support.maps import CODES, PROFILE, build_cells, cell_center_mm, cloud_payload
from tuya_lds_vacuum_map.lds.messages import Room
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


def test_without_a_cloud_map_the_coverage_draws_its_own_map():
    cov = coverage()
    cov.paint([(25, 25)])
    grid = cov.apply(None, ())
    assert (grid.width, grid.height) == (11, 11)
    assert grid.value_at(25, 25) == CODES.floor
    assert grid.get((1, 5)) == CODES.wall
    assert grid.get((0, 0)) == CODES.unknown


def test_moved_rooms_recolor_the_coverage():
    cov = coverage()
    cov.paint([(25, 25)])
    assert cov.apply(None, (Room(1, "A", pos=(25, 25)),)).value_at(25, 25) == 1
    assert cov.apply(None, (Room(2, "B", pos=(25, 25)),)).value_at(25, 25) == 2
