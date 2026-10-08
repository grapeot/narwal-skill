"""Write-command payload builders and response semantics.

Control payloads are re-implemented from the pinned upstream control client
(sjmotew/NarwalIntegration @ 867706aa352729bd5367c258d4fd42de7cfd19a1). The
upstream control client is not vendored; only the payloads this project needs
are reproduced here, with attribution retained in NOTICE.

Nothing in this module sends anything. It builds bytes and interprets result
codes; the session applies the allowlist and puts frames on the wire.
"""

from __future__ import annotations

from typing import Any

from narwal_skill.errors import DecodeError

EMPTY_CONTROL_TOPICS = {
    "pause": "task/pause",
    "resume": "task/resume",
    "stop": "task/force_end",
    "dock": "supply/recall",
}

# TaskResult field 1 result codes for control commands.
RESULT_SUCCESS = 1
RESULT_NOT_APPLICABLE = 2
RESULT_CONFLICT = 3
RESULT_NOT_READY = 4
RESULT_APPLIED = 6
ACCEPTED_RESULT_CODES = frozenset({RESULT_SUCCESS, RESULT_APPLIED})
DECLINED_RESULT_CODES = frozenset({RESULT_NOT_APPLICABLE, RESULT_CONFLICT, RESULT_NOT_READY})
RESULT_NAMES = {
    RESULT_SUCCESS: "success",
    RESULT_NOT_APPLICABLE: "not_applicable",
    RESULT_CONFLICT: "conflict",
    RESULT_NOT_READY: "not_ready",
    RESULT_APPLIED: "applied",
}

# WorkMode value -> (CleanParam.mode tag 1, pass-count tags). Mirrors upstream
# _WORK_MODE_PARAM so the outer taskType and the per-room param cannot drift.
WORK_MODES: dict[str, int] = {
    "vacuum": 1,
    "mop": 2,
    "vacuum_then_mop": 3,
    "vacuum_and_mop": 4,
}
WORK_MODE_PARAM: dict[int, tuple[int, tuple[int, ...]]] = {
    1: (2, (5,)),
    2: (3, (6,)),
    3: (5, (5, 6)),
    4: (4, (7,)),
}

FAN_LEVELS: dict[str, int] = {
    "mute": 1,
    "normal": 2,
    "strong": 3,
    "deep": 4,
    "super": 5,
}
WATER_LEVELS: dict[str, int] = {"dry": 1, "normal": 2, "wet": 3}
MOP_STRENGTH_NORMAL = 1


def _varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def _varint_field(field_num: int, value: int) -> bytes:
    return _varint((field_num << 3) | 0) + _varint(value)


def _bytes_field(field_num: int, data: bytes) -> bytes:
    return _varint((field_num << 3) | 2) + _varint(len(data)) + data


def clean_param(*, mode: str, fan: str, water: str, passes: int) -> bytes:
    """Encode CleanParam for one room.

    Tag 1 is the mode, tag 2 the fan tier, tag 3 the mop strength, tag 4 the
    water volume, and the mode-specific pass tag carries the pass count.
    """
    if mode not in WORK_MODES:
        raise ValueError(f"unknown mode {mode!r}")
    if fan not in FAN_LEVELS:
        raise ValueError(f"unknown fan {fan!r}")
    if water not in WATER_LEVELS:
        raise ValueError(f"unknown water {water!r}")
    if not 1 <= passes <= 3:
        raise ValueError(f"passes must be 1..3, got {passes}")
    param_mode, pass_tags = WORK_MODE_PARAM[WORK_MODES[mode]]
    body = _varint_field(1, param_mode)
    body += _varint_field(2, FAN_LEVELS[fan])
    body += _varint_field(3, MOP_STRENGTH_NORMAL)
    body += _varint_field(4, WATER_LEVELS[water])
    for tag in pass_tags:
        body += _varint_field(tag, passes)
    return body


def clean_item(room_id: int, *, order: int, param: bytes) -> bytes:
    zone = _varint_field(1, 1) + _varint_field(2, room_id)
    return _bytes_field(1, zone) + _bytes_field(2, param) + _varint_field(3, order)


def start_clean_payload(
    room_ids: list[int],
    map_id: int,
    *,
    mode: str = "vacuum_and_mop",
    fan: str = "normal",
    water: str = "normal",
    passes: int = 1,
) -> bytes:
    """Encode StartClean_Request{1: CleanTask{1: map_id, 2: [CleanItem], 3: {}, 5: taskType}}."""
    if not room_ids:
        raise ValueError("at least one room id is required")
    if map_id <= 0:
        raise ValueError("a positive map id is required")
    task_type = WORK_MODES[mode]
    param = clean_param(mode=mode, fan=fan, water=water, passes=passes)
    items = b"".join(
        _bytes_field(2, clean_item(room_id, order=index + 1, param=param))
        for index, room_id in enumerate(room_ids)
    )
    task = _varint_field(1, map_id) + items + _bytes_field(3, b"") + _varint_field(5, task_type)
    return _bytes_field(1, task)


def encode_control_payload(
    action: str,
    *,
    room_ids: list[int] | None = None,
    map_id: int | None = None,
    mode: str = "vacuum_and_mop",
    fan: str = "normal",
    water: str = "normal",
    passes: int = 1,
) -> tuple[str, bytes]:
    """Return (short_topic, payload) for one control action."""
    if action in EMPTY_CONTROL_TOPICS:
        return EMPTY_CONTROL_TOPICS[action], b""
    if action in {"start", "clean"}:
        rooms = room_ids or []
        return "clean/start_clean", start_clean_payload(
            rooms,
            int(map_id or 0),
            mode=mode,
            fan=fan,
            water=water,
            passes=passes,
        )
    raise ValueError(f"unknown control action {action!r}")


def control_result(decoded: dict[str, Any]) -> tuple[int | None, bool]:
    """Return (result_code, accepted) for a control response.

    Field 1 an int -> that result code. A dict -> the room-clean config echo,
    treated as accepted. Anything else is a shape error.
    """
    if not isinstance(decoded, dict):
        raise DecodeError(
            "control response was not a message",
            detail=f"decoded type {type(decoded).__name__}",
        )
    code = decoded.get("1")
    if isinstance(code, bool):
        raise DecodeError(
            "control response field 1 was a bool",
            detail="expected an integer result code or a config echo message",
        )
    if isinstance(code, int):
        return code, code in ACCEPTED_RESULT_CODES
    if isinstance(code, dict):
        return None, True
    if code is None:
        raise DecodeError(
            "control response had no field 1",
            detail="expected result code or config echo",
        )
    raise DecodeError(
        "control response field 1 had an unexpected type",
        detail=f"field 1 type {type(code).__name__}",
    )
