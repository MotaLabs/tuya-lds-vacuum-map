import base64
import json
from typing import Any

_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


def decode_name(code: str | None) -> str:
    if not code:
        return ""
    return "".join(chr(int(part, 36)) for part in code.split("+"))


def encode_name(text: str) -> str:
    return "+".join(_base36(ord(char)) for char in text)


def decode_raw(value: str) -> dict[str, Any]:
    return json.loads(base64.b64decode(value))


def encode_raw(payload: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()).decode()


def _base36(value: int) -> str:
    out = ""
    while True:
        value, rest = divmod(value, 36)
        out = _DIGITS[rest] + out
        if value == 0:
            return out
