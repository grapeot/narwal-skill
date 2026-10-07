"""Expected field5 shapes. Topic cannot correlate replies, so a mismatch closes the socket.

A later reply with the same shape as the pending query is still indistinguishable.
That residual is not treated as success for a different shape.
"""

from __future__ import annotations

import math
import re
from typing import Any

from narwal_skill.errors import DecodeError, QueryRejected
from narwal_skill.telemetry import REJECTED_RESULT_CODES, is_rejected_result, parse_identity

ACCEPTED_ACK_CODES = frozenset({1, 6})
_FIRMWARE_TEXT = re.compile(r"^v?\d+(?:\.\d+){2,}$", re.IGNORECASE)


def validate_query_response(short_topic: str, decoded: dict[str, Any]) -> None:
    """Raise if this payload cannot be the answer to short_topic. Does not invent a request id."""
    if not isinstance(decoded, dict):
        raise DecodeError(
            f"{short_topic} response was not a message",
            detail=f"decoded type {type(decoded).__name__}",
        )
    if short_topic == "common/active_robot_publish":
        _require_accepted_ack(decoded)
        return
    rejected = is_rejected_result(decoded)
    if rejected is not None:
        raise QueryRejected(
            f"{short_topic} was rejected and is not data",
            detail=f"result_code={rejected} topic={short_topic}",
        )
    if short_topic == "common/get_device_info":
        if parse_identity(decoded) is None:
            raise DecodeError(
                "get_device_info response was not identity bytes",
                detail="expected nonempty byte/text fields 1, 2, and 3",
            )
        return
    if short_topic == "status/get_device_base_status":
        if not _is_base_status(decoded):
            raise DecodeError(
                "base status response shape did not match",
                detail="expected a status message or a field-2 wrapper, not an unrelated field5 payload",
            )
        return
    if short_topic == "map/get_map":
        if not _is_map(decoded):
            raise DecodeError(
                "get_map response shape did not match",
                detail="expected field 2 to be a map message",
            )
        return
    if short_topic == "common/get_feature_list":
        if not _is_feature_list(decoded):
            raise DecodeError(
                "get_feature_list response shape did not match",
                detail="expected numeric keys and integer values, not an identity or status payload",
            )
        return
    raise DecodeError(
        f"no response schema for {short_topic}",
        detail=short_topic,
    )


def _require_accepted_ack(decoded: dict[str, Any]) -> None:
    """Accept an integer result code or the observed publish echo. Not an arbitrary dict."""
    code = decoded.get("1")
    if isinstance(code, bool):
        raise DecodeError(
            "subscribe ack field 1 was not an accepted code or publish echo",
            detail="field 1 type bool",
        )
    if isinstance(code, int):
        if code in REJECTED_RESULT_CODES:
            raise QueryRejected(
                "subscribe ack was rejected",
                detail=f"result_code={code}",
            )
        if code not in ACCEPTED_ACK_CODES:
            raise DecodeError(
                "subscribe ack result code was not accepted",
                detail=f"result_code={code}",
            )
        return
    if _is_publish_echo(decoded):
        return
    raise DecodeError(
        "subscribe ack field 1 was not an accepted code or publish echo",
        detail=f"field 1 type {type(code).__name__}",
    )


def _is_publish_echo(decoded: dict[str, Any]) -> bool:
    """Shape only. Field meanings are not promoted into canonical telemetry."""
    table = decoded.get("1")
    if not isinstance(table, dict) or not table:
        return False
    for key, value in table.items():
        if not str(key).isdigit():
            return False
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
    observed = decoded.get("2")
    if isinstance(observed, bool) or not isinstance(observed, int) or observed < 0:
        return False
    return _firmware_like(decoded.get("3"))


def _firmware_like(value: Any) -> bool:
    if isinstance(value, (bytes, bytearray)):
        text = bytes(value).decode("utf-8", errors="replace")
    elif isinstance(value, str):
        text = value
    else:
        return False
    return _FIRMWARE_TEXT.match(text.strip()) is not None


def _status_body(decoded: dict[str, Any]) -> dict[str, Any]:
    inner = decoded.get("2")
    if isinstance(inner, dict):
        return inner
    return decoded


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _map_signature(body: dict[str, Any]) -> bool:
    """Width+height, or a compressed grid. Field 1 alone is not a map."""
    width = body.get("4")
    height = body.get("5")
    if _finite_number(width) and _finite_number(height):
        return True
    grid = body.get("17")
    if isinstance(grid, (bytes, bytearray)) and grid:
        return True
    return isinstance(grid, str) and grid != ""


def _is_base_status(decoded: dict[str, Any]) -> bool:
    if parse_identity(decoded) is not None or _is_publish_echo(decoded):
        return False
    if _firmware_like(decoded.get("3")) and not isinstance(decoded.get("2"), dict):
        return False
    body = _status_body(decoded)
    if _map_signature(body):
        return False
    battery_ok = "2" in body and _finite_number(body.get("2"))
    mode_ok = isinstance(body.get("3"), dict)
    return battery_ok or mode_ok


def _is_map(decoded: dict[str, Any]) -> bool:
    if _is_publish_echo(decoded):
        return False
    payload = decoded.get("2")
    if not isinstance(payload, dict):
        return False
    return _map_signature(payload)


def _is_feature_list(decoded: dict[str, Any]) -> bool:
    """A one-field result ack is not a feature list. A real list has many integer fields."""
    if _is_publish_echo(decoded) or len(decoded) < 2:
        return False
    for key, value in decoded.items():
        if not str(key).isdigit():
            return False
        if isinstance(value, bool) or not isinstance(value, int):
            return False
    return True
