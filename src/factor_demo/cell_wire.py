"""Only JSON data and explicitly registered contracts cross a cell boundary."""

import json
import math

from pydantic import BaseModel

MAX_FRAME = 2 * 1024 * 1024


def encode(value):
    if isinstance(value, BaseModel):
        return {"@contract": type(value).__name__, "value": value.model_dump(mode="json")}
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("Only JSON data and declared contracts may cross this boundary")


def decode(value, models):
    if isinstance(value, list):
        return [decode(v, models) for v in value]
    if isinstance(value, dict):
        if "@contract" in value:
            if set(value) != {"@contract", "value"} or value["@contract"] not in models:
                raise ValueError("Unknown wire contract")
            return models[value["@contract"]].model_validate(value["value"])
        return {k: decode(v, models) for k, v in value.items()}
    return value


def dumps(value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode() + b"\n"
    if len(data) > MAX_FRAME:
        raise ValueError("Cell message exceeds the transport limit; use smaller tool results")
    return data


def loads(data):
    if len(data) > MAX_FRAME:
        raise ValueError("Cell message exceeds the transport limit")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise TypeError("Expected a cell envelope")
    return value
