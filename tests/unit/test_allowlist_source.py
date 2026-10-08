"""The product send path cannot name actuation topics in the default mode."""

from __future__ import annotations

import ast
from pathlib import Path

from narwal_skill.allowlist import (
    ALLOWED_SEND_TOPICS,
    CONTROL_SEND_TOPICS,
    FORBIDDEN_SEND_TOPICS,
    require_send_topic,
)
from narwal_skill.errors import ReadOnlyViolation

ROOT = Path(__file__).resolve().parents[2] / "src" / "narwal_skill"


def test_require_send_topic_rejects_actuation_without_control() -> None:
    for topic in CONTROL_SEND_TOPICS:
        with pytest_raises():
            require_send_topic(topic)


def test_require_send_topic_allows_control_only_with_flag() -> None:
    for topic in CONTROL_SEND_TOPICS:
        assert require_send_topic(topic, control=True) == topic


def test_require_send_topic_rejects_forbidden_even_with_control() -> None:
    for topic in FORBIDDEN_SEND_TOPICS:
        with pytest_raises():
            require_send_topic(topic, control=True)


def test_control_and_forbidden_are_disjoint() -> None:
    assert CONTROL_SEND_TOPICS.isdisjoint(FORBIDDEN_SEND_TOPICS)
    assert FORBIDDEN_SEND_TOPICS.isdisjoint(ALLOWED_SEND_TOPICS)


def pytest_raises():
    import pytest

    return pytest.raises(ReadOnlyViolation)


def test_forbidden_topics_appear_only_in_the_allowlist_module() -> None:
    """No product module names a permanently forbidden topic outside allowlist.py."""
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        if "vendor" in path.parts or path.name == "allowlist.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in FORBIDDEN_SEND_TOPICS:
                    offenders.append(f"{path.name}:{node.value}")
    assert offenders == []
    assert "common/get_device_info" in ALLOWED_SEND_TOPICS


def test_vendor_package_does_not_import_control_client() -> None:
    init = (ROOT / "vendor" / "narwal_client" / "__init__.py").read_text()
    assert "import client" not in init
    assert "NarwalClient" not in init
    assert not (ROOT / "vendor" / "narwal_client" / "client.py").exists()
