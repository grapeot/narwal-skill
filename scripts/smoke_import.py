"""Import and entry-point smoke. Does not open a socket."""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path

import narwal_skill
from narwal_skill.allowlist import FORBIDDEN_SEND_TOPICS, require_send_topic
from narwal_skill.errors import ReadOnlyViolation


def main() -> None:
    package = Path(narwal_skill.__file__).parent
    if not (package / "py.typed").is_file():
        raise SystemExit("py.typed missing")
    scripts = {item.name for item in entry_points(group="console_scripts")}
    if "narwal-local" not in scripts:
        raise SystemExit(f"console script missing: {sorted(scripts)}")
    try:
        require_send_topic("developer/take_picture")
    except ReadOnlyViolation:
        pass
    else:
        raise SystemExit("take_picture was not rejected")
    if "task/pause" not in FORBIDDEN_SEND_TOPICS:
        raise SystemExit("actuation topic missing from the forbidden set")
    print("smoke-import-ok")


if __name__ == "__main__":
    main()
