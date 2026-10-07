"""Map metadata and PNG export. Does not guess household room names."""

from __future__ import annotations

import json
import math
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from narwal_skill.coordinates import CALIBRATION_WARNING, place_point
from narwal_skill.errors import ArtifactError, DecodeError, QueryRejected
from narwal_skill.telemetry import to_float32
from narwal_skill.vendor.narwal_client.map_renderer import decompress_map, render_map_png
from narwal_skill.vendor.narwal_client.models import MapData

ROOM_TYPE_ENUM = {
    0: "room",
    1: "master_bedroom",
    2: "secondary_bedroom",
    3: "living_room",
    4: "kitchen",
    5: "bathroom",
    6: "toilet",
    7: "balcony",
    8: "dining_room",
    9: "closet",
    10: "corridor",
    11: "study",
    12: "kids_room",
    13: "entertainment_room",
    14: "storage_room",
    15: "others",
}


def unique_artifact(directory: Path, stem: str, suffix: str) -> Path:
    try:
        if directory.exists() and not directory.is_dir():
            raise ArtifactError(
                f"artifact directory is not a directory: {directory}",
                detail=f"path exists and is not a directory: {directory}",
            )
        directory.mkdir(parents=True, exist_ok=True)
    except ArtifactError:
        raise
    except OSError as exc:
        raise ArtifactError(
            f"cannot create artifact directory: {directory}",
            detail=f"{type(exc).__name__}: {exc}",
            cause=exc,
        ) from exc
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for _ in range(8):
        path = directory / f"{stem}_{stamp}_{secrets.token_hex(4)}{suffix}"
        if not path.exists():
            return path
    raise ArtifactError("could not allocate a unique artifact path", detail=str(directory))


def parse_map_response(decoded: dict[str, Any]) -> MapData:
    from narwal_skill.telemetry import is_rejected_result

    code = is_rejected_result(decoded)
    if code is not None:
        raise QueryRejected(
            "get_map was rejected and is not map data",
            detail=f"result_code={code}",
        )
    payload = decoded.get("2")
    if not isinstance(payload, dict):
        raise DecodeError(
            "get_map payload is not a map message",
            detail=f"field 2 type is {type(payload).__name__}",
        )
    try:
        map_data = MapData.from_response(decoded)
    except Exception as exc:
        raise DecodeError("get_map decode failed", detail=f"{type(exc).__name__}: {exc}", cause=exc) from exc
    if not map_data.map_id and not map_data.compressed_map and map_data.width <= 0:
        raise DecodeError(
            "get_map did not contain an active map",
            detail="missing map id, grid, and compressed map",
        )
    return map_data


def _present_int(raw: dict[str, Any], key: str) -> int | None:
    if key not in raw:
        return None
    try:
        return int(raw[key])
    except (TypeError, ValueError):
        return None


def room_records(map_data: MapData) -> list[dict[str, Any]]:
    records = []
    for room in map_data.rooms:
        name = room.name.strip() if isinstance(room.name, str) else ""
        if name in {"{}", "b''", 'b""'}:
            name = ""
        records.append(
            {
                "room_id": room.room_id,
                "name": name or None,
                "label": name or f"room-{room.room_id}",
                "room_type_code": room.room_sub_type,
                "room_type_enum": ROOM_TYPE_ENUM.get(room.room_sub_type),
            }
        )
    return records


def raw_xy(container: Any) -> tuple[float | None, float | None]:
    if not isinstance(container, dict):
        return None, None
    pos = container.get("1")
    if not isinstance(pos, dict):
        return None, None
    if "1" not in pos or "2" not in pos:
        return None, None
    return to_float32(pos.get("1")), to_float32(pos.get("2"))


def dock_raw(map_data: MapData) -> tuple[float | None, float | None]:
    field8 = map_data.raw.get("8") if isinstance(map_data.raw, dict) else None
    return raw_xy(field8)


def position_from_display(decoded: dict[str, Any] | None) -> dict[str, Any] | None:
    if not decoded:
        return None
    field1 = decoded.get("1")
    raw_x, raw_y = raw_xy(field1 if isinstance(field1, dict) else None)
    if raw_x is None or raw_y is None:
        return None
    heading = None
    if isinstance(field1, dict) and "2" in field1:
        heading = to_float32(field1.get("2"))
    timestamp = decoded.get("10")
    try:
        timestamp_i = int(timestamp) if timestamp is not None else None
    except (TypeError, ValueError):
        timestamp_i = None
    lost = raw_x == 0.0 and raw_y == 0.0 and (timestamp_i in (None, 0))
    return {
        "raw_x": _finite(raw_x),
        "raw_y": _finite(raw_y),
        "heading_rad": _finite(heading),
        "device_timestamp_ms": timestamp_i,
        "lost_context": lost,
    }


def _finite(value: float | None) -> float | None:
    if value is None or not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def canonical_position(decoded: dict[str, Any] | None) -> dict[str, Any]:
    """Raw display_map position. Never claimed calibrated, and never NaN."""
    parsed = position_from_display(decoded)
    position = {
        "raw_x": None,
        "raw_y": None,
        "heading_rad": None,
        "device_timestamp_ms": None,
        "lost_context": None,
        "calibration": "not_calibrated",
        "units": "protocol_float",
    }
    if parsed:
        position.update(parsed)
        position["calibration"] = "not_calibrated"
        position["units"] = "protocol_float"
    return position


def basic_trajectory(decoded: dict[str, Any] | None) -> dict[str, Any]:
    """Point count and raw points only. Not a joined route and not calibrated."""
    empty = {"count": 0, "points": [], "calibration": "not_calibrated"}
    if not decoded or not isinstance(decoded.get("2"), dict):
        return empty
    try:
        from narwal_skill.vendor.narwal_client.models import MapDisplayData

        points = MapDisplayData.from_broadcast(decoded).trajectory_points()
    except Exception as exc:
        return {
            "count": None,
            "points": None,
            "calibration": "not_calibrated",
            "decode_error": f"{type(exc).__name__}: {exc}",
        }
    finite = [
        [_finite(x), _finite(y)]
        for x, y in points
        if _finite(x) is not None and _finite(y) is not None
    ]
    limited = finite[:64]
    return {
        "count": len(finite),
        "points": limited,
        "truncated": len(finite) > 64,
        "calibration": "not_calibrated",
    }


def map_metadata(
    map_data: MapData,
    *,
    policy: str,
    offset_x: float,
    offset_y: float,
    render_scale: int,
    position: dict[str, Any] | None,
    position_observed_at: str | None,
) -> dict[str, Any]:
    raw = map_data.raw if isinstance(map_data.raw, dict) else {}
    width = _present_int(raw, "4")
    height = _present_int(raw, "5")
    resolution = _present_int(raw, "3")
    origin_x = map_data.origin_x if isinstance(raw.get("6"), dict) and "3" in raw["6"] else None
    origin_y = map_data.origin_y if isinstance(raw.get("6"), dict) and "1" in raw["6"] else None
    area = _present_int(raw, "33")
    dock_x, dock_y = dock_raw(map_data)
    width_i = width or 0
    height_i = height or 0
    resolution_i = resolution or 0
    origin_x_i = 0 if origin_x is None else origin_x
    origin_y_i = 0 if origin_y is None else origin_y
    dock = place_point(
        raw_x=dock_x,
        raw_y=dock_y,
        origin_x=origin_x_i,
        origin_y=origin_y_i,
        resolution=resolution_i,
        width=width_i,
        height=height_i,
        policy=policy,
        offset_x=offset_x,
        offset_y=offset_y,
    )
    robot = None
    if position is not None:
        robot = place_point(
            raw_x=position.get("raw_x"),
            raw_y=position.get("raw_y"),
            origin_x=origin_x_i,
            origin_y=origin_y_i,
            resolution=resolution_i,
            width=width_i,
            height=height_i,
            policy=policy,
            offset_x=offset_x,
            offset_y=offset_y,
            lost_context=bool(position.get("lost_context")),
        )
        robot["heading_rad"] = position.get("heading_rad")
        robot["device_timestamp_ms"] = position.get("device_timestamp_ms")
        robot["observed_at"] = position_observed_at
    return {
        "map_id": map_data.map_id or None,
        "width": width,
        "height": height,
        "resolution_mm_per_cell": resolution,
        "resolution_unit": "mm_per_cell",
        "resolution_unit_source": "upstream_protocol_field_2_3_documented_as_mm_per_cell",
        "origin_x": origin_x,
        "origin_y": origin_y,
        "render_scale": render_scale,
        "area_raw": area,
        "area_unit": "unknown",
        "rooms": room_records(map_data),
        "coordinate_policy": policy,
        "calibration": "not_calibrated",
        "calibration_warning": CALIBRATION_WARNING,
        "dock": dock,
        "robot": robot,
    }


def render_png(map_data: MapData, meta: dict[str, Any]) -> bytes:
    if not map_data.compressed_map or not meta.get("width") or not meta.get("height"):
        raise DecodeError(
            "map grid is incomplete",
            detail="compressed map, width, or height is absent",
        )
    decompressed = decompress_map(map_data.compressed_map)
    if not decompressed:
        raise DecodeError("map grid did not decompress", detail="empty decompressed grid")
    names = {room["room_id"]: room["label"] for room in meta["rooms"]}
    robot = meta.get("robot") or {}
    dock = meta.get("dock") or {}
    robot_x = robot.get("pixel_x") if robot.get("drawn") else None
    robot_y = robot.get("pixel_y") if robot.get("drawn") else None
    dock_x = dock.get("pixel_x") if dock.get("drawn") else None
    dock_y = dock.get("pixel_y") if dock.get("drawn") else None
    import narwal_skill.vendor.narwal_client.map_renderer as renderer

    previous = renderer.MAP_RENDER_SCALE
    renderer.MAP_RENDER_SCALE = int(meta["render_scale"])
    try:
        png = render_map_png(
            decompressed,
            int(meta["width"]),
            int(meta["height"]),
            robot_x,
            robot_y,
            None,
            dock_x,
            dock_y,
            names,
        )
    finally:
        renderer.MAP_RENDER_SCALE = previous
    if not png:
        raise ArtifactError("map renderer returned an empty PNG", detail="render_map_png returned no bytes")
    return png


def write_map_artifacts(
    directory: Path,
    map_data: MapData,
    meta: dict[str, Any],
) -> list[dict[str, str]]:
    png_path = unique_artifact(directory, "map", ".png")
    json_path = png_path.with_suffix(".json")
    if json_path.exists():
        json_path = unique_artifact(directory, "map_meta", ".json")
    try:
        png = render_png(map_data, meta)
        png_path.write_bytes(png)
        json_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    except ArtifactError:
        raise
    except OSError as exc:
        raise ArtifactError(
            "failed to write map artifacts",
            detail=f"{type(exc).__name__}: {exc}",
            cause=exc,
        ) from exc
    return [
        {"type": "map_png", "path": str(png_path.resolve())},
        {"type": "map_meta", "path": str(json_path.resolve())},
    ]
