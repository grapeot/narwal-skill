"""Offline normalization, coordinates, and artifact failures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from narwal_skill.codec import decode_payload
from narwal_skill.coordinates import place_point
from narwal_skill.errors import ArtifactError
from narwal_skill.map_export import map_metadata, parse_map_response, write_map_artifacts
from narwal_skill.telemetry import normalize_base, normalize_progress, normalize_working
from tests.loopback.frames import toy_map_payload


def test_progress_does_not_scale_fraction_and_keeps_flow2_percent() -> None:
    flow = normalize_progress(3.0, "QxMSPG6VSO")
    assert flow["progress_percent"] == 3.0
    assert flow["normalization"] == "flow2_reported_percent"
    small = normalize_progress(0.5, "QxMSPG6VSO")
    assert small["progress_percent"] is None
    assert small["ambiguous"] is True
    other = normalize_progress(92.0, "QoEsI5qYXO")
    assert other["progress_percent"] is None
    assert other["raw"] == 92.0
    absent = normalize_progress(None, "QxMSPG6VSO")
    assert absent["progress_percent"] is None
    assert absent["normalization"] == "absent"


def test_nested_error_code_and_empty_object_are_not_fake_faults() -> None:
    from narwal_skill.telemetry import fault_view, normalize_base

    single = fault_view({"1": {"1": 300}})
    assert single["fault_codes"] == [300]
    assert single["health"] == "faults_reported"
    assert single["healthy"] is False
    listed = fault_view({"1": [{"1": 12}, {"1": 34}]})
    assert listed["fault_codes"] == [12, 34]
    assert listed["fault_raw"] == [{"1": 12}, {"1": 34}]
    empty = fault_view({"1": {}})
    assert empty["fault_codes"] == []
    assert empty["health"] == "unknown"
    assert empty["healthy"] is False
    assert empty["fault_raw"] == {}
    assert empty["health"] != "faults_reported"
    base = normalize_base({"1": {"1": 7}}, source="status/robot_base_status", observed_at="t")
    assert base["fault_codes"] == [7]
    assert base["fault_raw"] == {"1": 7}


def test_packed_varint_fault_code_is_not_split_into_bytes() -> None:
    from narwal_skill.telemetry import fault_view

    large = bytes([0xAC, 0x02])
    parsed = fault_view({"1": large})
    assert parsed["fault_codes"] == [300]
    assert parsed["health"] == "faults_reported"
    as_text = fault_view({"1": large.decode("latin-1")})
    assert as_text["fault_codes"] == [300]
    truncated = fault_view({"1": bytes([0x80])})
    assert truncated["fault_codes"] is None
    assert truncated["health"] == "unknown"
    assert truncated["fault_decode_error"]
    assert "faults_reported" != truncated["health"]


def test_nonfinite_telemetry_is_null_and_stdout_rejects_nan() -> None:
    from narwal_skill.envelope import Envelope
    from narwal_skill.telemetry import normalize_base, to_float32

    assert to_float32(float("nan")) is None
    assert to_float32(float("inf")) is None
    base = normalize_base({"2": float("nan")}, source="status/get_device_base_status", observed_at="t")
    assert base["battery_percent"] is None
    env = Envelope()
    env.data = {"battery_percent": float("nan"), "elapsed_seconds": 0.0, "area_m2": float("inf")}
    text = env.emit()
    assert "NaN" not in text
    assert "Infinity" not in text
    parsed = json.loads(text)
    assert parsed["data"]["elapsed_seconds"] == 0.0
    assert parsed["data"]["battery_percent"] is None
    assert parsed["data"]["area_m2"] is None


def test_field13_is_not_area_and_null_is_not_zero() -> None:
    task = normalize_working(
        {"13": 18000},
        product_key="QxMSPG6VSO",
        source="status/working_status",
        observed_at="2026-10-06T00:00:00Z",
    )
    assert task["area_m2"] is None
    assert task["station_bag_dry_total_seconds"] == 18000
    zero = normalize_working(
        {"2": 0.0, "3": 0},
        product_key="QxMSPG6VSO",
        source="status/working_status",
        observed_at="2026-10-06T00:00:00Z",
    )
    assert zero["area_m2"] == 0.0
    assert zero["elapsed_seconds"] == 0
    base = normalize_base({}, source="status/get_device_base_status", observed_at="t")
    assert base["battery_percent"] is None
    assert base["fault_codes"] is None
    assert base["healthy"] is None
    present_zero = normalize_base({"2": 0.0}, source="status/get_device_base_status", observed_at="t")
    assert present_zero["battery_percent"] == 0.0


def test_coordinate_policies_do_not_draw_out_of_range() -> None:
    grid = place_point(
        raw_x=2, raw_y=1, origin_x=0, origin_y=0, resolution=60,
        width=8, height=8, policy="grid",
    )
    assert grid["drawn"] is True
    assert grid["pixel_x"] == 2
    upstream = place_point(
        raw_x=2, raw_y=1, origin_x=0, origin_y=0, resolution=60,
        width=8, height=8, policy="upstream",
    )
    assert upstream["pixel_x"] == pytest.approx(2 * 100 / 60)
    far = place_point(
        raw_x=50, raw_y=1, origin_x=0, origin_y=0, resolution=60,
        width=8, height=8, policy="upstream",
    )
    assert far["drawn"] is False
    assert far["pixel_x"] is None
    raw = place_point(
        raw_x=2, raw_y=1, origin_x=0, origin_y=0, resolution=60,
        width=8, height=8, policy="raw",
    )
    assert raw["drawn"] is False
    assert raw["calibration"] == "not_calibrated"


def test_toy_map_png_and_artifact_error(tmp_path: Path) -> None:
    decoded = decode_payload(toy_map_payload())
    map_data = parse_map_response(decoded)
    meta = map_metadata(
        map_data,
        policy="grid",
        offset_x=0,
        offset_y=0,
        render_scale=2,
        position={"raw_x": 1.0, "raw_y": 1.0, "lost_context": False, "heading_rad": 0.0},
        position_observed_at="2026-10-06T00:00:00Z",
    )
    artifacts = write_map_artifacts(tmp_path, map_data, meta)
    png = Path(artifacts[0]["path"])
    from PIL import Image

    with Image.open(png) as image:
        assert image.format == "PNG"
        assert image.size == (8, 8)
    sidecar = json.loads(Path(artifacts[1]["path"]).read_text())
    assert sidecar["rooms"][0]["label"] == "room-1"
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    with pytest.raises(ArtifactError):
        write_map_artifacts(blocker, map_data, meta)
