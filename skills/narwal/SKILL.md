---
name: narwal-local
description: Read-only status inspection, floor plan maps, bounded live telemetry, and connection diagnostics for port-9002 WebSocket Narwal robot vacuums, plus an opt-in confirmed control mode (pause, resume, stop, dock, start/room clean). Flow 2 smoke tested; other compatible Flow/Freo models are candidates.
---

# Narwal Local Skill

Provide AI coding and automation agents with deterministic local access to Narwal robot vacuums over the local WebSocket protocol on TCP port 9002 without Home Assistant or vendor cloud dependencies. Read-only inspection is the default; an opt-in control mode adds a small, explicitly confirmed set of write commands.

## Goal & Acceptance Criteria

When invoked, the agent executes `narwal-local` subcommands to observe device state and returns structured findings. A task is considered complete when:
1. **Valid JSON Output:** The CLI emits a single versioned JSON envelope (`schema_version: "1.0"`) to `stdout` with `status: "ok"` or `"partial"`, or clean error categorization on `"failed"`.
2. **Freshness Verification:** The `observed_at` UTC timestamp reflects the current command run.
3. **Null vs. Zero Semantic Integrity:** Telemetry metrics that are unpolled, unavailable, or unsupported by the model remain explicit JSON `null`, never numerical `0` or empty strings.
4. **Artifact Verification:** For `map`, the PNG exists and its dimensions equal grid dimensions times render scale. For `watch`, the JSONL exists and any records parse; an empty recording is possible and means no telemetry observed. For control commands, the audit JSONL line records the topic, payload, and result.

## Capability Scope & Safety Boundaries

- **Transport:** Local WebSocket connection directly on TCP port 9002 (`ws://<host>:9002`).
- **Hardware Validation Scope:** Direct physical hardware validation is strictly scoped to the **Narwal Flow 2** (firmware `v01.09.10.02` smoke tested). Other models inherit candidate status from upstream community protocol research, not local hardware certification. Control payloads are not hardware validated here.
- **Candidate Models (Upstream Reports):** Flow AX12, Freo Z10 Ultra (CX4), Freo Z10 Pro/Turbo (AX26), Freo X10 Pro (AX15), Freo 20, Freo 20 Edge, and Freo Z Ultra (CX7; requires explicit device ID, no broadcast).
- **Incompatible Models:** Models on other ports (Freo X Ultra uses ZeroMQ on port 6789), cloud-only models (Freo X Plus, J1/T10, J4), or models refusing port 9002 (standard Freo Z10).
- **Read-only by default:** The default session enforces an allowlist of read/query topics. 
- **Opt-in control:** `pause`, `resume`, `stop`, `dock`, `start`, and `clean` are reachable only from a session opened for control. Every control command requires `--yes`; `--dry-run` previews without sending; writes append to `control_audit.jsonl`.
- **Permanently forbidden (no flag relaxes this):** reboot, shutdown, yell, `common/notify_app_event`, camera and developer topics, live parameter mutation (`clean/set_fan_level`, `clean/set_mop_humidity`), dock maintenance (`supply/wash_mop`, `supply/dry_*`, `supply/dust_gathering`, `supply/ambient_light_ctrl`), `task/cancel`, `clean/plan/start`, `clean/easy_clean/start`, and arbitrary raw command injection.

## Available Tools & Resources

- **CLI Executable:** `narwal-local` (available via the project virtual environment `.venv/bin/narwal-local` or PATH).
- **Subcommands:** `snapshot`, `map`, `watch`, `doctor`, plus opt-in `pause`, `resume`, `stop`, `dock`, `start`, `clean`.
- **Environment Variables:**
  - `NARWAL_HOST`: Robot IP address or hostname (required; no default).
  - `NARWAL_PORT`: WebSocket port (default: `9002`).
  - `NARWAL_PRODUCT_KEY`: Model public product key (e.g., `QxMSPG6VSO` for Flow 2; required unless `--discover`).
  - `NARWAL_DEVICE_ID`: Per-device identifier (optional; populated via `common/get_device_info` if omitted).
  - `NARWAL_COORDINATE_POLICY`: Marker rendering policy (`grid`, `upstream`, or `raw`; default: `grid`).
- **Configuration Precedence:** Command-line flags > Process environment variables > Explicit `--env-file PATH`. A `.env` file in the working directory is **never** loaded automatically.
- **Privacy Boundary:** Real IP addresses, MAC addresses, device IDs, and private home floor plans must remain in local uncommitted files (e.g., `--env-file .env`). Never commit private network data or upload home map artifacts. Public model product keys are protocol constants, not private identifiers.
- **Detailed References:**
  - [compatibility.md](./references/compatibility.md) — Model compatibility matrix, port specifications, and network routing rules.
  - [output-contract.md](./references/output-contract.md) — Detailed JSON schema, telemetry mapping, exit codes, and coordinate policies.
  - Project `docs/tasks.md` — Task-and-judgment guide (state reading, task recipes, product gaps) for human readers.

## The Core Rule: The Robot Is the Authority

The CLI is stateless. Each invocation sends one action or one query and disconnects. It keeps no history and knows nothing about a prior run. Every judgment is made from the JSON returned by the current call.

Two consequences drive everything below:
- **You cannot answer "why did it stop before I got here" from a single snapshot.** Use a bounded `watch` window to see it live, or read the App's task history. This tool has no history.
- **The robot decides whether a command applies.** A valid command can be declined (`NOT_READY` / `CONFLICT` / `NOT_APPLICABLE`, exit `20`). That is an answer, not a bug. Never loop-retry a declined command.

Read the field-level interpretation rules before acting on any status; a task-and-judgment guide lives at `docs/tasks.md` in the project. The essentials:

### Fields you must not trust blindly
- **`is_docked` and `charging_state` are always `null`.** Raw codes are in `dock_raw` / `charging_status_raw` and vary by firmware. To tell whether it is docked or charging, use the **battery trend** (rising across a `watch` window or between snapshots), not these fields.
- **Mode `5` (`cleaning_alt`) is not proof of being stuck.** It may be a normal clean. To judge stuck, watch `map/display_map` position over time (see "Is it stuck / why did it stop").
- **`task.progress.progress_percent` is not an ETA.** It is a reported completion percent. On non-Flow-2 models it may be unnormalized.
- **`task.station_bag_dry_total_seconds` is a station drying timer, not cleaned area.** Area is `task.area_m2`.
- **Coordinates are `not_calibrated`.** Use them only to see whether it moved, never for absolute position.
- **Absent telemetry is `null`, never `0`.** An empty fault list means unknown health, not healthy.

## Task Playbook

Each task lists the default primitive sequence, the interpretation points, and what to do when things differ. Sequences are the default path, not a fixed script.

### Confirm current state
`narwal-local snapshot`. Read `base_query.mode` and `task`: `standby` with null task fields is idle; a cleaning mode with `area_m2` / `elapsed_seconds` is actively cleaning. A `no_broadcast` warning means this short window saw no broadcast, **not** that it is asleep or offline.

### Is it stuck / why did it stop
A single snapshot cannot answer "why". To see whether it is moving, run a bounded `watch` (`--duration 20`) and compare `map/display_map` `position.raw_x/raw_y` across samples. Same point repeatedly, or `lost_context: true`, is when stuck becomes plausible. For "how did it stop earlier", use the App's history — there is none here.

### Start a clean
Whole house `narwal-local start`; selected rooms `narwal-local clean --rooms 3,5`. Both use `clean/start_clean`, which **only applies while the robot is docked**; otherwise the robot answers `NOT_READY` (exit 20). Room ids and `map_id` come from the current `get_map`, not config; the tool never maps a room id to a household name. Use `--dry-run` first to see the topic and payload without sending.

### Intervene (pause / resume / stop / dock)
`pause`→`task/pause`, `resume`→`task/resume`, `stop`→`task/force_end`, `dock`→`supply/recall`. All need `--yes`; `--dry-run` previews without sending a control topic. `stop` is slow by design (the robot physically stops before answering); do not treat the wait as failure. Docking ends the task on its own — do not stack a `stop` after it.

### Watch for a while
`watch --duration SECONDS` (required, max 300), one JSONL line per broadcast. This is the only way to get "a stretch of time"; there is no background daemon, so nothing exists outside the window.

## What This Tool Cannot Do

These are product boundaries, not bugs. State them rather than implying more:
- **No history.** Cannot explain a past stop.
- **No stuck verdict.** Stuck is inferred from movement, not a status bit.
- **No dock maintenance.** Wash mop, dry mop, dust gathering, ambient light are permanently denied; use the App. "Recall then wash mop" is only half doable here (the `dock` half).
- **No room aliases.** Only map room ids.

## Command Reference

| Goal | Command | Key Flags |
|---|---|---|
| Status, battery, task | `narwal-local snapshot` | `[--listen-seconds 3] [--with-map] [--with-features]` |
| Floor plan PNG + rooms | `narwal-local map` | `[--out-dir DIR]` |
| Live telemetry window | `narwal-local watch` | `--duration SECONDS` (required, ≤300) |
| Diagnose connection | `narwal-local doctor` | — |
| Pause / resume | `narwal-local pause` / `resume` | `--yes [--dry-run]` |
| Force stop | `narwal-local stop` | `--yes [--dry-run]` (slow) |
| Return to dock | `narwal-local dock` | `--yes [--dry-run]` |
| Whole-house clean | `narwal-local start` | `--yes [--dry-run] [--mode] [--fan] [--water] [--passes]` |
| Selected-room clean | `narwal-local clean` | `--rooms ID[,ID...] --yes [--dry-run]` |

Control commands require `--yes`; `--dry-run` builds the payload and sends no control topic. Unless `--no-audit`, each write appends a line to `control_audit.jsonl`. Only one connection per source IP is allowed — do not run commands in parallel.

## Execution Parameters & Safety Bounds

Numeric options reject non-finite or out-of-range values before connecting. Coordinate offsets may be negative within their stated range:

| Option | Allowed Range | Default | Notes |
|---|---|---|---|
| `--budget` | `(0, 120]` seconds | `45.0` | Total budget for setup, connection, and queries. |
| `--query-timeout` | `(0, budget]` seconds | `8.0` | Timeout per serial query. Closes socket on timeout. |
| `--listen-seconds` | `[0, budget]` seconds | `3.0` | Broadcast listen window for `snapshot` and `map`. |
| `--duration` | `(0, 300]` seconds | *None (required)* | Mandatory streaming duration for `watch`. |
| `--grid-offset-x/y` | `[-1000000, 1000000]` | `0.0` | Manual coordinate alignment offset. |
| `--render-scale` | `1..8` (integer) | `3` | Pixels per grid cell for rendered PNG. |

## Interpreting Output & Telemetry Semantics

The CLI writes one JSON envelope to `stdout` (`schema_version: "1.0"`) and diagnostic progress to `stderr`.

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "observed_at": "2026-10-06T19:00:00.123456Z",
  "device": {
    "model": "Narwal Flow 2",
    "product_key": "QxMSPG6VSO",
    "device_id": "test-device-id-001",
    "firmware_version": "v01.09.10.02"
  },
  "data": {
    "base_query": {
      "source": "status/get_device_base_status",
      "battery_percent": 100.0,
      "mode": { "raw": {"1": 1}, "code": 1, "semantic": "standby", "stuck_inferred": false, "substage": null },
      "charging_state": null, "is_docked": null,
      "fault_codes": null, "healthy": null, "health": "unknown"
    },
    "task": {
      "area_m2": null, "elapsed_seconds": null,
      "progress": { "raw": null, "progress_percent": null, "normalization": "absent", "ambiguous": false, "source_field": "1" }
    }
  },
  "warnings": [],
  "errors": [],
  "artifacts": []
}
```

### Critical Telemetry Guidelines
1. **Never Assume Absent Fields Mean Healthy:** Base views yield null fault codes, null healthy and unknown health for absent data. This build never produces a positive healthy assessment, even from an empty list.
2. **Observed Zero vs. Missing Data:** An observed `0` or `0.0` (such as 0 elapsed seconds or 0 area at task start) is a legitimate measurement and is preserved. Only omitted, unpolled, or unsupported fields are emitted as `null`.
3. **Working Mode 5:** Raw mode 5 is emitted as semantic `"cleaning_alt"` with `stuck_inferred: false`. It cannot be assumed to be drying, maintenance, or stuck from the enum code alone.
4. **Docked and Charging State:** `is_docked` and `charging_state` are always null. Raw values are in `dock_raw` and `charging_status_raw`.
5. **Working Status Field 13:** It is `station_bag_dry_total_seconds`, not area or mop washing duration. Field 2 yields `data.task.area_m2`.
6. **Task Progress Is Not ETA:** `data.task.progress.progress_percent` is not remaining time. Flow 2 keys use reported `(1, 100]` and exact 0 as percent; `(0, 1]` remains ambiguous. Other models retain raw progress without normalization.
7. **Coordinate Calibration:** Marker placement uses provisional heuristics (`grid` policy default). Output explicitly states `calibration: "not_calibrated"`.
8. **Map Download Time vs. Creation Time:** The map artifact timestamp records when the map payload was transferred from the robot; it does not indicate when the floor plan was initially mapped.

## Exit Codes

| Code | Name | Description |
|---|---|---|
| `0` | `EXIT_OK` | Command completed without recorded errors; telemetry may still be missing. Inspect warnings and null fields. |
| `2` | `EXIT_USAGE` | Invalid CLI arguments, non-finite bounds, missing host/product key, or a control command missing `--yes`. |
| `3` | `EXIT_PARTIAL` | Partial success; basic identity retrieved or map image saved, but secondary queries or position sampling timed out. |
| `10` | `EXIT_TRANSPORT` | TCP connection refused, host unreachable, or WebSocket handshake failed. |
| `11` | `EXIT_QUERY` | Query timeout, rejected response code, or budget exceeded. |
| `12` | `EXIT_DECODE` | Protobuf payload decode error or frame corruption. |
| `13` | `EXIT_ARTIFACT` | File system error writing a PNG or JSONL artifact. An audit-write failure is a non-fatal warning and preserves the command exit code. |
| `20` | `EXIT_COMMAND` | A control command reached the robot and the robot declined it (`NOT_APPLICABLE`, `CONFLICT`, `NOT_READY`). |

## Known Protocol Quirks & Genuine Pitfalls

- **Empty-Topic `field5` Replies:** Responses to query requests arrive with an empty topic string (`""`). The client cannot correlate responses by topic. The library enforces a strict serial queue (one outstanding query at a time) and resets the WebSocket connection on timeout to purge stale replies.
- **Subscription Replies:** Consumed before subsequent queries. Accepted shapes are known integer result codes or a narrowly checked Flow 2 configuration echo; an arbitrary dictionary is not a success response.
- **Quiet Telemetry Is Not Proof of Sleep:** Silence does not establish sleep, disconnection, or absence of a task. Report observations rather than guessing their cause.
- **Fault-Code Shapes:** The parser accepts nested ErrorCode messages as well as integer/packed variants. `AC 02` is a synthetic packed-varint test for code 300, not proof of this robot's fault wire format. Fault-state hardware verification remains pending.
- **Map Resolution:** Map grid resolution is measured in millimeters per cell. Grid bounding-box area is not living area.
- **Private Artifact Isolation:** Rendered map PNGs and raw JSONL recordings contain private home geometry. Keep all artifacts inside `artifacts/` (gitignored). Never upload raw captures or floor plan images to public channels.

Response-shape validation closes the connection on an unrelated payload. Unsolicited replies also invalidate a session. With no request ID, a delayed duplicate that has exactly the expected schema is still indistinguishable; do not claim the protocol provides authenticated request/result correlation.

## Agent Skill Installation

To register this skill in an agent workspace:
1. Provide the repository URL or local checkout path to the AI coding agent.
2. The agent reads the target workspace's `AGENTS.md` or `CLAUDE.md`, then follows routing instructions and any skill index.
3. The agent registers exactly **one** root skill entry pointing to `skills/narwal/SKILL.md`. Sub-references remain internal.
4. Device IP addresses and credentials reside in private local configuration (`.env`), not inside the skill documentation.

Argparse syntax errors and help/version are exceptions to JSON stdout: syntax errors print usage on stderr and exit 2 with no JSON. Inspect exit code and stderr when stdout is empty.
