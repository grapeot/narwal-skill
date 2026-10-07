"""Configuration. Precedence is flags > process env > explicit env-file.

This module never reads a .env from the working directory on its own.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

from narwal_skill.errors import UsageError

DEFAULT_PORT = 9002
DEFAULT_BUDGET_S = 45.0
DEFAULT_QUERY_TIMEOUT_S = 8.0
DEFAULT_CONNECT_TIMEOUT_S = 5.0
DEFAULT_LISTEN_S = 3.0
MAX_WATCH_S = 300.0
MAX_DISCOVER_ATTEMPTS = 3

# Public model-level product keys for the local WebSocket port 9002 stack.
# These are not per-device identifiers. Cloud-only and non-9002 keys are omitted.
WS9002_MODELS: dict[str, str] = {
    "QoEsI5qYXO": "Narwal Flow",
    "QxMSPG6VSO": "Narwal Flow 2",
    "iSuVlI1If2": "Narwal Flow 2",
    "mkbqaprvrb": "Narwal Flow 2",
    "DrzDKQ0MU8": "Freo Z10 Ultra",
    "qV6BujoYLz": "Freo Z10 Pro/Turbo",
    "hEA7OEshlx": "Freo Z Ultra",
    "fjhpiem4ba": "Freo 20",
    "ulonq49mm1": "Freo 20 Edge",
    "BYWBPqSxeC": "Freo Z Ultra",
    "CGjuB6dzq7": "Narwal JX",
    "CNbforyZWI": "Freo X10 Pro",
}

FLOW2_PRODUCT_KEYS = frozenset({"QxMSPG6VSO", "iSuVlI1If2", "mkbqaprvrb"})

# Gentle discovery order: Flow 2 keys first, then one more confirmed 9002 key.
# Not the full upstream list, and never sent as a burst.
DISCOVER_PRODUCT_KEYS = (
    "QxMSPG6VSO",
    "iSuVlI1If2",
    "mkbqaprvrb",
)


def require_finite(
    name: str,
    value: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    minimum_exclusive: bool = False,
) -> float:
    """Reject NaN, infinities, and out-of-range floats before any socket opens."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise UsageError(
            f"invalid --{name}",
            detail=f"{name} must be a finite number, got {value!r}",
        )
    number = float(value)
    if minimum is not None and (number < minimum or (minimum_exclusive and number <= minimum)):
        raise UsageError(
            f"invalid --{name}",
            detail=f"{name} must be {'>' if minimum_exclusive else '>='} {minimum}, got {number}",
        )
    if maximum is not None and number > maximum:
        raise UsageError(
            f"invalid --{name}",
            detail=f"{name} must be <= {maximum}, got {number}",
        )
    return number


def model_name(product_key: str | None) -> str | None:
    if not product_key:
        return None
    return WS9002_MODELS.get(product_key)


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines. No interpolation, no automatic search."""
    if not path.is_file():
        raise UsageError(
            f"env file not found: {path}",
            detail=f"explicit --env-file does not exist: {path}",
        )
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot read env file: {path}", detail=str(exc), cause=exc) from exc
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise UsageError(
                f"invalid env file line {line_no}",
                detail=f"{path}:{line_no} is not KEY=VALUE",
            )
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not key:
            raise UsageError(f"invalid env file line {line_no}", detail=f"{path}:{line_no} empty key")
        values[key] = value
    return values


def _pick(flag: str | None, env_name: str, env: dict[str, str], file_values: dict[str, str]) -> str | None:
    if flag is not None and flag != "":
        return flag
    if env.get(env_name):
        return env[env_name]
    if file_values.get(env_name):
        return file_values[env_name]
    return None


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    product_key: str | None
    device_id: str | None
    budget_s: float
    query_timeout_s: float
    connect_timeout_s: float
    coordinate_policy: str
    grid_offset_x: float
    grid_offset_y: float
    render_scale: int
    discover: bool

    def topic(self, short_topic: str, product_key: str | None = None, device_id: str | None = None) -> str:
        key = product_key if product_key is not None else (self.product_key or "")
        device = device_id if device_id is not None else (self.device_id or "")
        return f"/{key}/{device}/{short_topic}"


def resolve_settings(
    *,
    host: str | None,
    port: int | None,
    product_key: str | None,
    device_id: str | None,
    env_file: str | None,
    budget_s: float | None,
    query_timeout_s: float | None,
    coordinate_policy: str | None,
    grid_offset_x: float | None,
    grid_offset_y: float | None,
    render_scale: int | None,
    discover: bool,
    environ: dict[str, str] | None = None,
) -> Settings:
    file_values: dict[str, str] = {}
    if env_file:
        file_values = parse_env_file(Path(env_file))
    env = dict(os.environ if environ is None else environ)

    host_value = _pick(host, "NARWAL_HOST", env, file_values)
    if not host_value:
        raise UsageError(
            "host is required",
            detail="pass --host, set NARWAL_HOST, or set NARWAL_HOST in an explicit --env-file. "
            "A .env in the working directory is not read automatically.",
        )

    port_flag = str(port) if port is not None else None
    port_text = _pick(port_flag, "NARWAL_PORT", env, file_values) or str(DEFAULT_PORT)
    try:
        port_value = int(port_text)
    except ValueError as exc:
        raise UsageError(f"invalid port {port_text!r}", detail=str(exc), cause=exc) from exc
    if not 1 <= port_value <= 65535:
        raise UsageError(f"invalid port {port_value}", detail="port must be 1..65535")

    key = _pick(product_key, "NARWAL_PRODUCT_KEY", env, file_values)
    device = _pick(device_id, "NARWAL_DEVICE_ID", env, file_values)
    if key is not None:
        key = key.strip() or None
    if device is not None:
        device = device.strip() or None

    if not key and not discover:
        raise UsageError(
            "product key is required unless --discover is set",
            detail="pass --product-key or NARWAL_PRODUCT_KEY. "
            "Host-only bootstrap can discover a device id, but a public model product key "
            "is required for the addressed topic. --discover tries at most "
            f"{MAX_DISCOVER_ATTEMPTS} Flow 2 keys serially inside the {DEFAULT_BUDGET_S:.0f}s budget.",
        )
    if key and key not in WS9002_MODELS:
        # Still allow an explicit key the operator supplies for a 9002 model we have not named.
        # Discovery will not invent cloud-only keys.
        pass

    policy = coordinate_policy or env.get("NARWAL_COORDINATE_POLICY") or file_values.get(
        "NARWAL_COORDINATE_POLICY"
    ) or "grid"
    if policy not in {"grid", "upstream", "raw"}:
        raise UsageError(
            f"invalid coordinate policy {policy!r}",
            detail="expected grid, upstream, or raw",
        )

    budget = require_finite(
        "budget",
        DEFAULT_BUDGET_S if budget_s is None else budget_s,
        minimum=0,
        maximum=120,
        minimum_exclusive=True,
    )
    timeout = require_finite(
        "query-timeout",
        DEFAULT_QUERY_TIMEOUT_S if query_timeout_s is None else query_timeout_s,
        minimum=0,
        maximum=budget,
        minimum_exclusive=True,
    )
    offset_x = require_finite(
        "grid-offset-x",
        0.0 if grid_offset_x is None else grid_offset_x,
        minimum=-1_000_000,
        maximum=1_000_000,
    )
    offset_y = require_finite(
        "grid-offset-y",
        0.0 if grid_offset_y is None else grid_offset_y,
        minimum=-1_000_000,
        maximum=1_000_000,
    )
    scale = 3 if render_scale is None else render_scale
    if isinstance(scale, bool) or not isinstance(scale, int) or not 1 <= scale <= 8:
        raise UsageError("invalid --render-scale", detail="render scale must be an integer 1..8")

    return Settings(
        host=host_value,
        port=port_value,
        product_key=key,
        device_id=device,
        budget_s=budget,
        query_timeout_s=timeout,
        connect_timeout_s=min(DEFAULT_CONNECT_TIMEOUT_S, budget),
        coordinate_policy=policy,
        grid_offset_x=offset_x,
        grid_offset_y=offset_y,
        render_scale=scale,
        discover=discover,
    )
