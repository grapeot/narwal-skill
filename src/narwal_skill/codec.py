"""Protobuf decode and small encoders used by the read-only session."""

from __future__ import annotations

from typing import Any

from narwal_skill.allowlist import SUBSCRIBE_TOPICS
from narwal_skill.errors import DecodeError


def decode_payload(payload: bytes) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        import blackboxprotobuf
    except ImportError as exc:
        raise DecodeError("bbpb is not installed", detail=str(exc), cause=exc) from exc
    try:
        decoded, _typedef = blackboxprotobuf.decode_message(payload)
    except Exception as exc:
        raise DecodeError(
            "protobuf decode failed",
            detail=f"{type(exc).__name__}: {exc}",
            cause=exc,
        ) from exc
    if not isinstance(decoded, dict):
        raise DecodeError(
            "protobuf decode was not a message",
            detail=f"decoded type {type(decoded).__name__}",
        )
    return decoded


def _varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def _varint_field(field_num: int, value: int) -> bytes:
    tag = (field_num << 3) | 0
    return _varint(tag) + _varint(value)


def _bytes_field(field_num: int, data: bytes) -> bytes:
    tag = (field_num << 3) | 2
    return _varint(tag) + _varint(len(data)) + data


def subscription_payload(duration_s: int, topics: tuple[str, ...] = SUBSCRIBE_TOPICS) -> bytes:
    """TopicDuration list for common/active_robot_publish. Duration is bounded by the caller."""
    if duration_s < 1 or duration_s > 300:
        raise ValueError("subscription duration must be 1..300 seconds")
    payload = b""
    for topic in topics:
        inner = _bytes_field(1, topic.encode("utf-8")) + _varint_field(2, duration_s)
        payload += _bytes_field(1, inner)
    return payload


def heartbeat_payload() -> bytes:
    return _varint_field(1, 1)
