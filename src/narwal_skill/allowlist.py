"""Hard send allowlist. The transport refuses every other topic.

Two tiers. Read topics are always allowed. Control topics (pause / resume /
stop / dock / start / clean) require the caller to opt in explicitly, so a
default session cannot put an actuation topic on the wire even by accident.
"""

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

# Actuation topics. Sent only from a session opened with allow_control=True.
CONTROL_SEND_TOPICS = frozenset(
    {
        "task/pause",
        "task/resume",
        "task/force_end",
        "supply/recall",
        "clean/start_clean",
    }
)

# Topics that must never be sent, even if a caller names them.
FORBIDDEN_SEND_TOPICS = frozenset(
    {
        "clean/plan/start",
        "clean/easy_clean/start",
        "clean/set_fan_level",
        "clean/set_mop_humidity",
        "task/cancel",
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


def require_send_topic(short_topic: str, *, control: bool = False) -> str:
    """Reject any topic outside the allowlist for the current mode.

    Read topics are always allowed. Control topics require control=True so a
    default session can never reach an actuation topic.
    """
    if short_topic in FORBIDDEN_SEND_TOPICS:
        raise ReadOnlyViolation(
            f"refusing to send topic {short_topic!r}; it is on the permanent deny list",
            detail=short_topic,
        )
    if short_topic in ALLOWED_SEND_TOPICS:
        return short_topic
    if control and short_topic in CONTROL_SEND_TOPICS:
        return short_topic
    raise ReadOnlyViolation(
        f"refusing to send topic {short_topic!r}; not allowed in this mode",
        detail=short_topic,
    )
