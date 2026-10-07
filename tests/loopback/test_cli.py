"""CLI against the loopback robot. stdout is one JSON document."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

import pytest

from narwal_skill.allowlist import ALLOWED_SEND_TOPICS, FORBIDDEN_SEND_TOPICS
from narwal_skill.cli import main
from tests.loopback.fake_robot import FakeRobot, push_later
from tests.loopback.frames import (
    base_status_payload,
    display_map_payload,
    field4,
    field5,
    identity_payload,
    toy_map_payload,
    working_payload,
)

PRODUCT = "QxMSPG6VSO"
DEVICE = "fake-device-001"


def _run(argv: list[str]) -> tuple[int, dict, str]:
    import io
    from contextlib import redirect_stderr, redirect_stdout

    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    text = out.getvalue()
    parsed = json.loads(text) if text.strip() else {}
    return code, parsed, err.getvalue()


class RunningRobot:
    def __init__(self, robot: FakeRobot, loop: asyncio.AbstractEventLoop, thread: threading.Thread) -> None:
        self.robot = robot
        self.loop = loop
        self.thread = thread

    @property
    def port(self) -> int:
        return self.robot.port

    @property
    def received(self) -> list[str]:
        return self.robot.received

    def stop(self) -> None:
        future = asyncio.run_coroutine_threadsafe(self.robot.stop(), self.loop)
        future.result(timeout=3)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)


def _serve(handler) -> RunningRobot:
    loop = asyncio.new_event_loop()
    robot = FakeRobot(handler)
    ready = threading.Event()

    def run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(robot.start())
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    if not ready.wait(timeout=3):
        raise RuntimeError("fake robot did not start")
    return RunningRobot(robot, loop, thread)


def _robot(battery: float | None = 83.0, mode: int = 5, omit_area: bool = False) -> RunningRobot:
    cron = '{"cron": "0 8 * * 1", "room_id": 3}'
    working = working_payload(
        progress=92.0,
        area=None if omit_area else 1.25,
        elapsed=0 if omit_area else 40,
        station_bag=18000,
        cron=cron,
    )
    topic = f"/{PRODUCT}/{DEVICE}/status/working_status"

    async def handler(ws, raw, short, conn):
        if short == "common/get_device_info":
            await ws.send(field5(identity_payload(DEVICE, PRODUCT)))
        elif short == "common/active_robot_publish":
            await ws.send(field4(topic, working))
            await ws.send(
                field4(f"/{PRODUCT}/{DEVICE}/map/display_map", display_map_payload(1.0, 1.0))
            )
            await ws.send(field5(b"\x08\x01"))
        elif short == "status/get_device_base_status":
            await ws.send(field4(f"/{PRODUCT}/{DEVICE}/map/display_map", display_map_payload(1.0, 1.0)))
            await ws.send(field5(base_status_payload(battery=battery, mode=mode)))
        elif short == "map/get_map":
            await ws.send(field5(toy_map_payload()))
        elif short == "status/app_status_heartbeat":
            return
        elif short == "common/get_feature_list":
            await ws.send(field5(b"\x08\x01"))

    return _serve(handler)


def _args(port: int, *extra: str) -> list[str]:
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


def test_snapshot_telemetry_contract(tmp_path: Path) -> None:
    robot = _robot()
    try:
        code, body, err = _run(
            ["snapshot", *_args(robot.port), "--listen-seconds", "0.3", "--out-dir", str(tmp_path)]
        )
    finally:
        robot.stop()
    assert code == 0, err
    assert body["schema_version"] == "1.0"
    assert body["status"] == "ok"
    assert body["device"]["device_id"] == DEVICE
    assert body["device"]["model"] == "Narwal Flow 2"
    assert body["data"]["base_query"]["battery_percent"] == pytest.approx(83.0)
    assert body["data"]["base_query"]["fault_codes"] is None
    assert body["data"]["base_query"]["healthy"] is None
    assert body["data"]["base_query"]["mode"]["code"] == 5
    assert body["data"]["base_query"]["mode"]["semantic"] == "cleaning_alt"
    assert body["data"]["base_query"]["mode"]["stuck_inferred"] is False
    assert body["data"]["base_query"]["mode"]["substage"] is None
    task = body["data"]["task"]
    assert task["area_m2"] == pytest.approx(1.25)
    assert task["station_bag_dry_total_seconds"] == 18000
    assert task["area_m2"] != 18000
    assert task["progress"]["progress_percent"] == pytest.approx(92.0)
    assert task["progress"]["normalization"] == "flow2_reported_percent"
    assert task["schedule_fragment"]["complete"] is False
    assert task["schedule_fragment"]["fragment"]["cron"] == "0 8 * * 1"
    assert body["data"]["base_query"]["source"] == "status/get_device_base_status"
    assert task["source"] == "status/working_status"
    assert body["data"]["base_query"]["observed_at"]
    assert task["observed_at"]
    assert "Traceback" not in err
    assert set(robot.received) <= ALLOWED_SEND_TOPICS
    assert set(robot.received).isdisjoint(FORBIDDEN_SEND_TOPICS)


def test_missing_battery_is_null_and_zero_elapsed_stays_zero() -> None:
    robot = _robot(battery=None, omit_area=True)
    try:
        code, body, err = _run(["snapshot", *_args(robot.port), "--listen-seconds", "0.2"])
    finally:
        robot.stop()
    assert code == 0, err
    assert body["data"]["base_query"]["battery_percent"] is None
    assert body["data"]["task"]["area_m2"] is None
    assert body["data"]["task"]["elapsed_seconds"] == 0
    assert body["data"]["task"]["station_bag_dry_total_seconds"] == 18000


def test_unknown_mode_and_other_model_progress() -> None:
    async def handler(ws, raw, short, conn):
        if short == "common/get_device_info":
            await ws.send(field5(identity_payload(DEVICE, "QoEsI5qYXO")))
        elif short == "common/active_robot_publish":
            await ws.send(
                field4(
                    f"/QoEsI5qYXO/{DEVICE}/status/working_status",
                    working_payload(progress=92.0),
                )
            )
            await ws.send(field5(b"\x08\x01"))
        elif short == "status/get_device_base_status":
            await ws.send(field5(base_status_payload(battery=50.0, mode=42)))
        elif short == "status/app_status_heartbeat":
            return

    robot = _serve(handler)
    try:
        code, body, err = _run(
            [
                "snapshot",
                "--host",
                "127.0.0.1",
                "--port",
                str(robot.port),
                "--product-key",
                "QoEsI5qYXO",
                "--device-id",
                DEVICE,
                "--query-timeout",
                "2",
                "--budget",
                "15",
                "--listen-seconds",
                "0.2",
            ]
        )
    finally:
        robot.stop()
    assert code == 0, err
    assert body["data"]["base_query"]["mode"]["semantic"] == "unknown"
    assert body["data"]["base_query"]["mode"]["code"] == 42
    assert body["data"]["task"]["progress"]["progress_percent"] is None
    assert body["data"]["task"]["progress"]["ambiguous"] is True
    assert body["data"]["task"]["progress"]["raw"] == pytest.approx(92.0)


def test_map_png_and_coordinate_policy(tmp_path: Path) -> None:
    robot = _robot()
    try:
        code, body, err = _run(
            [
                "map",
                *_args(robot.port),
                "--out-dir",
                str(tmp_path),
                "--listen-seconds",
                "0.2",
                "--coordinate-policy",
                "grid",
                "--render-scale",
                "2",
            ]
        )
    finally:
        robot.stop()
    assert code == 0, err
    meta = body["data"]["map"]
    assert meta["resolution_mm_per_cell"] == 60
    assert meta["area_unit"] == "unknown"
    assert meta["area_raw"] == 12
    assert meta["render_scale"] == 2
    assert meta["calibration"] == "not_calibrated"
    assert meta["rooms"][0]["label"] == "room-1"
    assert meta["rooms"][0]["name"] is None
    assert meta["robot"]["drawn"] is True
    png = Path(body["artifacts"][0]["path"])
    from PIL import Image

    with Image.open(png) as image:
        assert image.format == "PNG"
        assert image.size == (8, 8)


def test_watch_writes_jsonl_and_one_stdout_object(tmp_path: Path) -> None:
    async def handler(ws, raw, short, conn):
        if short == "common/get_device_info":
            await ws.send(field5(identity_payload()))
        elif short == "common/active_robot_publish":
            await ws.send(field5(b"\x08\x01"))
            await push_later(
                ws,
                field4(f"/{PRODUCT}/{DEVICE}/status/working_status", working_payload(progress=3.0)),
                0.05,
            )
        elif short == "status/app_status_heartbeat":
            return

    robot = _serve(handler)
    try:
        code, body, err = _run(
            ["watch", *_args(robot.port), "--duration", "0.35", "--out-dir", str(tmp_path)]
        )
    finally:
        robot.stop()
    assert code == 0, err
    assert body["data"]["event_count"] >= 1
    assert len(body["artifacts"]) == 1
    lines = Path(body["artifacts"][0]["path"]).read_text().strip().splitlines()
    assert lines
    assert json.loads(lines[0])["topic"] == "status/working_status"
    assert "\n{" not in json.dumps(body) or body["schema_version"] == "1.0"


def test_watch_raw_warning_and_disconnect_is_partial(tmp_path: Path) -> None:
    async def handler(ws, raw, short, conn):
        if short == "common/get_device_info":
            await ws.send(field5(identity_payload()))
        elif short == "common/active_robot_publish":
            await ws.send(field5(b"\x08\x01"))
            await ws.close(code=1000, reason="connection with same ip, close old one")

    robot = _serve(handler)
    try:
        code, body, err = _run(
            ["watch", *_args(robot.port), "--duration", "2", "--out-dir", str(tmp_path), "--raw"]
        )
    finally:
        robot.stop()
    assert code == 3, err
    assert body["status"] == "partial"
    assert any(item["code"] == "raw_capture_private" for item in body["warnings"])
    assert any("connection with same ip, close old one" in item["detail"] for item in body["errors"])
    assert "Traceback" not in err


def test_doctor_separates_transport_from_application_success() -> None:
    robot = _robot()
    try:
        code, body, err = _run(["doctor", *_args(robot.port)])
    finally:
        robot.stop()
    assert code == 0, err
    names = [item["name"] for item in body["data"]["checks"]]
    assert names == ["tcp", "websocket", "identity", "query"]
    assert body["data"]["application_ok"] is True
    assert body["data"]["websocket_ping_treated_as_application_success"] is False
    assert body["data"]["map_queried"] is False
    assert "map/get_map" not in robot.received
    closed, closed_body, closed_err = _run(
        [
            "doctor",
            "--host",
            "127.0.0.1",
            "--port",
            "9",
            "--product-key",
            PRODUCT,
            "--query-timeout",
            "1",
            "--budget",
            "5",
        ]
    )
    assert closed == 10, closed_err
    assert closed_body["status"] == "failed"
    assert closed_body["data"]["checks"][0]["name"] == "tcp"
    assert closed_body["data"]["application_ok"] is False
    assert "Traceback" not in closed_err


def test_watch_jsonl_keeps_display_map_position(tmp_path: Path) -> None:
    topic = f"/{PRODUCT}/{DEVICE}/map/display_map"

    async def handler(ws, raw, short, conn):
        if short == "common/get_device_info":
            await ws.send(field5(identity_payload()))
        elif short == "common/active_robot_publish":
            await ws.send(field5(b"\x08\x01"))
            await ws.send(field4(topic, display_map_payload(1.5, 2.5, 1_700_000_000_123, 0.4)))
        elif short == "status/app_status_heartbeat":
            return

    robot = _serve(handler)
    try:
        code, body, err = _run(
            ["watch", *_args(robot.port), "--duration", "0.3", "--out-dir", str(tmp_path)]
        )
    finally:
        robot.stop()
    assert code == 0, err
    lines = Path(body["artifacts"][0]["path"]).read_text().strip().splitlines()
    events = [json.loads(line) for line in lines if line]
    display = [item for item in events if item["topic"] == "map/display_map"]
    assert display
    position = display[0]["position"]
    assert position["raw_x"] == pytest.approx(1.5)
    assert position["raw_y"] == pytest.approx(2.5)
    assert position["heading_rad"] == pytest.approx(0.4)
    assert position["device_timestamp_ms"] == 1_700_000_000_123
    assert position["lost_context"] is False
    assert position["calibration"] == "not_calibrated"
    assert "trajectory" in display[0]
    assert display[0]["trajectory"]["calibration"] == "not_calibrated"


def test_unparsed_broadcasts_are_not_a_clean_ok(tmp_path: Path) -> None:
    async def handler(ws, raw, short, conn):
        if short == "common/get_device_info":
            await ws.send(field5(identity_payload()))
        elif short == "common/active_robot_publish":
            await ws.send(field5(b"\x08\x01"))
            await push_later(ws, b"\x01\x02\xff\x00", 0.05)

    robot = _serve(handler)
    try:
        code, body, err = _run(
            ["watch", *_args(robot.port), "--duration", "0.3", "--out-dir", str(tmp_path)]
        )
    finally:
        robot.stop()
    assert code == 3, err
    assert body["status"] == "partial"
    assert body["data"]["broadcast_frame_errors"] >= 1
    assert any(item["code"] == "broadcast_decode" for item in body["warnings"])


def test_map_still_writes_when_position_sampling_fails(tmp_path: Path) -> None:
    async def handler(ws, raw, short, conn):
        if short == "common/get_device_info":
            await ws.send(field5(identity_payload()))
        elif short == "map/get_map":
            await ws.send(field5(toy_map_payload()))

    robot = _serve(handler)
    try:
        code, body, err = _run(
            [
                "map",
                *_args(robot.port, "--query-timeout", "0.2"),
                "--listen-seconds",
                "0.2",
                "--out-dir",
                str(tmp_path),
            ]
        )
    finally:
        robot.stop()
    assert code == 3, err
    assert body["status"] == "partial"
    assert any(item["code"] == "position_sampling_failed" for item in body["warnings"])
    assert body["artifacts"]
    assert Path(body["artifacts"][0]["path"]).read_bytes().startswith(b"\x89PNG")


def test_discover_third_key_with_map_is_not_a_connection_cap(tmp_path: Path) -> None:
    async def handler(ws, raw, short, conn):
        if short == "common/get_device_info" and conn >= 3:
            await ws.send(field5(identity_payload(DEVICE, "mkbqaprvrb")))
        elif short == "common/active_robot_publish":
            await ws.send(field5(b"\x08\x01"))
        elif short == "status/get_device_base_status":
            await ws.send(field5(base_status_payload(battery=50.0, mode=5)))
        elif short == "map/get_map":
            await ws.send(field5(toy_map_payload()))

    robot = _serve(handler)
    try:
        code, body, err = _run(
            [
                "snapshot",
                "--host",
                "127.0.0.1",
                "--port",
                str(robot.port),
                "--discover",
                "--with-map",
                "--listen-seconds",
                "0",
                "--query-timeout",
                "0.25",
                "--budget",
                "8",
                "--out-dir",
                str(tmp_path),
            ]
        )
    finally:
        robot.stop()
    assert code in {0, 3}, err
    assert body["data"].get("map")
    assert body["artifacts"]
    assert "connection_cap" not in {item["code"] for item in body["errors"]}
    assert Path(body["artifacts"][0]["path"]).read_bytes().startswith(b"\x89PNG")


def test_watch_setup_failure_does_not_register_an_artifact(tmp_path: Path) -> None:
    code, body, err = _run(
        [
            "watch",
            "--host",
            "127.0.0.1",
            "--port",
            "9",
            "--product-key",
            PRODUCT,
            "--device-id",
            DEVICE,
            "--duration",
            "1",
            "--budget",
            "2",
            "--query-timeout",
            "0.3",
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert code == 10, err
    assert body["artifacts"] == []
    assert list(tmp_path.glob("*.jsonl")) == []


def test_host_missing_is_structured_usage() -> None:
    env = os.environ.copy()
    for key in ("NARWAL_HOST", "NARWAL_PRODUCT_KEY", "NARWAL_DEVICE_ID"):
        os.environ.pop(key, None)
    try:
        code, body, err = _run(["snapshot"])
    finally:
        os.environ.clear()
        os.environ.update(env)
    assert code == 2
    assert body["status"] == "failed"
    assert body["errors"][0]["code"] == "usage"
    assert "NARWAL_HOST" in body["errors"][0]["detail"]
    assert "Traceback" not in err


def test_bad_args_show_help_and_do_not_print_json(capsys) -> None:
    code = main(["watch", "--duration"])
    assert code == 2
    captured = capsys.readouterr()
    assert "duration" in captured.err.lower() or "usage" in captured.err.lower()
    assert captured.out == ""


def test_help_lists_connection_options(capsys) -> None:
    code = main(["snapshot", "--help"])
    assert code == 0
    text = capsys.readouterr().out
    assert "--host" in text
    assert "--product-key" in text
    assert "--device-id" in text
    assert "--listen-seconds" in text
