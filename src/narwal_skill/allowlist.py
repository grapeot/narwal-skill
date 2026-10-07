"""Hard send allowlist. The transport refuses every other topic."""

from __future__ import annotations

from narwal_skill.errors import ReadOnlyViolation

# Short topics this product may put on the wire. Subscription and heartbeat
# are bounded telemetry, not actuation.
ALLOWED_SEND_TOPICS = frozenset(
    {
        "common/get_device_info",
        "status/get_device_base_status",
        "map/get_map",
        "common/get_feature_list",
        "common/active_robot_publish",
        "status/app_status_heartbeat",
    }
)

# Topics that must never be sent, even if a caller names them.
FORBIDDEN_SEND_TOPICS = frozenset(
    {
        "clean/start_clean",
        "clean/plan/start",
        "clean/easy_clean/start",
        "clean/set_fan_level",
        "clean/set_mop_humidity",
        "task/pause",
        "task/resume",
        "task/force_end",
        "task/cancel",
        "supply/recall",
        "supply/wash_mop",
        "supply/wash_mop_by_robot_status",
        "supply/dry_mop",
        "supply/dry_dust_bag",
        "supply/dry_station_bag",
        "supply/dust_gathering",
        "supply/ambient_light_ctrl",
        "common/yell",
        "common/reboot",
        "common/shutdown",
        "common/notify_app_event",
        "developer/take_picture",
        "developer/led_control",
        "developer/get_robot_debug_image",
        "developer/ping",
    }
)

# Broadcast topics we ask the robot to publish. Receiving other topics is allowed.
SUBSCRIBE_TOPICS = (
    "status/robot_base_status",
    "status/working_status",
    "map/display_map",
    "upgrade/upgrade_status",
    "status/download_status",
    "status/time_line_status",
)

UNACKNOWLEDGED_SEND_TOPICS = frozenset({"status/app_status_heartbeat"})


def require_send_topic(short_topic: str) -> str:
    """Reject any topic outside the read-only allowlist."""
    if short_topic in FORBIDDEN_SEND_TOPICS or short_topic not in ALLOWED_SEND_TOPICS:
        raise ReadOnlyViolation(
            f"refusing to send topic {short_topic!r}; not in the read-only allowlist",
            detail=short_topic,
        )
    return short_topic
