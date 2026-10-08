"""narwal-local command line. stdout is one JSON document. stderr is diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from narwal_skill.config import DEFAULT_BUDGET_S, DEFAULT_LISTEN_S, DEFAULT_PORT, MAX_WATCH_S, resolve_settings
from narwal_skill.envelope import Envelope, empty_device
from narwal_skill.errors import EXIT_USAGE, UsageError, exception_payload


def _connection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=None, help="Robot host. Env: NARWAL_HOST. Required. No default address.")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"WebSocket port. Env: NARWAL_PORT. Default {DEFAULT_PORT}.",
    )
    parser.add_argument(
        "--product-key",
        default=None,
        help="Public model product key. Env: NARWAL_PRODUCT_KEY. Required unless --discover.",
    )
    parser.add_argument(
        "--device-id",
        default=None,
        help="Per-device id. Env: NARWAL_DEVICE_ID. No default. Empty lets get_device_info fill it.",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Explicit KEY=VALUE file. A .env in the working directory is not read automatically.",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        help=f"Setup and query budget in seconds, excluding watch --duration. Default {DEFAULT_BUDGET_S:.0f}.",
    )
    parser.add_argument(
        "--query-timeout",
        type=float,
        default=None,
        help="Per-query timeout in seconds. A timeout closes the socket before the next query.",
    )
    parser.add_argument(
        "--coordinate-policy",
        choices=("grid", "upstream", "raw"),
        default=None,
        help="Marker policy: grid, upstream, or raw. Env: NARWAL_COORDINATE_POLICY.",
    )
    parser.add_argument("--grid-offset-x", type=float, default=None, help="Extra X cell offset.")
    parser.add_argument("--grid-offset-y", type=float, default=None, help="Extra Y cell offset.")
    parser.add_argument("--render-scale", type=int, default=None, help="PNG scale in cells. Default 3. Range 1..8.")
    parser.add_argument(
        "--discover",
        action="store_true",
        help="If no product key is set, try at most 3 Flow 2 keys serially. Not a wake burst.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="narwal-local",
        description="Read-only local Narwal WebSocket CLI. stdout is one JSON document.",
    )
    parser.add_argument("--version", action="version", version="narwal-local 0.2.0")
    sub = parser.add_subparsers(dest="command", required=True)

    snapshot = sub.add_parser("snapshot", help="Identity, base status, and a short broadcast listen.")
    _connection(snapshot)
    snapshot.add_argument(
        "--listen-seconds",
        type=float,
        default=DEFAULT_LISTEN_S,
        help=f"Bounded broadcast listen after the queries. Default {DEFAULT_LISTEN_S:.0f}. Must fit in --budget.",
    )
    snapshot.add_argument("--with-map", action="store_true", help="Also query the map and write a PNG.")
    snapshot.add_argument("--with-features", action="store_true", help="Also query common/get_feature_list.")
    snapshot.add_argument("--out-dir", default="artifacts", help="Artifact directory for --with-map.")

    map_cmd = sub.add_parser("map", help="Query the map, listen briefly for position, write PNG and JSON.")
    _connection(map_cmd)
    map_cmd.add_argument("--out-dir", default="artifacts", help="Directory for unique PNG and JSON artifacts.")
    map_cmd.add_argument(
        "--listen-seconds",
        type=float,
        default=DEFAULT_LISTEN_S,
        help="Bounded listen for display_map position before rendering.",
    )

    watch = sub.add_parser("watch", help="Listen for a required duration and write one JSONL artifact.")
    _connection(watch)
    watch.add_argument(
        "--duration",
        type=float,
        required=True,
        help=f"Listen window in seconds. Required. Maximum {MAX_WATCH_S:.0f}. Not counted against --budget.",
    )
    watch.add_argument("--out-dir", default="artifacts", help="Directory for the JSONL artifact.")
    watch.add_argument(
        "--raw",
        action="store_true",
        help="Also write a raw hex JSONL. May contain private home telemetry.",
    )

    doctor = sub.add_parser("doctor", help="TCP, WebSocket, identity, and one status query. Ping is not success.")
    _connection(doctor)

    _control_subcommands(sub)
    return parser


def _control_common(parser: argparse.ArgumentParser) -> None:
    _connection(parser)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm the write command. Required unless --dry-run is set.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the topic and payload, print them, and send nothing. Does not need --yes.",
    )
    parser.add_argument(
        "--no-audit",
        action="store_true",
        help="Skip the control_audit.jsonl line.",
    )
    parser.add_argument("--out-dir", default="artifacts", help="Audit artifact directory.")


def _clean_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mode",
        choices=("vacuum", "mop", "vacuum_then_mop", "vacuum_and_mop"),
        default="vacuum_and_mop",
        help="Clean work mode. Default vacuum_and_mop.",
    )
    parser.add_argument(
        "--fan",
        choices=("mute", "normal", "strong", "deep", "super"),
        default="normal",
        help="Suction level. Default normal.",
    )
    parser.add_argument(
        "--water",
        choices=("dry", "normal", "wet"),
        default="normal",
        help="Mop water volume. Default normal.",
    )
    parser.add_argument("--passes", type=int, default=1, help="Pass count 1..3. Default 1.")


def _control_subcommands(sub: argparse._SubParsersAction) -> None:
    for name, help_text in (
        ("pause", "Pause the current task."),
        ("resume", "Resume a paused task."),
        ("stop", "Force-stop the current task. The robot stops before answering."),
        ("dock", "Send the robot back to its dock (recall)."),
    ):
        parser = sub.add_parser(name, help=help_text)
        _control_common(parser)

    start = sub.add_parser("start", help="Start a whole-house clean over every room on the active map.")
    _control_common(start)
    _clean_options(start)

    clean = sub.add_parser("clean", help="Start a clean for selected rooms.")
    _control_common(clean)
    _clean_options(clean)
    clean.add_argument(
        "--rooms",
        required=True,
        help="Comma-separated robot room ids, e.g. 3,5,7. Validated against the active map.",
    )


def _settings_from(args: argparse.Namespace):
    return resolve_settings(
        host=args.host,
        port=args.port,
        product_key=args.product_key,
        device_id=args.device_id,
        env_file=args.env_file,
        budget_s=args.budget,
        query_timeout_s=args.query_timeout,
        coordinate_policy=args.coordinate_policy,
        grid_offset_x=args.grid_offset_x,
        grid_offset_y=args.grid_offset_y,
        render_scale=args.render_scale,
        discover=args.discover,
    )


def _emit(envelope: Envelope) -> int:
    sys.stdout.write(envelope.emit())
    return envelope.exit_code


def _usage(exc: UsageError) -> int:
    env = Envelope(status="failed", device=empty_device(), exit_code=EXIT_USAGE)
    env.errors.append(exception_payload(exc))
    env.data = {"usage": "narwal-local <snapshot|map|watch|doctor|pause|resume|stop|dock|start|clean> --help"}
    print(exc.message, file=sys.stderr)
    return _emit(env)


async def _run(args: argparse.Namespace) -> Envelope:
    from narwal_skill.commands import run_doctor, run_map, run_snapshot, run_watch

    def diag(message: str) -> None:
        print(message, file=sys.stderr)

    settings = _settings_from(args)
    from narwal_skill.config import require_finite

    if args.command in {"snapshot", "map"}:
        require_finite(
            "listen-seconds",
            args.listen_seconds,
            minimum=0,
            maximum=settings.budget_s,
        )
    if args.command == "watch":
        require_finite(
            "duration",
            args.duration,
            minimum=0,
            maximum=MAX_WATCH_S,
            minimum_exclusive=True,
        )
    if args.command == "snapshot":
        return await run_snapshot(
            settings,
            listen_s=args.listen_seconds,
            with_map=args.with_map,
            with_features=args.with_features,
            out_dir=Path(args.out_dir),
            diag=diag,
        )
    if args.command == "map":
        return await run_map(
            settings,
            listen_s=args.listen_seconds,
            out_dir=Path(args.out_dir),
            diag=diag,
        )
    if args.command == "watch":
        return await run_watch(
            settings,
            duration_s=args.duration,
            out_dir=Path(args.out_dir),
            raw=args.raw,
            diag=diag,
        )
    if args.command == "doctor":
        return await run_doctor(settings, diag)
    if args.command in {"pause", "resume", "stop", "dock", "start", "clean"}:
        from narwal_skill.commands import run_control

        passes = getattr(args, "passes", 1)
        if isinstance(passes, bool) or not isinstance(passes, int) or not 1 <= passes <= 3:
            raise UsageError("invalid --passes", detail="passes must be an integer 1..3")
        return await run_control(
            settings,
            action=args.command,
            rooms=_parse_rooms(getattr(args, "rooms", None)),
            mode=getattr(args, "mode", "vacuum_and_mop"),
            fan=getattr(args, "fan", "normal"),
            water=getattr(args, "water", "normal"),
            passes=passes,
            dry_run=args.dry_run,
            yes=args.yes,
            no_audit=args.no_audit,
            out_dir=Path(args.out_dir),
            diag=diag,
        )
    raise UsageError(f"unknown command {args.command}", detail=args.command)


def _parse_rooms(text: str | None) -> list[int]:
    if text is None:
        return []
    rooms: list[int] = []
    for chunk in text.split(","):
        token = chunk.strip()
        if not token:
            continue
        try:
            room = int(token)
        except ValueError as exc:
            raise UsageError(f"invalid --rooms value {token!r}", detail=str(exc)) from exc
        if room <= 0:
            raise UsageError(f"invalid room id {room}", detail="room ids are positive integers")
        if room not in rooms:
            rooms.append(room)
    return rooms


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else EXIT_USAGE
    try:
        envelope = asyncio.run(_run(args))
    except UsageError as exc:
        return _usage(exc)
    except Exception as exc:
        env = Envelope(status="failed", device=empty_device())
        env.fail(exc)
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return _emit(env)
    return _emit(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
