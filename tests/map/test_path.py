import pytest
from hypothesis import given
from hypothesis import strategies as st

from tuya_lds_vacuum_map.lds.messages import PathChunk
from tuya_lds_vacuum_map.map.path import PathTracker


def chunk(path_id: int, start: int, points: list[list[int]], total: int | None = None) -> PathChunk:
    return PathChunk(path_id, start, tuple((x, y) for x, y in points), total)


def test_first_chunk_starts_the_path():
    tracker = PathTracker()
    assert tracker.position is None
    assert tracker.update(chunk(1, 0, [[0, 0], [10, 0]])) == [(0, 0), (10, 0)]
    assert tracker.position == (10, 0)


def test_next_chunk_returns_the_joint_with_the_new_points():
    tracker = PathTracker()
    tracker.update(chunk(1, 0, [[0, 0], [10, 0]]))
    assert tracker.update(chunk(1, 2, [[20, 0]])) == [(10, 0), (20, 0)]
    assert tracker.points == [(0, 0), (10, 0), (20, 0)]


def test_resent_chunk_replaces_the_tail():
    tracker = PathTracker()
    tracker.update(chunk(1, 0, [[0, 0], [10, 0], [20, 0]]))
    tracker.update(chunk(1, 1, [[10, 5], [20, 5]]))
    assert tracker.points == [(0, 0), (10, 5), (20, 5)]


def test_new_path_id_starts_over_even_with_an_offset():
    tracker = PathTracker()
    tracker.update(chunk(1, 0, [[0, 0], [10, 0]]))
    assert tracker.update(chunk(2, 5, [[50, 50]])) == [(50, 50)]
    assert tracker.points == [(50, 50)]
    assert tracker.path_id == 2


def test_total_points_trims_the_path():
    tracker = PathTracker()
    tracker.update(chunk(1, 0, [[0, 0], [10, 0], [20, 0]]))
    tracker.update(chunk(1, 1, [[10, 0]], total=2))
    assert tracker.points == [(0, 0), (10, 0)]


coordinates = st.integers(-1_000_000, 1_000_000)


@given(st.lists(st.tuples(coordinates, coordinates), min_size=1, max_size=40), st.data())
def test_any_split_into_chunks_rebuilds_the_path(points, data):
    cuts = sorted(data.draw(st.sets(st.integers(1, len(points) - 1)))) if len(points) > 1 else []
    tracker = PathTracker()
    start = 0
    for end in [*cuts, len(points)]:
        tracker.update(chunk(7, start, [list(point) for point in points[start:end]], total=end))
        start = end
    assert tracker.points == points
    tracker.update(chunk(7, 0, [list(point) for point in points[: len(points) // 2]], total=len(points) // 2))
    assert tracker.points == points[: len(points) // 2]


def test_a_restored_path_goes_on_with_its_id_and_starts_over_with_another():
    tracker = PathTracker()
    tracker.restore(1, [[0, 0], [10, 0]])
    assert (tracker.path_id, tracker.points, tracker.position) == (1, [(0, 0), (10, 0)], (10, 0))
    assert tracker.update(chunk(1, 2, [[20, 0]])) == [(10, 0), (20, 0)]
    tracker.update(chunk(2, 0, [[5, 5]]))
    assert tracker.points == [(5, 5)]


def test_a_restored_path_that_is_not_a_list_is_empty():
    tracker = PathTracker()
    tracker.restore(4, {"x": 0})
    assert (tracker.path_id, tracker.points) == (4, [])


def test_a_restored_path_with_a_bad_point_is_refused_and_changes_nothing():
    tracker = PathTracker()
    tracker.update(chunk(1, 0, [[0, 0]]))
    with pytest.raises(ValueError, match="not a number"):
        tracker.restore(2, [[10, 0], [None, 1]])
    assert (tracker.path_id, tracker.points) == (1, [(0, 0)])


def test_a_chunk_past_the_end_starts_the_drawn_path_over_and_the_next_ones_line_up():
    tracker = PathTracker()
    tracker.restore(1, [[0, 0], [10, 0]])
    assert tracker.update(chunk(1, 5, [[100, 100], [110, 100]])) == [(100, 100), (110, 100)]
    assert tracker.points == [(100, 100), (110, 100)]
    assert tracker.update(chunk(1, 7, [[120, 100]])) == [(110, 100), (120, 100)]
    tracker.update(chunk(1, 6, [[110, 105], [120, 105]], total=8))
    assert tracker.points == [(100, 100), (110, 105), (120, 105)]
    tracker.update(chunk(1, 6, [[110, 106], [120, 106]], total=7))
    assert tracker.points == [(100, 100), (110, 106)]


def test_a_gap_of_one_point_starts_over_too():
    tracker = PathTracker()
    tracker.update(chunk(1, 0, [[0, 0]]))
    assert tracker.update(chunk(1, 2, [[20, 0]])) == [(20, 0)]
    assert tracker.points == [(20, 0)]


def test_the_whole_path_sent_again_after_a_gap_replaces_it():
    tracker = PathTracker()
    tracker.restore(1, [[0, 0]])
    tracker.update(chunk(1, 4, [[50, 50]]))
    assert tracker.update(chunk(1, 0, [[1, 1], [2, 2]])) == [(1, 1), (2, 2)]
    assert tracker.points == [(1, 1), (2, 2)]
    assert tracker.update(chunk(1, 2, [[3, 3]])) == [(2, 2), (3, 3)]


def test_a_total_that_ends_before_the_kept_points_drops_them_all():
    tracker = PathTracker()
    tracker.update(chunk(1, 5, [[50, 50]]))
    tracker.update(chunk(1, 5, [[51, 51]], total=5))
    assert tracker.points == []
