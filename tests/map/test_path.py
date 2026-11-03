from tuya_lds_vacuum_map.map.path import PathTracker


def chunk(path_id: int, start: int, points: list[list[int]], total: int | None = None) -> dict:
    data = {"pathID": path_id, "startPos": start, "posArray": points}
    if total is not None:
        data["totalPoints"] = total
    return data


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
