"""Normalize read-only telemetry. Missing values stay null. Zero stays zero."""

from __future__ import annotations

import json
import math
import struct
from typing import Any

from narwal_skill.config import FLOW2_PRODUCT_KEYS

# Public working-status codes. Mode 5 is cleaning_alt, not a stuck assertion.
# 99 is an upstream placeholder that has not been observed; it is not a fault claim.
MODE_SEMANTICS: dict[int, str] = {
    1: "standby",
    2: "docked_v2",
    3: "cleaning_v2",
    4: "cleaning",
    5: "cleaning_alt",
    7: "remapping",
    10: "docked",
    14: "charged",
    17: "custom_cleaning",
    19: "task_completed",
}

REJECTED_RESULT_CODES = frozenset({2, 3, 4})


def reported_number(value: Any) -> float | None:
    """Float32 fields may arrive as bit-pattern ints. Small ints stay numeric."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, int):
        if value > 1000 or value < 0:
            return to_float32(value)
        return float(value)
    return to_float32(value)


def to_float32(value: Any) -> float | None:
    """Accept a decoded float or a fixed32 bit pattern. Absent stays None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, int):
        try:
            decoded = struct.unpack("<f", struct.pack("<I", value & 0xFFFFFFFF))[0]
            return decoded if math.isfinite(decoded) else None
        except struct.error:
            return None
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace").strip().strip("\x00")
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("b'") and text.endswith("'"):
            text = text[2:-1]
        return text
    return None


def is_rejected_result(decoded: dict[str, Any]) -> int | None:
    """Integer result codes are not query data. Non-integer field 1 can be payload."""
    field1 = decoded.get("1")
    if isinstance(field1, bool):
        return None
    if isinstance(field1, int) and field1 in REJECTED_RESULT_CODES:
        return field1
    return None


def parse_identity(decoded: dict[str, Any]) -> dict[str, str] | None:
    """get_device_info fields 1/2/3 are bytes. A result code is not identity."""
    if is_rejected_result(decoded) is not None:
        return None
    product_key = _text(decoded.get("1"))
    device_id = _text(decoded.get("2"))
    if "3" not in decoded:
        return None
    firmware = _text(decoded.get("3"))
    if product_key is None or device_id is None or firmware is None:
        return None
    if not product_key or not device_id:
        return None
    return {
        "product_key": product_key,
        "device_id": device_id,
        "firmware_version": firmware,
    }


def unwrap_base_status(decoded: dict[str, Any]) -> dict[str, Any] | None:
    """Query responses wrap RobotBaseStatus in field 2. Broadcasts are the payload."""
    if is_rejected_result(decoded) is not None:
        return None
    inner = decoded.get("2")
    if isinstance(inner, dict) and any(key in inner for key in ("1", "2", "3", "11", "47")):
        return inner
    if isinstance(decoded.get("3"), dict) or ("2" in decoded and not isinstance(decoded.get("2"), dict)):
        return decoded
    return None


def _mode_dict(payload: dict[str, Any]) -> dict[str, Any] | None:
    field3 = payload.get("3")
    if isinstance(field3, list):
        field3 = field3[0] if field3 else None
    if isinstance(field3, dict):
        return field3
    return None


def normalize_mode(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Raw mode dict plus a public-enum semantic. Unknown codes stay unknown.

    Working substage is not inferred here. Mode 5 is not labeled stuck.
    """
    if not payload:
        return {
            "raw": None,
            "code": None,
            "semantic": "unknown",
            "stuck_inferred": False,
            "substage": None,
        }
    raw = _mode_dict(payload)
    code: int | None = None
    if raw is not None and "1" in raw and not isinstance(raw.get("1"), bool):
        try:
            code = int(raw["1"])
        except (TypeError, ValueError):
            code = None
    semantic = MODE_SEMANTICS.get(code, "unknown") if code is not None else "unknown"
    return {
        "raw": raw,
        "code": code,
        "semantic": semantic,
        "stuck_inferred": False,
        "substage": None,
    }


def fault_view(payload: dict[str, Any] | None) -> dict[str, Any]:
    """An absent or undecodable error list is not a healthy claim and not a fake code list."""
    unknown = {
        "fault_codes": None,
        "health": "unknown",
        "healthy": None,
        "fault_decode_error": None,
    }
    if not payload or "1" not in payload:
        return unknown
    raw = payload.get("1")
    unknown["fault_raw"] = raw
    if raw == {}:
        return {
            "fault_codes": [],
            "health": "unknown",
            "healthy": False,
            "fault_decode_error": None,
            "fault_raw": {},
        }
    try:
        codes = _fault_codes(raw)
    except ValueError as exc:
        unknown["fault_decode_error"] = str(exc)
        return unknown
    return {
        "fault_codes": codes,
        "health": "faults_reported" if codes else "unknown",
        "healthy": False,
        "fault_decode_error": None,
        "fault_raw": raw,
    }


def decode_packed_varints(data: bytes) -> list[int]:
    """Decode a protobuf packed varint blob. Truncation is an error, not a byte list."""
    codes: list[int] = []
    pos = 0
    while pos < len(data):
        value = 0
        shift = 0
        while True:
            if pos >= len(data):
                raise ValueError("truncated packed varint")
            byte = data[pos]
            pos += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
            if shift > 63:
                raise ValueError("packed varint wider than 64 bits")
        codes.append(value)
    return codes


def _identity_code(entry: dict[str, Any]) -> int:
    if "1" not in entry:
        raise ValueError("ErrorCode message has no identity code")
    code = entry["1"]
    if isinstance(code, bool) or not isinstance(code, int):
        raise ValueError("ErrorCode identity field is not an integer")
    return code


def _fault_codes(raw: Any) -> list[int]:
    if raw is None:
        return []
    if isinstance(raw, bool):
        raise ValueError("fault field is not an integer code")
    if isinstance(raw, int):
        return [raw]
    if isinstance(raw, dict):
        return [_identity_code(raw)]
    if isinstance(raw, list):
        if not raw:
            return []
        if all(isinstance(item, dict) for item in raw):
            return [_identity_code(item) for item in raw]
        codes: list[int] = []
        for item in raw:
            if isinstance(item, bool) or not isinstance(item, int):
                raise ValueError("fault list contains a non-integer")
            codes.append(item)
        return codes
    if isinstance(raw, (bytes, bytearray)):
        return decode_packed_varints(bytes(raw))
    if isinstance(raw, str):
        if any(ord(ch) > 255 for ch in raw):
            raise ValueError("fault string is not packed-varint bytes")
        return decode_packed_varints(raw.encode("latin-1"))
    raise ValueError(f"fault field type {type(raw).__name__} is not a packed varint")


def normalize_base(payload: dict[str, Any] | None, *, source: str, observed_at: str) -> dict[str, Any]:
    battery = to_float32(payload.get("2")) if payload and "2" in payload else None
    faults = fault_view(payload)
    mode = normalize_mode(payload)
    dock_raw: dict[str, Any] = {}
    if payload:
        for key in ("11", "47"):
            if key in payload:
                dock_raw[key] = payload.get(key)
        if isinstance(mode.get("raw"), dict):
            for key in ("10", "12"):
                if key in mode["raw"]:
                    dock_raw[f"3.{key}"] = mode["raw"].get(key)
    return {
        "observed_at": observed_at,
        "source": source,
        "battery_percent": battery,
        "mode": mode,
        "fault_codes": faults["fault_codes"],
        "health": faults["health"],
        "healthy": None,
        "fault_decode_error": faults["fault_decode_error"],
        "fault_raw": faults.get("fault_raw"),
        "charging_status_raw": payload.get("47") if payload and "47" in payload else None,
        "charging_state": None,
        "dock_raw": dock_raw or None,
        "is_docked": None,
    }


def normalize_progress(raw: Any, product_key: str | None) -> dict[str, Any]:
    """Flow 2 reports percent-scale floats (observed 3 and 92). Do not multiply by 100.

    Other models are left unnormalized. The raw value is always retained.
    """
    if raw is None:
        return {
            "raw": None,
            "progress_percent": None,
            "normalization": "absent",
            "ambiguous": False,
            "source_field": "1",
        }
    number = reported_number(raw)
    if number is None:
        return {
            "raw": raw if isinstance(raw, (int, float, str)) else None,
            "progress_percent": None,
            "normalization": "not_normalized",
            "ambiguous": True,
            "source_field": "1",
        }
    number = float(number)
    if product_key in FLOW2_PRODUCT_KEYS:
        if number == 0.0 or 1.0 < number <= 100.0:
            return {
                "raw": number,
                "progress_percent": number,
                "normalization": "flow2_reported_percent",
                "ambiguous": False,
                "source_field": "1",
            }
        return {
            "raw": number,
            "progress_percent": None,
            "normalization": "not_normalized",
            "ambiguous": True,
            "source_field": "1",
        }
    return {
        "raw": number,
        "progress_percent": None,
        "normalization": "not_normalized",
        "ambiguous": True,
        "source_field": "1",
    }


def _optional_number(payload: dict[str, Any], key: str, *, as_float: bool) -> int | float | None:
    if key not in payload:
        return None
    value = payload[key]
    if as_float:
        number = value if isinstance(value, float) else to_float32(value)
        return None if number is None else float(number)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def extract_schedule_fragment(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Record an embedded cron JSON blob as a fragment, never as the full schedule."""
    found = _find_cron(payload, depth=0)
    if found is None:
        return None
    return {
        "complete": False,
        "source": "working_status_embedded_json",
        "fragment": found,
        "note": "This is the fragment embedded in the current working status, not the full schedule list.",
    }


def _find_cron(value: Any, depth: int) -> Any:
    if depth > 6:
        return None
    if isinstance(value, dict):
        if _looks_like_schedule(value):
            return value
        for item in value.values():
            found = _find_cron(item, depth + 1)
            if found is not None:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_cron(item, depth + 1)
            if found is not None:
                return found
        return None
    text = _text(value)
    if not text or not text.startswith(("{", "[")):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return _find_cron(parsed, depth + 1)


def _looks_like_schedule(obj: dict[str, Any]) -> bool:
    keys = {str(key).lower() for key in obj}
    if keys & {"cron", "crontab", "schedule", "clean_schedule"}:
        return True
    for item in obj.values():
        if isinstance(item, str) and _cron_expression(item):
            return True
    return False


def _cron_expression(text: str) -> bool:
    parts = text.split()
    return 5 <= len(parts) <= 6 and all(part for part in parts)


def normalize_working(
    payload: dict[str, Any] | None,
    *,
    product_key: str | None,
    source: str | None,
    observed_at: str | None,
) -> dict[str, Any]:
    """Task metrics come only from working_status. Field 13 is a station timer, not area."""
    if not payload:
        return {
            "observed_at": observed_at,
            "source": source,
            "area_m2": None,
            "elapsed_seconds": None,
            "remaining_seconds": None,
            "progress": normalize_progress(None, product_key),
            "station_bag_dry_total_seconds": None,
            "schedule_fragment": None,
        }
    area = _optional_number(payload, "2", as_float=True)
    elapsed = _optional_number(payload, "3", as_float=False)
    remaining = _optional_number(payload, "4", as_float=False)
    station = _optional_number(payload, "13", as_float=False)
    progress_raw = payload.get("1") if "1" in payload else None
    return {
        "observed_at": observed_at,
        "source": source,
        "area_m2": area,
        "elapsed_seconds": elapsed,
        "remaining_seconds": remaining,
        "progress": normalize_progress(progress_raw, product_key),
        "station_bag_dry_total_seconds": station,
        "schedule_fragment": extract_schedule_fragment(payload),
    }


def broadcast_absence() -> dict[str, Any]:
    return {
        "observed": False,
        "robot_state": "unknown",
        "topics": [],
        "note": "No broadcast was observed. That is unknown, not asleep.",
    }
