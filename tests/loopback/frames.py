"""Synthetic frame builders. No real captures."""

from __future__ import annotations

import struct
import zlib

from narwal_skill.vendor.narwal_client.protocol import PROTOBUF_FIELD_TAG, build_frame


def _varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def field_varint(field_num: int, value: int) -> bytes:
    return _varint((field_num << 3) | 0) + _varint(value)


def field_bytes(field_num: int, data: bytes) -> bytes:
    return _varint((field_num << 3) | 2) + _varint(len(data)) + data


def field_float(field_num: int, value: float) -> bytes:
    return _varint((field_num << 3) | 5) + struct.pack("<f", value)


def field5(payload: bytes, topic: str = "") -> bytes:
    topic_bytes = topic.encode("utf-8")
    header = (len(topic_bytes) + 2) & 0xFF
    return bytes([0x01, header, 0x2A, len(topic_bytes)]) + topic_bytes + payload


def field4(topic: str, payload: bytes) -> bytes:
    frame = build_frame(topic, payload)
    assert frame[2] == PROTOBUF_FIELD_TAG
    return frame


def publish_echo_payload(firmware: bytes = b"v01.00.00.00\n") -> bytes:
    rates = b"".join(field_varint(number, 1000) for number in (1, 2, 3, 5, 6))
    return field_bytes(1, rates) + field_varint(2, 30000) + field_bytes(3, firmware)


def identity_payload(device_id: str = "fake-device-001", product_key: str = "QxMSPG6VSO") -> bytes:
    return (
        field_bytes(1, product_key.encode())
        + field_bytes(2, device_id.encode())
        + field_bytes(3, b"v01.00.00.00")
    )


def base_status_payload(*, battery: float | None, mode: int, include_faults: bool = False) -> bytes:
    inner = b""
    if include_faults:
        inner += field_bytes(1, b"")
    if battery is not None:
        inner += field_float(2, battery)
    inner += field_bytes(3, field_varint(1, mode))
    return field_bytes(2, inner)


def working_payload(
    *,
    progress: float | None = 92.0,
    area: float | None = 1.25,
    elapsed: int | None = 40,
    station_bag: int | None = 18000,
    cron: str | None = None,
) -> bytes:
    payload = b""
    if progress is not None:
        payload += field_float(1, progress)
    if area is not None:
        payload += field_float(2, area)
    if elapsed is not None:
        payload += field_varint(3, elapsed)
    if station_bag is not None:
        payload += field_varint(13, station_bag)
    if cron is not None:
        payload += field_bytes(20, cron.encode())
    return payload


def toy_map_payload(width: int = 4, height: int = 4) -> bytes:
    pixels = [0] * (width * height)
    pixels[0] = 0x20
    pixels[5] = (1 << 8) | 0x01
    pixels[6] = (1 << 8) | 0x10
    body = b"".join(_varint(pixel) for pixel in pixels)
    wrapped = bytes([0x0A]) + _varint(len(body)) + body
    compressed = zlib.compress(wrapped)
    room = field_varint(1, 1) + field_varint(2, 0)
    origin = field_varint(1, 0) + field_varint(3, 0)
    dock = field_bytes(1, field_float(1, 1.0) + field_float(2, 1.0))
    inner = (
        field_varint(1, 7)
        + field_varint(3, 60)
        + field_varint(4, width)
        + field_varint(5, height)
        + field_bytes(6, origin)
        + field_bytes(8, dock)
        + field_bytes(12, room)
        + field_bytes(17, compressed)
        + field_varint(33, 12)
    )
    return field_bytes(2, inner)


def display_map_payload(
    x: float,
    y: float,
    timestamp_ms: int = 1_700_000_000_000,
    heading: float = 0.25,
) -> bytes:
    point = field_bytes(1, field_float(1, x) + field_float(2, y)) + field_float(2, heading)
    return field_bytes(1, point) + field_varint(10, timestamp_ms)
