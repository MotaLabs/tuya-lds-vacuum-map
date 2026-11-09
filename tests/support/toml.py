import json
import math
from typing import Any


def toml_string(text: str) -> str:
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return ("-inf" if value < 0 else "inf") if math.isinf(value) else "nan" if math.isnan(value) else repr(value)
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    return "{" + ", ".join(f"{toml_string(key)} = {toml_value(item)}" for key, item in value.items()) + "}"


def toml_document(values: dict[str, Any]) -> str:
    return "".join(f"{toml_string(key)} = {toml_value(value)}\n" for key, value in values.items())
