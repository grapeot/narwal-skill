"""Read-only command orchestration. No actuation and no reconnect loop."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from narwal_skill.config import (
    DISCOVER_PRODUCT_KEYS,
    MAX_DISCOVER_ATTEMPTS,
    Settings,
    model_name,
)
from narwal_skill.envelope import Envelope, empty_device, exit_code_for, json_safe, utc_now
from narwal_skill.errors import (
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_QUERY,
    EXIT_USAGE,
    ArtifactError,
    BudgetExceeded,
    CommandNotApplied,
    ConfirmationRequired,
    ConnectionCap,
    NarwalError,
    QueryError,
    TransportError,
    UsageError,
)
from narwal_skill.map_export import (
    map_metadata,
    parse_map_response,
    position_from_display,
    write_map_artifacts,
)
from narwal_skill.session import Broadcast, QueryResult, ReadOnlySession, rejected_or_none
from narwal_skill.telemetry import (
    broadcast_absence,
    normalize_base,
    normalize_working,
    parse_identity,
    unwrap_base_status,
)

Diag = Callable[[str], None]
MAX_CONNECTIONS = 6


class Runner:
    def __init__(self, settings: Settings, diag: Diag, *, deadline: float | None = None) -> None:
        self.settings = settings
        self.diag = diag
        self.deadline = time.monotonic() + settings.budget_s if deadline is None else deadline
        self.opens = 0
        self.identity: dict[str, str] | None = None
        self.identity_observed_at: str | None = None
        self.sent_topics: list[str] = []

    def url(self) -> str:
        host = self.settings.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"ws://{host}:{self.settings.port}"

    def _topic(self, short: str) -> str:
        key = (self.identity or {}).get("product_key") or self.settings.product_key or ""
        device = (self.identity or {}).get("device_id") or self.settings.device_id or ""
        return self.settings.topic(short, key, device)

    async def open_session(self, *, allow_control: bool = False) -> ReadOnlySession:
        """Open one connection against the command deadline. Callers cannot extend it."""
        if self.opens >= MAX_CONNECTIONS:
            raise ConnectionCap(
                "serial connection cap reached",
                detail=f"refusing a {MAX_CONNECTIONS + 1}th connection; no retry loop",
            )
        if time.monotonic() >= self.deadline:
            raise BudgetExceeded(
                "session budget exhausted",
                detail="budget elapsed before connect; refusing a fresh deadline",
            )
        self.opens += 1
        session = ReadOnlySession(
            url=self.url(),
            connect_timeout_s=min(self.settings.connect_timeout_s, max(0.0, self.deadline - time.monotonic())),
            deadline=self.deadline,
            allow_control=allow_control,
            sent_topics=self.sent_topics,
        )
        self.diag(f"connecting {self.url()} ({self.opens}/{MAX_CONNECTIONS})")
        await session.connect()
        return session

    async def query(self, session: ReadOnlySession, short: str, payload: bytes = b"") -> QueryResult:
        self.diag(f"query {short}")
        result = await session.query(
            short,
            payload,
            full_topic=self._topic(short),
            timeout_s=self.settings.query_timeout_s,
        )
        rejected = rejected_or_none(result.decoded, short)
        if rejected is not None:
            raise rejected
        return result

    async def send_control(
        self,
        session: ReadOnlySession,
        short: str,
        payload: bytes = b"",
        *,
        timeout_s: float | None = None,
    ) -> QueryResult:
        self.diag(f"command {short} ({len(payload)} bytes)")
        return await session.query(
            short,
            payload,
            full_topic=self._topic(short),
            timeout_s=timeout_s if timeout_s is not None else self.settings.query_timeout_s,
            control=True,
        )

    async def discover(self) -> dict[str, str]:
        keys: list[str]
        if self.settings.product_key:
            keys = [self.settings.product_key]
        else:
            keys = list(DISCOVER_PRODUCT_KEYS[:MAX_DISCOVER_ATTEMPTS])
        last: Exception | None = None
        for key in keys:
            if self.opens >= MAX_CONNECTIONS:
                break
            session = await self.open_session()
            try:
                self.settings = replace_key(self.settings, key)
                result = await self.query(session, "common/get_device_info")
                identity = parse_identity(result.decoded)
                if identity is None:
                    last = QueryError(
                        "get_device_info payload was not identity bytes",
                        detail="fields 1/2/3 were missing, empty, or a rejected result code",
                    )
                    continue
                self.identity = identity
                self.identity_observed_at = result.observed_at
                self.diag(f"identity product_key={identity['product_key']}")
                return identity
            except NarwalError as exc:
                last = exc
                self.diag(f"identity attempt failed: {exc.message}")
            finally:
                await session.aclose()
        if last is None:
            last = QueryError("identity discovery did not run", detail="no product key attempts")
        raise last


def replace_key(settings: Settings, product_key: str) -> Settings:
    return Settings(
        host=settings.host,
        port=settings.port,
        product_key=product_key,
        device_id=settings.device_id,
        budget_s=settings.budget_s,
        query_timeout_s=settings.query_timeout_s,
        connect_timeout_s=settings.connect_timeout_s,
        coordinate_policy=settings.coordinate_policy,
        grid_offset_x=settings.grid_offset_x,
        grid_offset_y=settings.grid_offset_y,
        render_scale=settings.render_scale,
        discover=settings.discover,
    )


def apply_device(env: Envelope, identity: dict[str, str] | None) -> None:
    if not identity:
        env.device = empty_device()
        return
    key = identity.get("product_key")
    env.device = {
        "model": model_name(key),
        "product_key": key,
        "device_id": identity.get("device_id"),
        "firmware_version": identity.get("firmware_version"),
    }
    if key and model_name(key) is None:
        env.warn(
            "unknown_product_key",
            "product key is not in the port-9002 model table; model is unknown, not a claim of support",
        )


def _latest(broadcasts: list[Broadcast], short: str) -> Broadcast | None:
    found = [item for item in broadcasts if item.short_topic == short and item.decoded is not None]
    return found[-1] if found else None


def _record(env: Envelope, exc: Exception, *, fatal: bool) -> None:
    env.fail(exc)
    if not fatal and env.errors:
        env.status = "partial"


async def tcp_check(host: str, port: int, timeout_s: float) -> None:
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout_s)
    except Exception as exc:
        raise TransportError(
            f"tcp connect failed for {host}:{port}",
            detail=f"{type(exc).__name__}: {exc}",
            cause=exc,
        ) from exc
    writer.close()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(writer.wait_closed(), timeout=1.5)


async def run_snapshot(
    settings: Settings,
    *,
    listen_s: float,
    with_map: bool,
    with_features: bool,
    out_dir: Path,
    diag: Diag,
) -> Envelope:
    env = Envelope(observed_at=utc_now())
    try:
        from narwal_skill.config import require_finite

        listen_s = require_finite(
            "listen-seconds", listen_s, minimum=0, maximum=settings.budget_s
        )
    except UsageError as exc:
        env.fail(exc)
        env.status = "failed"
        env.exit_code = exc.exit_code
        return env
    runner = Runner(settings, diag)
    try:
        identity = await runner.discover()
    except NarwalError as exc:
        env.fail(exc)
        env.device = empty_device()
        env.data = {"broadcast": broadcast_absence()}
        env.status = "failed"
        env.exit_code = exc.exit_code
        return env
    apply_device(env, identity)
    session: ReadOnlySession | None = None
    broadcasts: list[Broadcast] = []
    base_query: dict[str, Any] | None = None
    try:
        session = await runner.open_session()
        sub_for = max(1, int(listen_s) + 2)
        await session.subscribe(
            sub_for,
            full_topic=runner._topic("common/active_robot_publish"),
            timeout_s=settings.query_timeout_s,
        )
        base_result = await runner.query(session, "status/get_device_base_status")
        payload = unwrap_base_status(base_result.decoded)
        if payload is None:
            raise QueryError(
                "base status payload failed shape validation",
                detail="rejected result or missing base-status message; not treated as data",
            )
        base_query = normalize_base(
            payload,
            source="status/get_device_base_status",
            observed_at=base_result.observed_at,
        )
        if with_features:
            feature_result = await runner.query(session, "common/get_feature_list")
            env.data["features"] = json_safe(feature_result.decoded)
            env.data["features_observed_at"] = feature_result.observed_at
        if listen_s > 0:
            await session.listen(
                listen_s,
                heartbeat_topic=runner._topic("status/app_status_heartbeat"),
                within_deadline=True,
            )
        broadcasts = list(session.broadcasts)
    except NarwalError as exc:
        _record(env, exc, fatal=False)
        diag(exc.message)
    finally:
        if session is not None:
            if not broadcasts:
                broadcasts = list(session.broadcasts)
            _note_broadcast_quality(env, session, broadcasts)
            if session.listen_clipped:
                env.warn(
                    "listen_budget_clipped",
                    "listen was shortened to the remaining setup budget",
                )
            await session.aclose()

    working = _latest(broadcasts, "status/working_status")
    base_broadcast = _latest(broadcasts, "status/robot_base_status")
    display = _latest(broadcasts, "map/display_map")
    task = normalize_working(
        working.decoded if working else None,
        product_key=identity.get("product_key"),
        source="status/working_status" if working else None,
        observed_at=working.observed_at if working else None,
    )
    if task["progress"]["ambiguous"]:
        env.warn(
            "progress_not_normalized",
            "working_status field 1 was not converted with the 0..1 times 100 rule",
        )
    if task["schedule_fragment"] is not None:
        env.warn(
            "schedule_fragment_only",
            "embedded cron JSON is a current-task fragment, not the full schedule list",
        )
    broadcast_view = broadcast_absence()
    if broadcasts:
        broadcast_view = {
            "observed": True,
            "robot_state": "unknown",
            "topics": sorted({item.short_topic for item in broadcasts}),
            "count": len(broadcasts),
            "note": "Broadcast presence is not a full activity claim. Absence would be unknown, not asleep.",
        }
    else:
        env.warn("no_broadcast", broadcast_view["note"])
    env.data.update(
        {
            "identity_observed_at": runner.identity_observed_at,
            "base_query": base_query,
            "base_broadcast": (
                normalize_base(
                    unwrap_base_status(base_broadcast.decoded) or base_broadcast.decoded,
                    source="status/robot_base_status",
                    observed_at=base_broadcast.observed_at,
                )
                if base_broadcast and base_broadcast.decoded
                else None
            ),
            "task": task,
            "broadcast": broadcast_view,
            "listen_seconds": listen_s,
            "sent_topics": list(runner.sent_topics),
        }
    )
    if with_map and "artifact" not in {item.get("code") for item in env.errors}:
        try:
            artifacts, meta, position_error, listen_clipped = await _fetch_map(
                runner, settings, out_dir, diag, display
            )
            env.artifacts.extend(artifacts)
            env.data["map"] = meta
            _note_position_sample(env, position_error, listen_clipped, meta)
        except NarwalError as exc:
            _record(env, exc, fatal=False)
            diag(exc.message)
    _finalize(env)
    return env


async def run_map(settings: Settings, *, listen_s: float, out_dir: Path, diag: Diag) -> Envelope:
    env = Envelope(observed_at=utc_now())
    try:
        from narwal_skill.config import require_finite

        listen_s = require_finite(
            "listen-seconds", listen_s, minimum=0, maximum=settings.budget_s
        )
    except UsageError as exc:
        env.fail(exc)
        env.status = "failed"
        env.exit_code = exc.exit_code
        env.device = empty_device()
        return env
    runner = Runner(settings, diag)
    try:
        identity = await runner.discover()
    except NarwalError as exc:
        env.fail(exc)
        env.device = empty_device()
        env.status = "failed"
        env.exit_code = exc.exit_code
        return env
    apply_device(env, identity)
    try:
        artifacts, meta, position_error, listen_clipped = await _fetch_map(
            runner, settings, out_dir, diag, None, listen_s=listen_s
        )
    except NarwalError as exc:
        env.fail(exc)
        env.status = "failed"
        env.exit_code = exc.exit_code
        env.data = {"calibration_warning": meta_warning()}
        return env
    env.artifacts.extend(artifacts)
    env.data = {"map": meta, "listen_seconds": listen_s}
    if meta.get("robot") is None:
        env.warn("position_unknown", "No in-range display_map position. Robot state is unknown, not asleep.")
    if meta.get("dock", {}).get("raw_x") is not None and not meta["dock"].get("drawn"):
        env.warn("dock_not_drawn", "Dock coordinate was not drawn because it is uncalibrated or out of range.")
    if meta.get("robot") and not meta["robot"].get("drawn"):
        env.warn("robot_not_drawn", "Robot coordinate was not drawn because it is uncalibrated or out of range.")
    env.warn("calibration", meta["calibration_warning"])
    _note_position_sample(env, position_error, listen_clipped, meta)
    _finalize(env)
    return env


async def run_watch(
    settings: Settings,
    *,
    duration_s: float,
    out_dir: Path,
    raw: bool,
    diag: Diag,
) -> Envelope:
    env = Envelope(observed_at=utc_now())
    try:
        from narwal_skill.config import MAX_WATCH_S, require_finite

        duration_s = require_finite(
            "duration",
            duration_s,
            minimum=0,
            maximum=MAX_WATCH_S,
            minimum_exclusive=True,
        )
    except UsageError as exc:
        env.fail(exc)
        env.status = "failed"
        env.exit_code = exc.exit_code
        env.device = empty_device()
        return env
    runner = Runner(settings, diag)
    try:
        identity = await runner.discover()
    except NarwalError as exc:
        env.fail(exc)
        env.device = empty_device()
        env.status = "failed"
        env.exit_code = exc.exit_code
        return env
    apply_device(env, identity)
    from narwal_skill.map_export import unique_artifact

    try:
        jsonl_path = unique_artifact(out_dir, "watch", ".jsonl")
    except ArtifactError as exc:
        env.fail(exc)
        env.status = "failed"
        env.exit_code = exc.exit_code
        return env
    raw_path = None
    if raw:
        env.warn(
            "raw_capture_private",
            "Raw capture may contain private home telemetry. Do not commit this file.",
        )
        raw_path = jsonl_path.with_name(jsonl_path.stem + ".raw.jsonl")
    session: ReadOnlySession | None = None
    completed = False
    started_listen = False
    try:
        session = await runner.open_session()
        try:
            await session.subscribe(
                max(1, min(300, int(duration_s) + 2)),
                full_topic=runner._topic("common/active_robot_publish"),
                timeout_s=settings.query_timeout_s,
            )
        except NarwalError as exc:
            _record(env, exc, fatal=False)
            diag(exc.message)
            await session.aclose()
            session = await runner.open_session()
        started_listen = True
        await session.listen(
            duration_s,
            heartbeat_topic=runner._topic("status/app_status_heartbeat"),
            within_deadline=False,
        )
        completed = True
    except NarwalError as exc:
        _record(env, exc, fatal=not completed)
        diag(exc.message)
    finally:
        broadcasts = list(session.broadcasts) if session is not None else []
        if session is not None:
            _note_broadcast_quality(env, session, broadcasts)
            await session.aclose()
    if not started_listen:
        env.status = "failed"
        env.exit_code = exit_code_for("failed", env.errors) if env.errors else EXIT_QUERY
        return env
    try:
        _write_jsonl(jsonl_path, broadcasts, identity, raw_path)
    except ArtifactError as exc:
        env.fail(exc)
        env.status = "failed"
        env.exit_code = exc.exit_code
        return env
    env.artifacts.append({"type": "watch_jsonl", "path": str(jsonl_path.resolve())})
    if raw_path is not None:
        env.artifacts.append({"type": "watch_raw_jsonl", "path": str(raw_path.resolve())})
    topics = sorted({item.short_topic for item in broadcasts})
    env.data = {
        "duration_seconds": duration_s,
        "event_count": len(broadcasts),
        "topics": topics,
        "broadcast": (
            {
                "observed": True,
                "robot_state": "unknown",
                "topics": topics,
                "count": len(broadcasts),
            }
            if broadcasts
            else broadcast_absence()
        ),
        "interrupted": not completed,
        "broadcast_frame_errors": env.data.get("broadcast_frame_errors", 0),
        "broadcast_decode_errors": env.data.get("broadcast_decode_errors", 0),
    }
    if not broadcasts and completed:
        env.warn("no_broadcast", "No broadcast was observed. That is unknown, not asleep.")
    if env.errors and (completed or started_listen):
        env.status = "partial"
    elif env.errors:
        env.status = "failed"
        env.exit_code = exit_code_for("failed", env.errors)
        return env
    _finalize(env)
    return env


async def run_doctor(settings: Settings, diag: Diag) -> Envelope:
    env = Envelope(observed_at=utc_now())
    checks: list[dict[str, Any]] = []
    env.data = {
        "checks": checks,
        "application_ok": False,
        "websocket_ping_treated_as_application_success": False,
        "map_queried": False,
    }
    runner = Runner(settings, diag)
    try:
        timeout = min(settings.connect_timeout_s, runner.deadline - time.monotonic())
        if timeout <= 0:
            raise BudgetExceeded(
                "doctor budget exhausted before TCP",
                detail="TCP connect is inside the shared command budget",
            )
        diag(f"tcp {settings.host}:{settings.port}")
        await tcp_check(settings.host, settings.port, timeout)
        checks.append({"name": "tcp", "status": "ok", "detail": "tcp connect succeeded"})
    except NarwalError as exc:
        checks.append({"name": "tcp", "status": "failed", "detail": exc.detail})
        env.fail(exc)
        env.status = "failed"
        env.exit_code = exc.exit_code
        env.device = empty_device()
        return env

    session: ReadOnlySession | None = None
    try:
        session = await runner.open_session()
        checks.append({"name": "websocket", "status": "ok", "detail": "websocket handshake succeeded"})
    except NarwalError as exc:
        checks.append({"name": "websocket", "status": "failed", "detail": exc.detail})
        env.fail(exc)
        env.status = "failed"
        env.exit_code = exc.exit_code
        env.device = empty_device()
        return env

    try:
        result = await runner.query(session, "common/get_device_info")
        identity = parse_identity(result.decoded)
        if identity is None:
            raise QueryError(
                "identity query payload was not fields 1/2/3 bytes",
                detail="non-applicable or rejected payloads are not identity data",
            )
        runner.identity = identity
        apply_device(env, identity)
        checks.append({"name": "identity", "status": "ok", "detail": "get_device_info returned identity bytes"})
        env.data["application_ok"] = True
    except NarwalError as exc:
        checks.append({"name": "identity", "status": "failed", "detail": exc.detail})
        env.fail(exc)
        env.device = empty_device()
        env.status = "failed"
        env.exit_code = exc.exit_code
        if session is not None:
            await session.aclose()
        return env

    try:
        if session is None or not session.usable:
            session = await runner.open_session()
        base = await runner.query(session, "status/get_device_base_status")
        payload = unwrap_base_status(base.decoded)
        if payload is None:
            raise QueryError(
                "base status query was not a status payload",
                detail="rejected or wrong-shaped payload was not treated as data",
            )
        checks.append({"name": "query", "status": "ok", "detail": "get_device_base_status returned a status message"})
        env.data["base_query"] = normalize_base(
            payload,
            source="status/get_device_base_status",
            observed_at=base.observed_at,
        )
    except NarwalError as exc:
        checks.append({"name": "query", "status": "failed", "detail": exc.detail})
        _record(env, exc, fatal=False)
    finally:
        if session is not None:
            await session.aclose()
    _finalize(env)
    return env


CONTROL_ACTIONS = ("pause", "resume", "stop", "dock", "start", "clean")
STOP_QUERY_TIMEOUT_S = 15.0


def _audit_line(
    *,
    action: str,
    short_topic: str,
    full_topic: str,
    payload: bytes,
    result_code: int | None,
    outcome: str,
) -> str:
    return json.dumps(
        {
            "observed_at": utc_now(),
            "action": action,
            "topic": short_topic,
            "full_topic": full_topic,
            "payload_hex": payload.hex(),
            "result_code": result_code,
            "outcome": outcome,
        },
        allow_nan=False,
    )


def _write_audit(out_dir: Path, line: str) -> Path:
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "control_audit.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        raise ArtifactError(
            "failed to write control audit log",
            detail=f"{type(exc).__name__}: {exc}",
            cause=exc,
        ) from exc
    return path


def _preflight_view(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    mode = payload.get("mode") or {}
    return {
        "battery_percent": payload.get("battery_percent"),
        "working_mode": mode.get("semantic"),
        "working_mode_code": mode.get("code"),
        "fault_codes": payload.get("fault_codes"),
        "note": "Informational only. The robot arbitrates whether the command applies.",
    }


async def _load_map(session: ReadOnlySession, runner: Runner) -> tuple[int, list[int]]:
    """Fetch the active map and return (map_id, cleanable room ids)."""
    from narwal_skill.map_export import room_records

    result = await runner.query(session, "map/get_map")
    map_data = parse_map_response(result.decoded)
    if not map_data.map_id:
        raise QueryError(
            "active map has no map id",
            detail="clean/start_clean needs the active map id from get_map field 2.1",
        )
    rooms = [record["room_id"] for record in room_records(map_data) if record["room_id"] > 0]
    return map_data.map_id, rooms


async def run_control(
    settings: Settings,
    *,
    action: str,
    rooms: list[int],
    mode: str,
    fan: str,
    water: str,
    passes: int,
    dry_run: bool,
    yes: bool,
    no_audit: bool,
    out_dir: Path,
    diag: Diag,
) -> Envelope:
    from narwal_skill.control import EMPTY_CONTROL_TOPICS, control_result

    env = Envelope(observed_at=utc_now())
    if action not in CONTROL_ACTIONS:
        env.fail(UsageError(f"unknown control action {action!r}", detail=action))
        env.status = "failed"
        env.exit_code = EXIT_USAGE
        env.device = empty_device()
        return env
    if not yes and not dry_run:
        env.fail(
            ConfirmationRequired(
                "control commands need an explicit --yes",
                detail="re-run with --yes to send, or --dry-run to preview without sending",
            )
        )
        env.status = "failed"
        env.exit_code = EXIT_USAGE
        env.device = empty_device()
        return env
    if action == "clean" and not rooms:
        env.fail(UsageError("clean requires --rooms", detail="pass one or more room ids, e.g. --rooms 3,5"))
        env.status = "failed"
        env.exit_code = EXIT_USAGE
        env.device = empty_device()
        return env

    runner = Runner(settings, diag)
    # Preflight and map lookup need a read session; the command needs a control
    # session. The allowlist gate is per-session, so both are explicit here.
    pre_session: ReadOnlySession | None = None
    control_session: ReadOnlySession | None = None
    payload = b""
    short_topic = ""
    try:
        identity = await runner.discover()
        apply_device(env, identity)
    except NarwalError as exc:
        env.fail(exc)
        env.device = empty_device()
        env.status = "failed"
        env.exit_code = exc.exit_code
        return env

    try:
        pre_session = await runner.open_session()
        base = await runner.query(pre_session, "status/get_device_base_status")
        base_payload = unwrap_base_status(base.decoded)
        env.data["preflight"] = _preflight_view(
            normalize_base(
                base_payload if base_payload is not None else base.decoded,
                source="status/get_device_base_status",
                observed_at=base.observed_at,
            )
            if base_payload is not None
            else None
        )
        if action in {"start", "clean"}:
            map_id, known_rooms = await _load_map(pre_session, runner)
            if action == "clean":
                unknown = [room for room in rooms if room not in known_rooms]
                if unknown:
                    raise UsageError(
                        f"unknown room ids: {unknown}",
                        detail=f"active map rooms are {sorted(known_rooms)}",
                    )
                target_rooms = rooms
            else:
                target_rooms = known_rooms
            if not target_rooms:
                raise QueryError(
                    "no cleanable rooms on the active map",
                    detail="whole-house start needs at least one room on the map",
                )
            env.data["target_rooms"] = target_rooms
            payload = build_start_payload(
                target_rooms,
                map_id,
                mode=mode,
                fan=fan,
                water=water,
                passes=passes,
            )
            short_topic = "clean/start_clean"
        else:
            short_topic, payload = EMPTY_CONTROL_TOPICS[action], b""
    except NarwalError as exc:
        _record(env, exc, fatal=False)
        env.status = "failed"
        env.exit_code = exc.exit_code or EXIT_QUERY
        if pre_session is not None:
            await pre_session.aclose()
        _finalize(env)
        return env
    except (ValueError, TypeError) as exc:
        # A payload-builder error, not a protocol failure. Close the read session
        # so it cannot leak, and report it as a usage error rather than a decode.
        detail = str(exc) or type(exc).__name__
        env.fail(UsageError("could not build the control payload", detail=detail, cause=exc))
        env.status = "failed"
        env.exit_code = EXIT_USAGE
        if pre_session is not None:
            await pre_session.aclose()
        _finalize(env)
        return env

    full_topic = runner._topic(short_topic)
    env.data["action"] = action
    env.data["topic"] = short_topic
    env.data["payload_hex"] = payload.hex()

    if dry_run:
        env.data["dry_run"] = True
        env.data["sent"] = False
        if pre_session is not None:
            await pre_session.aclose()
        _finalize(env)
        return env

    result_code: int | None = None
    outcome = "unknown"
    try:
        if pre_session is not None:
            await pre_session.aclose()
            pre_session = None
        control_session = await runner.open_session(allow_control=True)
        timeout_s = STOP_QUERY_TIMEOUT_S if action == "stop" else None
        result = await runner.send_control(control_session, short_topic, payload, timeout_s=timeout_s)
        code, accepted = control_result(result.decoded)
        result_code = code
        outcome = "accepted" if accepted else "declined"
        env.data["result"] = {
            "code": code,
            "accepted": accepted,
            "echo": code is None,
        }
        if not accepted:
            raise CommandNotApplied(
                f"robot declined {action}",
                detail=f"result_code={code} topic={short_topic}",
            )
    except CommandNotApplied as exc:
        env.fail(exc)
        env.status = "failed"
        env.exit_code = exc.exit_code
    except NarwalError as exc:
        env.fail(exc)
        env.status = "failed"
        env.exit_code = exc.exit_code
    finally:
        if control_session is not None:
            await control_session.aclose()
        if pre_session is not None:
            await pre_session.aclose()

    if not no_audit:
        try:
            audit_path = _write_audit(
                out_dir,
                _audit_line(
                    action=action,
                    short_topic=short_topic,
                    full_topic=full_topic,
                    payload=payload,
                    result_code=result_code,
                    outcome=outcome,
                ),
            )
            env.artifacts.append({"type": "control_audit", "path": str(audit_path.resolve())})
        except ArtifactError as exc:
            # An audit-write failure must not rewrite the command outcome. Record
            # it as a warning so exit 0 (accepted) or 20 (declined) is preserved.
            env.warn("audit_write_failed", exc.detail)

    _finalize(env)
    return env


def build_start_payload(
    room_ids: list[int],
    map_id: int,
    *,
    mode: str,
    fan: str,
    water: str,
    passes: int,
) -> bytes:
    from narwal_skill.control import start_clean_payload

    return start_clean_payload(
        room_ids,
        map_id,
        mode=mode,
        fan=fan,
        water=water,
        passes=passes,
    )


async def _fetch_map(
    runner: Runner,
    settings: Settings,
    out_dir: Path,
    diag: Diag,
    prior_display: Broadcast | None,
    listen_s: float = 2.0,
) -> tuple[list[dict[str, str]], dict[str, Any], str | None, bool]:
    session = await runner.open_session()
    display = prior_display
    position_error: str | None = None
    listen_clipped = False
    try:
        if display is None and listen_s > 0:
            try:
                await session.subscribe(
                    max(1, int(listen_s) + 2),
                    full_topic=runner._topic("common/active_robot_publish"),
                    timeout_s=settings.query_timeout_s,
                )
                await session.listen(listen_s, within_deadline=True)
                listen_clipped = session.listen_clipped
                display = _latest(list(session.broadcasts), "map/display_map") or display
            except NarwalError as exc:
                position_error = exc.detail
                diag(f"position listen failed: {exc.message}")
                await session.aclose()
                session = await runner.open_session()
        if not session.usable:
            session = await runner.open_session()
        result = await runner.query(session, "map/get_map")
    finally:
        await session.aclose()
    map_data = parse_map_response(result.decoded)
    position = position_from_display(display.decoded) if display and display.decoded else None
    meta = map_metadata(
        map_data,
        policy=settings.coordinate_policy,
        offset_x=settings.grid_offset_x,
        offset_y=settings.grid_offset_y,
        render_scale=settings.render_scale,
        position=position,
        position_observed_at=display.observed_at if display else None,
    )
    artifacts = write_map_artifacts(out_dir, map_data, meta)
    return artifacts, meta, position_error, listen_clipped


def _write_jsonl(
    path: Path,
    broadcasts: list[Broadcast],
    identity: dict[str, str] | None,
    raw_path: Path | None,
) -> None:
    product_key = (identity or {}).get("product_key")
    lines: list[str] = []
    raw_lines: list[str] = []
    for item in broadcasts:
        task = None
        base = None
        if item.short_topic == "status/working_status" and item.decoded:
            task = normalize_working(
                item.decoded,
                product_key=product_key,
                source=item.short_topic,
                observed_at=item.observed_at,
            )
        if item.short_topic == "status/robot_base_status" and item.decoded:
            payload = unwrap_base_status(item.decoded) or item.decoded
            base = normalize_base(payload, source=item.short_topic, observed_at=item.observed_at)
        record = {
            "observed_at": item.observed_at,
            "topic": item.short_topic,
            "source": "broadcast",
            "decode_error": item.decode_error,
            "task": task,
            "base": base,
        }
        if item.short_topic == "map/display_map":
            from narwal_skill.map_export import basic_trajectory, canonical_position

            record["position"] = canonical_position(item.decoded)
            record["trajectory"] = basic_trajectory(item.decoded)
        lines.append(json.dumps(json_safe(record), allow_nan=False))
        if raw_path is not None:
            raw_lines.append(
                json.dumps(
                    {
                        "observed_at": item.observed_at,
                        "topic": item.topic,
                        "short_topic": item.short_topic,
                        "payload_hex": item.payload.hex(),
                    }
                )
            )
    try:
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        if raw_path is not None:
            raw_path.write_text("\n".join(raw_lines) + ("\n" if raw_lines else ""), encoding="utf-8")
    except OSError as exc:
        raise ArtifactError(
            "failed to write watch artifact",
            detail=f"{type(exc).__name__}: {exc}",
            cause=exc,
        ) from exc


def meta_warning() -> str:
    from narwal_skill.coordinates import CALIBRATION_WARNING

    return CALIBRATION_WARNING


def _note_position_sample(
    env: Envelope,
    position_error: str | None,
    listen_clipped: bool,
    meta: dict[str, Any],
) -> None:
    if listen_clipped:
        env.warn(
            "listen_budget_clipped",
            "position listen was shortened to the remaining setup budget",
        )
    if position_error:
        env.warn("position_sampling_failed", position_error)
        env.status = "partial"
    robot = meta.get("robot")
    if listen_clipped and not (isinstance(robot, dict) and robot.get("raw_x") is not None):
        env.warn(
            "position_sample_incomplete",
            "position listen ended with the setup budget before a display_map position arrived",
        )
        env.status = "partial"


def _note_broadcast_quality(env: Envelope, session: ReadOnlySession, broadcasts: list[Broadcast]) -> None:
    frame_errors = session.frame_errors
    decode_errors = session.decode_errors
    env.data["broadcast_frame_errors"] = env.data.get("broadcast_frame_errors", 0) + frame_errors
    env.data["broadcast_decode_errors"] = env.data.get("broadcast_decode_errors", 0) + decode_errors
    if not frame_errors and not decode_errors:
        return
    env.warn(
        "broadcast_decode",
        f"unparsed broadcasts: frame_errors={frame_errors} decode_errors={decode_errors}",
    )
    decoded_ok = sum(1 for item in broadcasts if item.decoded is not None)
    if decoded_ok == 0:
        env.status = "partial"


def _finalize(env: Envelope) -> None:
    if env.status == "partial":
        env.exit_code = EXIT_PARTIAL
        return
    if not env.errors:
        env.status = "ok"
        env.exit_code = EXIT_OK
        return
    env.status = "failed"
    env.exit_code = exit_code_for("failed", env.errors)
