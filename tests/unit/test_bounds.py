"""Float option bounds fail before any socket is opened."""

from __future__ import annotations

import time

import pytest

from narwal_skill.cli import main
from narwal_skill.commands import Runner
from narwal_skill.config import require_finite, resolve_settings
from narwal_skill.errors import BudgetExceeded, UsageError


def _usage(argv: list[str]) -> int:
    return main(argv)


def test_nonfinite_and_negative_options_are_usage() -> None:
    cases = [
        ["snapshot", "--host", "192.0.2.10", "--product-key", "QxMSPG6VSO", "--budget", "nan"],
        ["snapshot", "--host", "192.0.2.10", "--product-key", "QxMSPG6VSO", "--query-timeout", "inf"],
        ["snapshot", "--host", "192.0.2.10", "--product-key", "QxMSPG6VSO", "--grid-offset-x", "nan"],
        ["map", "--host", "192.0.2.10", "--product-key", "QxMSPG6VSO", "--listen-seconds", "-1"],
        ["watch", "--host", "192.0.2.10", "--product-key", "QxMSPG6VSO", "--duration", "nan"],
        ["watch", "--host", "192.0.2.10", "--product-key", "QxMSPG6VSO", "--duration", "inf"],
    ]
    for argv in cases:
        assert _usage(argv) == 2


def test_require_finite_rejects_nan_without_treating_it_as_in_range() -> None:
    with pytest.raises(UsageError):
        require_finite("budget", float("nan"), minimum=0, maximum=120, minimum_exclusive=True)
    with pytest.raises(UsageError):
        require_finite("listen-seconds", -0.1, minimum=0, maximum=45)


@pytest.mark.asyncio
async def test_open_session_does_not_replace_an_expired_deadline() -> None:
    settings = resolve_settings(
        host="192.0.2.10",
        port=9,
        product_key="QxMSPG6VSO",
        device_id=None,
        env_file=None,
        budget_s=None,
        query_timeout_s=None,
        coordinate_policy=None,
        grid_offset_x=None,
        grid_offset_y=None,
        render_scale=None,
        discover=False,
    )
    runner = Runner(settings, lambda _message: None, deadline=time.monotonic() - 1)
    with pytest.raises(BudgetExceeded):
        await runner.open_session()
