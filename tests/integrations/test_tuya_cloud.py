import hashlib
import hmac
import io
import json
import urllib.request

import pytest

from tuya_lds_vacuum_map.integrations import tuya_cloud
from tuya_lds_vacuum_map.integrations.tuya_cloud import TuyaCloud, TuyaCloudError

BASE = tuya_cloud.ENDPOINTS["us"]
TOKEN_PATH = "/v1.0/token?grant_type=1"
FILES_PATH = "/v1.0/users/sweepers/file/dev/realtime-map"
LAYOUT_URL = "https://files.example/layout"
LAYOUT = {"infoType": 20002, "data": {"width": 1}}
FILES = [{"map_type": 1, "map_url": "https://files.example/path"}, {"map_type": 0, "map_url": LAYOUT_URL}]


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


async def test_requests_are_signed(http):
    await TuyaCloud("id", "secret", "us").realtime_map("dev")
    request = http.requests[1]
    t, nonce = request.get_header("T"), request.get_header("Nonce")
    signed = "id" + "tok" + t + nonce + "GET\n" + hashlib.sha256(b"").hexdigest() + "\n\n" + FILES_PATH
    expected = hmac.new(b"secret", signed.encode(), hashlib.sha256).hexdigest().upper()
    assert request.get_header("Sign") == expected
    assert (request.get_header("Client_id"), request.get_header("Access_token")) == ("id", "tok")
    assert request.get_header("Sign_method") == "HMAC-SHA256"


async def test_a_rejected_token_is_fetched_again(http):
    http.answers[BASE + FILES_PATH] = [
        {"success": False, "code": 1010, "msg": "token invalid"},
        {"success": True, "result": FILES},
    ]
    cloud = TuyaCloud("id", "secret", "us")
    with pytest.raises(TuyaCloudError, match="token invalid") as error:
        await cloud.realtime_map("dev")
    assert error.value.code == 1010
    assert await cloud.realtime_map("dev") == LAYOUT
    assert http.urls().count(BASE + TOKEN_PATH) == 2


async def test_a_map_without_layout_is_an_error(http):
    http.answers[BASE + FILES_PATH] = [{"success": True, "result": FILES[:1]}]
    with pytest.raises(TuyaCloudError, match="no layout"):
        await TuyaCloud("id", "secret", "us").realtime_map("dev")
