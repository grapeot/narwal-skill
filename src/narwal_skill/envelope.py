"""Versioned stdout envelope. One document, no traceback."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from narwal_skill.errors import (
    EXIT_ARTIFACT,
    EXIT_COMMAND,
    EXIT_DECODE,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_QUERY,
    EXIT_TRANSPORT,
    EXIT_USAGE,
    NarwalError,
    exception_payload,
)

SCHEMA_VERSION = "1.0"


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def json_safe(value: Any) -> Any:
    """Convert protocol leftovers into JSON values. Bytes become hex, never objects mixed into numbers."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, (bytes, bytearray)):
        return {"encoding": "hex", "hex": bytes(value).hex()}
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return str(value)


@dataclass
class Envelope:
    status: str = "failed"
    observed_at: str = field(default_factory=utc_now)
    device: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    artifacts: list[dict[str, str]] = field(default_factory=list)
    exit_code: int = EXIT_QUERY

    def warn(self, code: str, message: str) -> None:
        self.warnings.append({"code": code, "message": message})

    def fail(self, exc: BaseException) -> None:
        self.errors.append(exception_payload(exc))
        if isinstance(exc, NarwalError):
            self.exit_code = exc.exit_code
        if self.status == "ok":
            self.status = "failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "observed_at": self.observed_at,
            "device": json_safe(self.device),
            "data": json_safe(self.data),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "artifacts": list(self.artifacts),
        }

    def emit(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False, allow_nan=False) + "\n"


def exit_code_for(status: str, errors: list[dict[str, str]]) -> int:
    if status == "ok":
        return EXIT_OK
    if status == "partial":
        return EXIT_PARTIAL
    codes = {item.get("code") for item in errors}
    if "usage" in codes or "readonly_violation" in codes:
        return EXIT_USAGE
    if "transport" in codes:
        return EXIT_TRANSPORT
    if "decode" in codes:
        return EXIT_DECODE
    if "artifact" in codes:
        return EXIT_ARTIFACT
    if codes & {"query", "query_timeout", "query_rejected", "budget_exceeded"}:
        return EXIT_QUERY
    if "confirmation_required" in codes:
        return EXIT_USAGE
    if "command_not_applied" in codes:
        return EXIT_COMMAND
    return EXIT_QUERY


def empty_device() -> dict[str, Any]:
    return {
        "model": None,
        "product_key": None,
        "device_id": None,
        "firmware_version": None,
    }
