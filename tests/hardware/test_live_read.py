"""Opt-in read-only hardware check. Skipped unless NARWAL_LIVE_TEST=1.

No default host. This file must not be executed against a robot by unit CI.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("NARWAL_LIVE_TEST") != "1",
    reason="set NARWAL_LIVE_TEST=1 to run the read-only hardware check",
)


def test_live_snapshot_readonly() -> None:
    host = os.environ.get("NARWAL_HOST")
    product_key = os.environ.get("NARWAL_PRODUCT_KEY")
    if not host or not product_key:
        pytest.skip("NARWAL_HOST and NARWAL_PRODUCT_KEY are required; there is no default device")
    from narwal_skill.cli import main

    code = main(
        [
            "snapshot",
            "--host",
            host,
            "--product-key",
            product_key,
            "--listen-seconds",
            "1",
        ]
    )
    assert code in {0, 3}
