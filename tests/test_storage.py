import json

import pytest

from tuya_lds_vacuum_map.storage import MapSource, Snapshot, Storage


def test_missing_file_gives_defaults(tmp_path):
    assert Storage(tmp_path / "state.json").load() == Snapshot()


def test_snapshot_round_trip(tmp_path):
    storage = Storage(tmp_path / "data" / "state.json")
    snapshot = Snapshot(
        room_message={"infoType": 21004},
        robot_state="totaling",
        room_mode=True,
        source=MapSource.LOCAL,
        selected_room="Suíte",
        coverage=[1, 2, 3, 4],
    )
    storage.save(snapshot)
    assert storage.load() == snapshot
    assert [path.name for path in (tmp_path / "data").iterdir()] == ["state.json"]


def test_unknown_keys_and_sources_are_ignored(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"robot_state": "idle", "source": "cloud", "future_field": 1}))
    snapshot = Storage(path).load()
    assert snapshot.robot_state == "idle"
    assert snapshot.source is MapSource.AUTO


def test_text_that_is_not_valid_unicode_is_saved(tmp_path):
    storage = Storage(tmp_path / "state.json")
    snapshot = Snapshot(cloud_map={"name": "Su\ud800íte"}, selected_room="\udfff")
    storage.save(snapshot)
    assert storage.load() == snapshot


def test_a_failed_save_leaves_no_temporary_file(tmp_path):
    (tmp_path / "state.json").mkdir()
    with pytest.raises(IsADirectoryError):
        Storage(tmp_path / "state.json").save(Snapshot())
    assert [path.name for path in tmp_path.iterdir()] == ["state.json"]
