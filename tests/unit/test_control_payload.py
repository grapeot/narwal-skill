"""Control payload encoding and result semantics. Byte-exact, offline."""

from __future__ import annotations

import pytest

from narwal_skill.control import (
    clean_param,
    control_result,
    encode_control_payload,
    start_clean_payload,
)
from narwal_skill.errors import DecodeError


def _varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def _vf(field: int, value: int) -> bytes:
    return _varint((field << 3) | 0) + _varint(value)


def _bf(field: int, data: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(data)) + data


def test_empty_control_topics() -> None:
    for action, topic in (
        ("pause", "task/pause"),
        ("resume", "task/resume"),
        ("stop", "task/force_end"),
        ("dock", "supply/recall"),
    ):
        short, payload = encode_control_payload(action)
        assert short == topic
        assert payload == b""


def test_start_clean_payload_is_byte_exact() -> None:
    # One room, vacuum_and_mop, normal fan, normal water, one pass.
    # taskType 4 -> CleanParam.mode 4, pass tag 7.
    param = _vf(1, 4) + _vf(2, 2) + _vf(3, 1) + _vf(4, 2) + _vf(7, 1)
    zone = _vf(1, 1) + _vf(2, 3)
    item = _bf(1, zone) + _bf(2, param) + _vf(3, 1)
    task = _vf(1, 7) + _bf(2, item) + _bf(3, b"") + _vf(5, 4)
    expected = _bf(1, task)
    assert start_clean_payload([3], 7) == expected


def test_two_rooms_repeat_clean_item_with_incrementing_order() -> None:
    payload = start_clean_payload([3, 5], 9, mode="vacuum", fan="strong", water="wet", passes=2)
    # vacuum -> taskType 1, CleanParam.mode 2, pass tag 5.
    param = _vf(1, 2) + _vf(2, 3) + _vf(3, 1) + _vf(4, 3) + _vf(5, 2)
    item1 = _bf(1, _vf(1, 1) + _vf(2, 3)) + _bf(2, param) + _vf(3, 1)
    item2 = _bf(1, _vf(1, 1) + _vf(2, 5)) + _bf(2, param) + _vf(3, 2)
    task = _vf(1, 9) + _bf(2, item1) + _bf(2, item2) + _bf(3, b"") + _vf(5, 1)
    assert payload == _bf(1, task)


def test_super_fan_is_encoded_as_five() -> None:
    # clean/start_clean writes the fan tier unclamped; a Flow 2 accepts SUPER (5).
    param = clean_param(mode="vacuum_and_mop", fan="super", water="normal", passes=1)
    assert param == _vf(1, 4) + _vf(2, 5) + _vf(3, 1) + _vf(4, 2) + _vf(7, 1)
    deep = clean_param(mode="vacuum_and_mop", fan="deep", water="normal", passes=1)
    assert param != deep


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "nope"},
        {"fan": "turbo"},
        {"water": "soaked"},
    ],
)
def test_bad_clean_param_raises(kwargs: dict) -> None:
    base = {"mode": "vacuum_and_mop", "fan": "normal", "water": "normal", "passes": 1}
    base.update(kwargs)
    with pytest.raises(ValueError):
        clean_param(**base)


def test_passes_bounds() -> None:
    with pytest.raises(ValueError):
        clean_param(mode="vacuum", fan="normal", water="normal", passes=0)
    with pytest.raises(ValueError):
        clean_param(mode="vacuum", fan="normal", water="normal", passes=4)


def test_start_clean_requires_rooms_and_map_id() -> None:
    with pytest.raises(ValueError):
        start_clean_payload([], 5)
    with pytest.raises(ValueError):
        start_clean_payload([3], 0)


def test_control_result_accepts_codes_and_echo() -> None:
    assert control_result({"1": 1}) == (1, True)
    assert control_result({"1": 6}) == (6, True)
    assert control_result({"1": 4}) == (4, False)
    assert control_result({"1": 3}) == (3, False)
    code, accepted = control_result({"1": {"1": 100}})
    assert code is None
    assert accepted is True


@pytest.mark.parametrize("payload", [{"1": True}, {"1": "x"}, {}])
def test_control_result_rejects_bad_shapes(payload: dict) -> None:
    with pytest.raises(DecodeError):
        control_result(payload)


def test_control_result_returns_unknown_code_for_range_check() -> None:
    # Range validation is the shape validator's job, not control_result's.
    assert control_result({"1": 99}) == (99, False)


def test_unknown_code_is_rejected_by_shape_validator() -> None:
    from narwal_skill.response_shape import validate_control_response

    with pytest.raises(DecodeError):
        validate_control_response("task/pause", {"1": 99})
    validate_control_response("task/pause", {"1": 1})
    validate_control_response("task/pause", {"1": 4})
    validate_control_response("task/pause", {"1": 3})
