"""Protocol loopback tests against a fake robot."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from narwal_skill.allowlist import ALLOWED_SEND_TOPICS, FORBIDDEN_SEND_TOPICS
from narwal_skill.commands import Runner
from narwal_skill.config import resolve_settings
from narwal_skill.errors import (
    SAME_IP_CLOSE,
    ConnectionCap,
    DecodeError,
    QueryRejected,
    QueryTimeout,
    TransportError,
)
from narwal_skill.session import ReadOnlySession
from narwal_skill.vendor.narwal_client.protocol import parse_frame
from tests.loopback.fake_robot import FakeRobot
from tests.loopback.frames import base_status_payload, field4, field5, field_varint, identity_payload


async def _session(port: int, timeout: float = 1.0) -> ReadOnlySession:
    session = ReadOnlySession(
        url=f"ws://127.0.0.1:{port}",
        connect_timeout_s=1.0,
        deadline=time.monotonic() + 5,
    )
    await session.connect()
    return session


async def test_empty_topic_reply_keeps_interleaved_broadcast() -> None:
    seen = []

    async def handler(ws, raw, short, conn):
        seen.append(short)
        topic = f"/QxMSPG6VSO/fake-device-001/{short}"
        await ws.send(field4(topic.replace(short, "status/working_status"), b"\x08\x01"))
        await ws.send(field5(identity_payload(), topic=""))

    robot = FakeRobot(handler)
    await robot.start()
    try:
        session = await _session(robot.port)
        result = await session.query(
            "common/get_device_info",
            full_topic="/QxMSPG6VSO/fake-device-001/common/get_device_info",
            timeout_s=1.0,
        )
        assert result.response_topic == ""
        assert result.decoded["2"] in {"fake-device-001", b"fake-device-001"}
        assert session.broadcasts
        assert session.broadcasts[0].short_topic == "status/working_status"
        parsed = parse_frame(field5(identity_payload()))
        assert parsed.topic == ""
        assert parsed.field_tag == 0x2A
        await session.aclose()
    finally:
        await robot.stop()


async def test_subscription_ack_is_consumed_before_next_query() -> None:
    base = base_status_payload(battery=11.0, mode=1)
    replies = {"common/active_robot_publish": b"\x08\x01", "status/get_device_base_status": base}

    async def handler(ws, raw, short, conn):
        await ws.send(field5(replies[short]))

    robot = FakeRobot(handler)
    await robot.start()
    try:
        session = await _session(robot.port)
        ack = await session.subscribe(
            3,
            full_topic="/QxMSPG6VSO/fake-device-001/common/active_robot_publish",
            timeout_s=1.0,
        )
        status = await session.query(
            "status/get_device_base_status",
            full_topic="/QxMSPG6VSO/fake-device-001/status/get_device_base_status",
            timeout_s=1.0,
        )
        assert ack.payload == b"\x08\x01"
        assert status.payload == base
        assert robot.received == [
            "common/active_robot_publish",
            "status/get_device_base_status",
        ]
        await session.aclose()
    finally:
        await robot.stop()


async def test_timeout_closes_and_late_reply_does_not_pollute_next_query() -> None:
    async def handler(ws, raw, short, conn):
        if conn == 1:
            await asyncio.sleep(0.45)
            try:
                await ws.send(field5(identity_payload("late-device")))
            except Exception:
                return
        else:
            await ws.send(field5(identity_payload("fake-device-002")))

    robot = FakeRobot(handler)
    await robot.start()
    try:
        first = await _session(robot.port)
        with pytest.raises(QueryTimeout) as caught:
            await first.query(
                "common/get_device_info",
                full_topic="/QxMSPG6VSO//common/get_device_info",
                timeout_s=0.12,
            )
        assert first._closed
        assert "socket closed" in caught.value.detail
        second = await _session(robot.port)
        result = await second.query(
            "common/get_device_info",
            full_topic="/QxMSPG6VSO//common/get_device_info",
            timeout_s=1.0,
        )
        assert "fake-device-002" in json.dumps(result.decoded)
        assert "late-device" not in json.dumps(result.decoded)
        await second.aclose()
    finally:
        await robot.stop()


async def test_same_ip_close_detail_is_preserved() -> None:
    async def handler(ws, raw, short, conn):
        await ws.close(code=1000, reason=SAME_IP_CLOSE)

    robot = FakeRobot(handler)
    await robot.start()
    try:
        session = await _session(robot.port)
        with pytest.raises(TransportError) as caught:
            await session.query(
                "common/get_device_info",
                full_topic="/QxMSPG6VSO/fake-device-001/common/get_device_info",
                timeout_s=1.0,
            )
        assert SAME_IP_CLOSE in caught.value.detail
        await session.aclose()
    finally:
        await robot.stop()


async def test_delayed_field5_does_not_answer_a_different_query() -> None:
    async def handler(ws, raw, short, conn):
        if short == "common/active_robot_publish":
            await ws.send(field5(b"\x08\x01"))

            async def later() -> None:
                await asyncio.sleep(0.2)
                await ws.send(field5(identity_payload("poison-device")))

            asyncio.create_task(later())

    robot = FakeRobot(handler)
    await robot.start()
    try:
        session = await _session(robot.port)
        await session.subscribe(
            3,
            full_topic="/QxMSPG6VSO/fake-device-001/common/active_robot_publish",
            timeout_s=1.0,
        )
        with pytest.raises(DecodeError) as caught:
            await session.query(
                "common/get_feature_list",
                full_topic="/QxMSPG6VSO/fake-device-001/common/get_feature_list",
                timeout_s=1.0,
            )
        assert "poison-device" not in caught.value.detail
        assert not session.usable
        with pytest.raises(TransportError):
            await session.query(
                "status/get_device_base_status",
                full_topic="/QxMSPG6VSO/fake-device-001/status/get_device_base_status",
                timeout_s=0.5,
            )
        await session.aclose()
    finally:
        await robot.stop()


async def test_map_reply_is_not_accepted_as_base_status() -> None:
    from tests.loopback.frames import toy_map_payload

    async def handler(ws, raw, short, conn):
        await ws.send(field5(toy_map_payload()))

    robot = FakeRobot(handler)
    await robot.start()
    try:
        session = await _session(robot.port)
        with pytest.raises(DecodeError):
            await session.query(
                "status/get_device_base_status",
                full_topic="/QxMSPG6VSO/fake-device-001/status/get_device_base_status",
                timeout_s=1.0,
            )
        assert not session.usable
        await session.aclose()
    finally:
        await robot.stop()


async def test_nonempty_field5_topic_is_not_correlation() -> None:
    from tests.loopback.frames import base_status_payload

    async def handler(ws, raw, short, conn):
        await ws.send(field5(base_status_payload(battery=50.0, mode=1), topic="status/robot_base_status"))

    robot = FakeRobot(handler)
    await robot.start()
    try:
        session = await _session(robot.port)
        with pytest.raises(DecodeError) as caught:
            await session.query(
                "status/get_device_base_status",
                full_topic="/QxMSPG6VSO/fake-device-001/status/get_device_base_status",
                timeout_s=1.0,
            )
        assert "non-empty" in caught.value.detail or "non-empty" in caught.value.message
        assert not session.usable
        await session.aclose()
    finally:
        await robot.stop()


async def test_publish_echo_ack_is_not_a_later_base_status() -> None:
    from tests.loopback.frames import publish_echo_payload

    async def handler(ws, raw, short, conn):
        if short == "common/active_robot_publish":
            await ws.send(field5(publish_echo_payload()))

            async def later() -> None:
                await asyncio.sleep(0.15)
                await ws.send(field5(publish_echo_payload()))

            asyncio.create_task(later())

    robot = FakeRobot(handler)
    await robot.start()
    try:
        session = await _session(robot.port)
        ack = await session.subscribe(
            3,
            full_topic="/QxMSPG6VSO/fake-device-001/common/active_robot_publish",
            timeout_s=1.0,
        )
        assert isinstance(ack.decoded.get("1"), dict)
        with pytest.raises(DecodeError):
            await session.query(
                "status/get_device_base_status",
                full_topic="/QxMSPG6VSO/fake-device-001/status/get_device_base_status",
                timeout_s=1.0,
            )
        assert not session.usable
        await session.aclose()
    finally:
        await robot.stop()


async def test_subscribe_ack_reject_closes() -> None:
    async def handler(ws, raw, short, conn):
        await ws.send(field5(field_varint(1, 2)))

    robot = FakeRobot(handler)
    await robot.start()
    try:
        session = await _session(robot.port)
        with pytest.raises(QueryRejected):
            await session.subscribe(
                3,
                full_topic="/QxMSPG6VSO/fake-device-001/common/active_robot_publish",
                timeout_s=1.0,
            )
        assert not session.usable
        await session.aclose()
    finally:
        await robot.stop()


async def test_unsolicited_field5_closes_before_the_next_query() -> None:
    async def handler(ws, raw, short, conn):
        await ws.send(field5(b"\x08\x01"))
        await ws.send(field5(identity_payload("stray-device")))

    robot = FakeRobot(handler)
    await robot.start()
    try:
        session = await _session(robot.port)
        await session.subscribe(
            3,
            full_topic="/QxMSPG6VSO/fake-device-001/common/active_robot_publish",
            timeout_s=1.0,
        )
        await asyncio.sleep(0.05)
        with pytest.raises(TransportError) as caught:
            await session.query(
                "common/get_device_info",
                full_topic="/QxMSPG6VSO/fake-device-001/common/get_device_info",
                timeout_s=0.5,
            )
        assert "unexpected field5" in caught.value.detail
        await session.aclose()
    finally:
        await robot.stop()


async def test_connection_cap_is_not_a_budget_error() -> None:
    async def handler(ws, raw, short, conn):
        await asyncio.sleep(30)

    robot = FakeRobot(handler)
    await robot.start()
    try:
        settings = resolve_settings(
            host="127.0.0.1",
            port=robot.port,
            product_key="QxMSPG6VSO",
            device_id="fake-device-001",
            env_file=None,
            budget_s=10,
            query_timeout_s=1,
            coordinate_policy=None,
            grid_offset_x=None,
            grid_offset_y=None,
            render_scale=None,
            discover=False,
        )
        runner = Runner(settings, lambda _message: None)
        opened = []
        for _ in range(6):
            opened.append(await runner.open_session())
        with pytest.raises(ConnectionCap) as caught:
            await runner.open_session()
        assert caught.value.code == "connection_cap"
        assert robot.connections == 6
        for session in opened:
            await session.aclose()
    finally:
        await robot.stop()


def test_allowlist_has_no_actuation() -> None:
    assert ALLOWED_SEND_TOPICS.isdisjoint(FORBIDDEN_SEND_TOPICS)
    assert "developer/take_picture" not in ALLOWED_SEND_TOPICS
    assert "common/reboot" not in ALLOWED_SEND_TOPICS
    assert "clean/start_clean" not in ALLOWED_SEND_TOPICS
