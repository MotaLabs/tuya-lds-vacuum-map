import hashlib
import hmac
import io
import json
import urllib.request

import pytest
from hypothesis import HealthCheck, given, settings

from support.strategies import json_values
from tuya_lds_vacuum_map.integrations import tuya_cloud
from tuya_lds_vacuum_map.integrations.tuya_cloud import Reported, TuyaCloud, TuyaCloudError

BASE = tuya_cloud.ENDPOINTS["us"]
TOKEN_PATH = "/v1.0/token?grant_type=1"
FILES_PATH = "/v1.0/users/sweepers/file/dev/realtime-map"
LAYOUT_URL = "https://files.example/layout"
LAYOUT = {"infoType": 20002, "data": {"width": 1}}
FILES = [{"map_type": 1, "map_url": "https://files.example/path"}, {"map_type": 0, "map_url": LAYOUT_URL}]
SHADOW_PATH = "/v2.0/cloud/thing/dev/shadow/properties"


class FakeHttp:
    def __init__(self) -> None:
        self.requests: list = []
        self.answers = {
            BASE + TOKEN_PATH: [{"success": True, "result": {"access_token": "tok", "expire_time": 7200}}],
            BASE + FILES_PATH: [{"success": True, "result": FILES}],
            LAYOUT_URL: [LAYOUT],
        }

    def urlopen(self, request, timeout: float):
        url = request.full_url if isinstance(request, urllib.request.Request) else request
        self.requests.append(request)
        answers = self.answers[url]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        return io.BytesIO(json.dumps(answer).encode())

    def urls(self) -> list[str]:
        return [
            request.full_url if isinstance(request, urllib.request.Request) else request for request in self.requests
        ]


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def time(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(tuya_cloud, "time", fake)
    return fake


@pytest.fixture
def http(monkeypatch) -> FakeHttp:
    fake = FakeHttp()
    monkeypatch.setattr(tuya_cloud.urllib.request, "urlopen", fake.urlopen)
    return fake


async def test_the_realtime_map_is_the_layout_file(http):
    assert await TuyaCloud("id", "secret", "us").realtime_map("dev") == LAYOUT
    assert http.urls() == [BASE + TOKEN_PATH, BASE + FILES_PATH, LAYOUT_URL]


async def test_the_token_is_reused(http):
    cloud = TuyaCloud("id", "secret", "us")
    await cloud.realtime_map("dev")
    await cloud.realtime_map("dev")
    assert http.urls().count(BASE + TOKEN_PATH) == 1


@pytest.mark.parametrize(
    ("answer", "lifetime"),
    [({"access_token": "tok", "expire_time": 7200}, 7200), ({"access_token": "tok"}, 3600)],
)
async def test_the_token_is_renewed_a_minute_before_it_expires(http, clock, answer, lifetime):
    http.answers[BASE + TOKEN_PATH] = [{"success": True, "result": answer}]
    cloud = TuyaCloud("id", "secret", "us")
    await cloud.realtime_map("dev")
    clock.now += lifetime - 61
    await cloud.realtime_map("dev")
    assert http.urls().count(BASE + TOKEN_PATH) == 1
    clock.now += 1
    await cloud.realtime_map("dev")
    assert http.urls().count(BASE + TOKEN_PATH) == 2


async def test_requests_are_signed(http, clock):
    await TuyaCloud("id", "secret", "us").realtime_map("dev")
    request = http.requests[1]
    t, nonce = request.get_header("T"), request.get_header("Nonce")
    assert t == "1000000000"
    signed = "id" + "tok" + t + nonce + "GET\n" + hashlib.sha256(b"").hexdigest() + "\n\n" + FILES_PATH
    expected = hmac.new(b"secret", signed.encode(), hashlib.sha256).hexdigest().upper()
    assert request.get_header("Sign") == expected
    assert (request.get_header("Client_id"), request.get_header("Access_token")) == ("id", "tok")
    assert request.get_header("Sign_method") == "HMAC-SHA256"


@pytest.mark.parametrize("code", [1010, 1011])
async def test_a_rejected_token_is_fetched_again(http, code):
    http.answers[BASE + FILES_PATH] = [
        {"success": False, "code": code, "msg": "token invalid"},
        {"success": True, "result": FILES},
    ]
    cloud = TuyaCloud("id", "secret", "us")
    with pytest.raises(TuyaCloudError, match="token invalid") as error:
        await cloud.realtime_map("dev")
    assert error.value.code == code
    assert await cloud.realtime_map("dev") == LAYOUT
    assert http.urls().count(BASE + TOKEN_PATH) == 2


async def test_a_map_without_layout_is_an_error(http):
    http.answers[BASE + FILES_PATH] = [{"success": True, "result": FILES[:1]}]
    with pytest.raises(TuyaCloudError, match="no layout"):
        await TuyaCloud("id", "secret", "us").realtime_map("dev")


@pytest.mark.parametrize(
    ("path", "answer", "reason"),
    [
        (TOKEN_PATH, {"success": True, "result": []}, "no access token"),
        (TOKEN_PATH, {"success": True, "result": {"access_token": 7}}, "no access token"),
        (TOKEN_PATH, [], "None: None"),
        (FILES_PATH, {"success": True}, "no layout"),
        (FILES_PATH, {"success": True, "result": {"map_type": 0}}, "no layout"),
        (FILES_PATH, {"success": True, "result": ["file", {"map_type": 0}]}, "no https address"),
        (FILES_PATH, {"success": True, "result": [{"map_type": 0, "map_url": "file:///etc/passwd"}]}, "no https"),
    ],
)
async def test_an_answer_of_the_wrong_shape_is_a_cloud_error(http, path, answer, reason):
    http.answers[BASE + path] = [answer]
    with pytest.raises(TuyaCloudError, match=reason):
        await TuyaCloud("id", "secret", "us").realtime_map("dev")
    assert "file:///etc/passwd" not in http.urls()


async def test_the_data_points_are_the_device_shadow_with_the_time_of_each(http):
    properties = [
        {"dp_id": 105, "code": "robot_state", "value": "fullcharge", "time": 1700000000123},
        {"dp_id": "118", "code": "total_clean_count", "value": 963, "time": "1700000000456"},
        {"dp_id": 144, "value": True, "time": True},
        {"dp_id": 107, "value": 1440},
        {"dp_id": 7.0, "value": 1},
        {"dp_id": True, "value": 1},
        {"dp_id": "\u00b2", "value": 1},
        {"code": "no_dp", "value": 1},
        "not an object",
    ]
    http.answers[BASE + SHADOW_PATH] = [{"success": True, "result": {"properties": properties}}]
    assert await TuyaCloud("id", "secret", "us").data_points("dev") == {
        105: Reported("fullcharge", 1700000000123),
        118: Reported(963, 0),
        144: Reported(True, 0),
        107: Reported(1440, 0),
    }
    assert http.urls() == [BASE + TOKEN_PATH, BASE + SHADOW_PATH]


@pytest.mark.parametrize("result", [None, [], {}, {"properties": {"dp_id": 105}}])
async def test_a_shadow_without_properties_has_no_data_points(http, result):
    http.answers[BASE + SHADOW_PATH] = [{"success": True, "result": result}]
    assert await TuyaCloud("id", "secret", "us").data_points("dev") == {}


async def test_a_refused_shadow_is_a_cloud_error(http):
    http.answers[BASE + SHADOW_PATH] = [{"success": False, "code": 2008, "msg": "command or value not support"}]
    with pytest.raises(TuyaCloudError, match="not support"):
        await TuyaCloud("id", "secret", "us").data_points("dev")


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(json_values)
def test_no_shadow_breaks_the_reader(http, result):
    http.answers[BASE + SHADOW_PATH] = [{"success": True, "result": result}]
    points = TuyaCloud("id", "secret", "us")._data_points("dev")
    assert all(isinstance(dp, int) and isinstance(point.at_ms, int) for dp, point in points.items())
