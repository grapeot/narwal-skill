"""Control commands against the loopback robot. One JSON document on stdout."""

from __future__ import annotations

import json
from pathlib import Path

from narwal_skill.cli import main
from tests.loopback.frames import (
    base_status_payload,
    field5,
    field_bytes,
    field_varint,
    identity_payload,
    toy_map_payload,
)
from tests.loopback.test_cli import (  # noqa: F401 - reuse the shared harness
    DEVICE,
    PRODUCT,
    _args,
    _robot,
    _run,
    _serve,
)


def _control_robot(result_code: int, *, echo: bool = False) -> object:
    async def handler(ws, raw, short, conn):
        if short == "common/get_device_info":
            await ws.send(field5(identity_payload(DEVICE, PRODUCT)))
        elif short == "status/get_device_base_status":
            await ws.send(field5(base_status_payload(battery=80.0, mode=1)))
        elif short == "map/get_map":
            await ws.send(field5(toy_map_payload()))
        elif short == "status/app_status_heartbeat":
            return
        elif short in {"task/pause", "task/resume", "task/force_end", "supply/recall", "clean/start_clean"}:
            if echo:
                await ws.send(field5(field_bytes(1, field_varint(1, 100))))
            else:
                await ws.send(field5(field_varint(1, result_code)))

    return _serve(handler)


def _control_args(port: int, *extra: str) -> list[str]:
    return [
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--product-key",
        PRODUCT,
        "--device-id",
        DEVICE,
        "--query-timeout",
        "2",
        "--budget",
        "15",
        *extra,
    ]


def test_pause_success_exit_zero_and_audit(tmp_path: Path) -> None:
    robot = _control_robot(1)
    try:
        code, body, err = _run(
            ["pause", *_control_args(robot.port, "--yes", "--out-dir", str(tmp_path))]
        )
    finally:
        robot.stop()
    assert code == 0, err
    assert body["status"] == "ok"
    assert body["data"]["action"] == "pause"
    assert body["data"]["topic"] == "task/pause"
    assert body["data"]["result"]["accepted"] is True
    assert "task/pause" in robot.received
    audit = Path(body["artifacts"][0]["path"])
    assert audit.name == "control_audit.jsonl"
    record = json.loads(audit.read_text().strip())
    assert record["action"] == "pause"
    assert record["outcome"] == "accepted"


def test_declined_command_exits_20(tmp_path: Path) -> None:
    robot = _control_robot(4)
    try:
        code, body, err = _run(
            ["start", *_control_args(robot.port, "--yes", "--out-dir", str(tmp_path))]
        )
    finally:
        robot.stop()
    assert code == 20, err
    assert body["status"] == "failed"
    assert body["data"]["result"]["code"] == 4
    assert "clean/start_clean" in robot.received
    assert any(item["code"] == "command_not_applied" for item in body["errors"])


def test_conflict_code_three_is_not_applied(tmp_path: Path) -> None:
    robot = _control_robot(3)
    try:
        code, body, err = _run(
            ["resume", *_control_args(robot.port, "--yes", "--out-dir", str(tmp_path))]
        )
    finally:
        robot.stop()
    assert code == 20, err
    assert body["data"]["result"]["code"] == 3


def test_missing_yes_is_usage_and_sends_nothing(tmp_path: Path) -> None:
    robot = _control_robot(1)
    try:
        code, body, err = _run(
            ["pause", *_control_args(robot.port, "--out-dir", str(tmp_path))]
        )
    finally:
        robot.stop()
    assert code == 2, err
    assert body["errors"][0]["code"] == "confirmation_required"
    assert "task/pause" not in robot.received
    assert list(tmp_path.glob("*.jsonl")) == []


def test_dry_run_builds_without_sending_a_control_topic(tmp_path: Path) -> None:
    robot = _control_robot(1)
    try:
        code, body, err = _run(
            [
                "clean",
                *_control_args(robot.port, "--rooms", "1", "--dry-run", "--out-dir", str(tmp_path)),
            ]
        )
    finally:
        robot.stop()
    assert code == 0, err
    assert body["data"]["dry_run"] is True
    assert body["data"]["sent"] is False
    assert body["data"]["payload_hex"]
    assert body["data"]["target_rooms"] == [1]
    # Dry run reads identity/status/map to build the payload but sends no control topic.
    assert "clean/start_clean" not in robot.received
    assert "common/get_device_info" in robot.received
    assert "map/get_map" in robot.received
    assert list(tmp_path.glob("*.jsonl")) == []


def test_start_whole_house_uses_all_map_rooms(tmp_path: Path) -> None:
    robot = _control_robot(1)
    try:
        code, body, err = _run(
            ["start", *_control_args(robot.port, "--yes", "--out-dir", str(tmp_path))]
        )
    finally:
        robot.stop()
    assert code == 0, err
    assert body["data"]["target_rooms"] == [1]
    assert "clean/start_clean" in robot.received


def test_clean_unknown_room_is_usage(tmp_path: Path) -> None:
    robot = _control_robot(1)
    try:
        code, body, err = _run(
            [
                "clean",
                *_control_args(robot.port, "--rooms", "99", "--yes", "--out-dir", str(tmp_path)),
            ]
        )
    finally:
        robot.stop()
    assert code == 2, err
    assert body["errors"][0]["code"] == "usage"
    assert "clean/start_clean" not in robot.received


def test_dock_recall_topic(tmp_path: Path) -> None:
    robot = _control_robot(1)
    try:
        code, body, err = _run(
            ["dock", *_control_args(robot.port, "--yes", "--out-dir", str(tmp_path))]
        )
    finally:
        robot.stop()
    assert code == 0, err
    assert body["data"]["topic"] == "supply/recall"
    assert "supply/recall" in robot.received


def test_command_echo_dict_is_success(tmp_path: Path) -> None:
    robot = _control_robot(1, echo=True)
    try:
        code, body, err = _run(
            ["start", *_control_args(robot.port, "--yes", "--out-dir", str(tmp_path))]
        )
    finally:
        robot.stop()
    assert code == 0, err
    assert body["data"]["result"]["accepted"] is True
    assert body["data"]["result"]["echo"] is True


def test_bad_passes_is_usage() -> None:
    code = main(["start", "--passes", "9", "--host", "127.0.0.1", "--product-key", PRODUCT])
    assert code == 2


def test_audit_write_failure_preserves_command_exit_code(tmp_path: Path) -> None:
    robot = _control_robot(3)  # declined -> exit 20
    blocking = tmp_path / "not_a_dir"
    blocking.write_text("x")
    try:
        code, body, err = _run(
            ["resume", *_control_args(robot.port, "--yes", "--out-dir", str(blocking))]
        )
    finally:
        robot.stop()
    # A failed audit write must not rewrite the declined exit code to 3.
    assert code == 20, err
    assert body["status"] == "failed"
    assert body["data"]["result"]["code"] == 3
    assert any(item["code"] == "command_not_applied" for item in body["errors"])
    assert any(item["code"] == "audit_write_failed" for item in body["warnings"])


def test_audit_write_failure_preserves_success(tmp_path: Path) -> None:
    robot = _control_robot(1)
    blocking = tmp_path / "not_a_dir"
    blocking.write_text("x")
    try:
        code, body, err = _run(
            ["pause", *_control_args(robot.port, "--yes", "--out-dir", str(blocking))]
        )
    finally:
        robot.stop()
    assert code == 0, err
    assert body["status"] == "ok"
    assert any(item["code"] == "audit_write_failed" for item in body["warnings"])


def test_stop_timeout_exits_eleven(tmp_path: Path) -> None:
    async def handler(ws, raw, short, conn):
        if short == "common/get_device_info":
            await ws.send(field5(identity_payload(DEVICE, PRODUCT)))
        elif short == "status/get_device_base_status":
            await ws.send(field5(base_status_payload(battery=80.0, mode=1)))
        elif short == "task/force_end":
            return  # never answers

    robot = _serve(handler)
    try:
        code, body, err = _run(
            [
                "stop",
                "--host",
                "127.0.0.1",
                "--port",
                str(robot.port),
                "--product-key",
                PRODUCT,
                "--device-id",
                DEVICE,
                "--query-timeout",
                "1",
                "--budget",
                "3",
                "--yes",
                "--out-dir",
                str(tmp_path),
            ]
        )
    finally:
        robot.stop()
    assert code == 11, err
    assert body["status"] == "failed"


def test_unknown_result_code_closes_socket_and_exits_twelve(tmp_path: Path) -> None:
    robot = _control_robot(99)
    try:
        code, body, err = _run(
            ["pause", *_control_args(robot.port, "--yes", "--out-dir", str(tmp_path))]
        )
    finally:
        robot.stop()
    assert code == 12, err
    assert body["status"] == "failed"
    assert any(item["code"] == "decode" for item in body["errors"])
