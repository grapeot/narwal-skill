"""Import and entry-point smoke. Does not open a socket."""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path

import narwal_skill
from narwal_skill.allowlist import (
    CONTROL_SEND_TOPICS,
    FORBIDDEN_SEND_TOPICS,
    require_send_topic,
)
from narwal_skill.errors import ReadOnlyViolation


def _must_reject(topic: str, *, control: bool) -> None:
    try:
        require_send_topic(topic, control=control)
    except ReadOnlyViolation:
        return
    raise SystemExit(f"{topic} was not rejected (control={control})")


def main() -> None:
    package = Path(narwal_skill.__file__).parent
    if not (package / "py.typed").is_file():
        raise SystemExit("py.typed missing")
    scripts = {item.name for item in entry_points(group="console_scripts")}
    if "narwal-local" not in scripts:
        raise SystemExit(f"console script missing: {sorted(scripts)}")
    # Permanent deny list is unrelaxable, even with control=True.
    _must_reject("developer/take_picture", control=True)
    _must_reject("common/reboot", control=True)
    if "common/reboot" not in FORBIDDEN_SEND_TOPICS:
        raise SystemExit("reboot missing from the forbidden set")
    # Control topics are never reachable without the control flag.
    if not CONTROL_SEND_TOPICS:
        raise SystemExit("control allowlist is empty")
    for topic in CONTROL_SEND_TOPICS:
        _must_reject(topic, control=False)
    print("smoke-import-ok")


if __name__ == "__main__":
    main()
