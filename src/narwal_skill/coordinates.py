"""Map coordinate policies.

grid: pixel = raw - origin + offset. This is the raw-origin placement that
lands on the dock in the earlier sytchi client, and it matches upstream
overlay_to_grid.

upstream: pixel = (raw_dm * 100 / resolution_mm) - origin + offset.
That is the PROTOCOL.md formula (value_dm * 10) / (resolution / 10) - origin.

raw: do not invent a pixel. The position is reported as not calibrated.
"""

from __future__ import annotations

import math
from typing import Any

CALIBRATION_WARNING = (
    "Marker placement is not live-calibrated on this install. "
    "Flow 2 hardware acceptance is still pending. "
    "Select --coordinate-policy grid, upstream, or raw, and optional grid offsets, "
    "and treat any drawn marker as provisional."
)

POLICY_FORMULAS = {
    "grid": "pixel = raw - origin + offset",
    "upstream": "pixel = (raw * 100 / resolution_mm_per_cell) - origin + offset",
    "raw": "no pixel conversion; position reported as not_calibrated",
}


def to_pixel(
    raw: float,
    origin: int,
    resolution: int,
    policy: str,
    offset: float = 0.0,
) -> float | None:
    if policy == "raw":
        return None
    if not math.isfinite(raw):
        return None
    if policy == "grid":
        return raw - origin + offset
    if policy == "upstream":
        if resolution <= 0:
            return None
        return (raw * 100.0 / resolution) - origin + offset
    raise ValueError(f"unknown coordinate policy {policy!r}")


def in_range(pixel: float | None, size: int) -> bool:
    if pixel is None or size <= 0 or not math.isfinite(pixel):
        return False
    return 0.0 <= pixel < size


def place_point(
    *,
    raw_x: float | None,
    raw_y: float | None,
    origin_x: int,
    origin_y: int,
    resolution: int,
    width: int,
    height: int,
    policy: str,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    lost_context: bool = False,
) -> dict[str, Any]:
    """Return placement metadata. Out-of-range points are not given drawable pixels."""
    result: dict[str, Any] = {
        "raw_x": raw_x,
        "raw_y": raw_y,
        "policy": policy,
        "formula": POLICY_FORMULAS.get(policy, "unknown"),
        "origin_x": origin_x,
        "origin_y": origin_y,
        "offset_x": offset_x,
        "offset_y": offset_y,
        "resolution_mm_per_cell": resolution,
        "units": {
            "raw": "protocol_float",
            "origin": "grid_cells",
            "resolution": "mm_per_cell",
            "pixel": "grid_cells" if policy != "raw" else None,
        },
        "calibration": "not_calibrated",
        "pixel_x": None,
        "pixel_y": None,
        "in_range": False,
        "drawn": False,
        "lost_context": lost_context,
    }
    if lost_context or raw_x is None or raw_y is None:
        return result
    px = to_pixel(raw_x, origin_x, resolution, policy, offset_x)
    py = to_pixel(raw_y, origin_y, resolution, policy, offset_y)
    if policy == "raw":
        return result
    inside = in_range(px, width) and in_range(py, height)
    result["in_range"] = inside
    if inside:
        result["pixel_x"] = px
        result["pixel_y"] = py
        result["drawn"] = True
    return result
