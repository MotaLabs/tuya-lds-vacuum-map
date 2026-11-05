from hypothesis import given
from hypothesis import strategies as st

from support.strategies import json_values
from tuya_lds_vacuum_map.integrations.localtuya import DP_EVENT, STATUS_EVENT, LocalTuyaLink, data_points


class FakeHomeAssistant:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def call_service(self, domain: str, service: str, data: dict) -> None:
        self.calls.append((domain, service, data))


def test_dp_event_names_its_device():
    assert data_points(DP_EVENT, {"device_id": "dev", "dp": "105", "value": "totaling"}) == ("dev", {105: "totaling"})


def test_status_event_keeps_numeric_data_points():
    status = {"device_id": "dev", "new_status": {"105": "idle", 144: True, "online": True}}
    assert data_points(STATUS_EVENT, status) == ("dev", {105: "idle", 144: True})


def test_other_events_carry_no_data_points():
    assert data_points("state_changed", {"device_id": "dev"}) == ("dev", {})
    assert data_points(DP_EVENT, {"dp": 105, "value": "idle"}) == (None, {105: "idle"})


async def test_send_raw_sets_the_raw_data_point_as_a_number():
    ha = FakeHomeAssistant()
    await LocalTuyaLink(ha, "dev", 127).send_raw("abc")
    assert ha.calls == [("localtuya", "set_dp", {"device_id": "dev", "dp": 127, "value": "abc"})]


def test_data_points_that_are_not_plain_numbers_are_dropped():
    assert data_points(DP_EVENT, {"device_id": "dev", "dp": "\u00b2", "value": 1}) == ("dev", {})
    assert data_points(DP_EVENT, {"device_id": "dev", "dp": [105], "value": 1}) == ("dev", {})
    assert data_points(STATUS_EVENT, {"device_id": ["dev"], "new_status": ["105"]}) == (None, {})


@given(st.sampled_from((DP_EVENT, STATUS_EVENT, "other")), json_values)
def test_no_event_data_breaks_the_reader(event_type, data):
    device_id, points = data_points(event_type, data)
    assert device_id is None or isinstance(device_id, str)
    assert all(isinstance(dp, int) for dp in points)
