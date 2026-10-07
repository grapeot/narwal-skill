"""Wrong field5 shapes are rejected without a socket."""

from __future__ import annotations

import pytest

from narwal_skill.codec import decode_payload
from narwal_skill.errors import DecodeError, QueryRejected
from narwal_skill.response_shape import validate_query_response
from tests.loopback.frames import identity_payload


def test_feature_list_rejects_identity_and_singleton_ack() -> None:
    identity = decode_payload(identity_payload("poison-device"))
    with pytest.raises(DecodeError):
        validate_query_response("common/get_feature_list", identity)
    with pytest.raises(DecodeError):
        validate_query_response("common/get_feature_list", {"1": 1})
    validate_query_response("common/get_feature_list", {str(i): i for i in range(1, 84)})


def test_identity_base_and_map_reject_the_wrong_shape() -> None:
    identity = decode_payload(identity_payload())
    with pytest.raises(DecodeError):
        validate_query_response("status/get_device_base_status", identity)
    with pytest.raises(DecodeError):
        validate_query_response("map/get_map", identity)
    with pytest.raises(DecodeError):
        validate_query_response("common/get_device_info", {"1": 1, "2": 2, "3": 3})
    validate_query_response("common/get_device_info", identity)


def test_map_payload_is_not_accepted_as_base_status() -> None:
    live_map = {
        "2": {
            "1": 7,
            "3": 60,
            "4": 120,
            "5": 80,
            "17": b"\x78\x9c\x01\x00",
        }
    }
    live_base = {"2": {"2": 1118175232, "3": {"1": 10}}}
    with pytest.raises(DecodeError):
        validate_query_response("status/get_device_base_status", live_map)
    validate_query_response("map/get_map", live_map)
    with pytest.raises(DecodeError):
        validate_query_response("map/get_map", live_base)
    with pytest.raises(DecodeError):
        validate_query_response("map/get_map", {"2": {"1": 7}})
    validate_query_response("status/get_device_base_status", live_base)
    validate_query_response("status/get_device_base_status", {"2": {"2": 1118175232}})
    validate_query_response("status/get_device_base_status", {"2": {"3": {"1": 5}}})
    with pytest.raises(DecodeError):
        validate_query_response("status/get_device_base_status", {"1": 1})


def _publish_echo() -> dict:
    return {
        "1": {"1": 1000, "2": 1000, "3": 1000, "5": 1000, "6": 1000},
        "2": 30000,
        "3": b"v01.00.00.00\n",
    }


def test_publish_echo_is_an_ack_and_not_base_feature_or_map() -> None:
    echo = _publish_echo()
    validate_query_response("common/active_robot_publish", echo)
    validate_query_response("common/active_robot_publish", {**echo, "3": "v01.00.00.00"})
    for topic in (
        "status/get_device_base_status",
        "common/get_feature_list",
        "map/get_map",
    ):
        with pytest.raises(DecodeError):
            validate_query_response(topic, echo)
    poison = {"1": {"1": "QxMSPG6VSO"}, "2": "poison-device", "3": b"v01.00.00.00"}
    with pytest.raises(DecodeError):
        validate_query_response("common/active_robot_publish", poison)
    with pytest.raises(DecodeError):
        validate_query_response("common/active_robot_publish", {"1": {}})
    validate_query_response("status/get_device_base_status", {"2": {"2": 1118175232, "3": {"1": 10}}})
    validate_query_response("common/get_feature_list", {str(i): i for i in range(1, 84)})


def test_subscribe_ack_non_applicable_is_rejected() -> None:
    with pytest.raises(QueryRejected):
        validate_query_response("common/active_robot_publish", {"1": 2})
    validate_query_response("common/active_robot_publish", {"1": 1})
