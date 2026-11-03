import asyncio
import hashlib
import hmac
import json
import time
import urllib.request
import uuid

ENDPOINTS = {
    "us": "https://openapi.tuyaus.com",
    "eu": "https://openapi.tuyaeu.com",
    "cn": "https://openapi.tuyacn.com",
    "in": "https://openapi.tuyain.com",
    "sg": "https://openapi-sg.iotbing.com",
}
LAYOUT_MAP = 0


class TuyaCloudError(Exception):
    def __init__(self, code, message) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class TuyaCloud:
    def __init__(self, client_id: str, client_secret: str, region: str, timeout: float = 20) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.base = ENDPOINTS[region]
        self.timeout = timeout
        self._token: str | None = None
        self._token_expires = 0.0

    async def realtime_map(self, device_id: str) -> dict:
        return await asyncio.to_thread(self._realtime_map, device_id)

    def _realtime_map(self, device_id: str) -> dict:
        files = self._get(f"/v1.0/users/sweepers/file/{device_id}/realtime-map", self._access_token())
        layout = next((item for item in files if item.get("map_type") == LAYOUT_MAP), None)
        if layout is None:
            raise TuyaCloudError("no-layout", "the realtime map has no layout file")
        with urllib.request.urlopen(layout["map_url"], timeout=self.timeout) as response:
            return json.loads(response.read())

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires:
            return self._token
        result = self._get("/v1.0/token?grant_type=1")
        self._token = result["access_token"]
        self._token_expires = time.time() + int(result.get("expire_time", 3600)) - 60
        return self._token

    def _get(self, path: str, token: str = ""):
        now = str(int(time.time() * 1000))
        nonce = uuid.uuid4().hex
        to_sign = "GET\n" + hashlib.sha256(b"").hexdigest() + "\n\n" + path
        message = (self.client_id + token + now + nonce + to_sign).encode()
        sign = hmac.new(self.client_secret.encode(), message, hashlib.sha256).hexdigest().upper()
        headers = {"client_id": self.client_id, "t": now, "nonce": nonce, "sign": sign, "sign_method": "HMAC-SHA256"}
        if token:
            headers["access_token"] = token
        with urllib.request.urlopen(
            urllib.request.Request(self.base + path, headers=headers), timeout=self.timeout
        ) as response:
            body = json.loads(response.read())
        if not body.get("success"):
            if token and body.get("code") in (1010, 1011):
                self._token = None
            raise TuyaCloudError(body.get("code"), body.get("msg"))
        return body["result"]
