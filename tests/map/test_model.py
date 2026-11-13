from support.maps import CODES, build_cells, cloud_payload
from tuya_lds_vacuum_map.lds.messages import Room
from tuya_lds_vacuum_map.map.coverage import Coverage
from tuya_lds_vacuum_map.map.model import MapModel


def model(payload: dict | None = None) -> MapModel:
    return MapModel(Coverage(CODES, 50, 150), payload)


def test_without_any_map_there_is_nothing_to_draw():
    empty = model()
    assert (empty.cloud_payload, empty.view) == (None, None)
    assert empty.compose(()) is None
    assert (empty.dock, empty.map_id) == (None, None)


def test_the_same_cloud_map_keeps_the_coverage(payload):
    current = model(payload)
    current.coverage.paint([(25, 25)])
    assert current.set_cloud(cloud_payload()) is False
    assert current.coverage.cells


def test_a_new_cloud_map_resets_the_coverage(payload):
    current = model(payload)
    current.coverage.paint([(25, 25)])
    cells = build_cells()
    cells[0] = 1
    assert current.set_cloud(cloud_payload(cells, map_id=43)) is True
    assert current.coverage.cells == set()
    assert current.map_id == 43


def test_compose_keeps_the_view(payload):
    current = model(payload)
    view = current.compose((Room(1, "Left"),))
    assert current.view is view
    assert view.map_id == 42
