---
name: narwal-local
description: Read-only status inspection, floor plan maps, bounded live telemetry, and connection diagnostics for port-9002 WebSocket Narwal robot vacuums. Flow 2 smoke tested; other compatible Flow/Freo models are candidates.
---

# Narwal Local Skill

Provide AI coding and automation agents with deterministic, read-only local access to Narwal robot vacuums over the local WebSocket protocol on TCP port 9002 without Home Assistant or vendor cloud dependencies.

## Goal & Acceptance Criteria

When invoked, the agent executes `narwal-local` subcommands to observe device state and returns structured findings. A task is considered complete when:
1. **Valid JSON Output:** The CLI emits a single versioned JSON envelope (`schema_version: "1.0"`) to `stdout` with `status: "ok"` or `"partial"`, or clean error categorization on `"failed"`.
2. **Freshness Verification:** The `observed_at` UTC timestamp reflects the current command run.
3. **Null vs. Zero Semantic Integrity:** Telemetry metrics that are unpolled, unavailable, or unsupported by the model remain explicit JSON `null`, never numerical `0` or empty strings.
4. **Artifact Verification:** For `map`, the PNG exists and its dimensions equal grid dimensions times render scale. For `watch`, the JSONL exists and any records parse; an empty recording is possible and means no telemetry observed.

## Capability Scope & Safety Boundaries

- **Transport:** Local WebSocket connection directly on TCP port 9002 (`ws://<host>:9002`).
- **Hardware Validation Scope:** Direct physical hardware validation is strictly scoped to the **Narwal Flow 2** (firmware `v01.09.10.02` smoke tested). Other models inherit candidate status from upstream community protocol research, not local hardware certification.
- **Candidate Models (Upstream Reports):** Flow AX12, Freo Z10 Ultra (CX4), Freo Z10 Pro/Turbo (AX26), Freo X10 Pro (AX15), Freo 20, Freo 20 Edge, and Freo Z Ultra (CX7; requires explicit device ID, no broadcast).
- **Incompatible Models:** Models on other ports (Freo X Ultra uses ZeroMQ on port 6789), cloud-only models (Freo X Plus, J1/T10, J4), or models refusing port 9002 (standard Freo Z10).
- **Strictly Read-Only:** The CLI and library strictly enforce a send allowlist of 6 query and telemetry topics. Under no circumstances does this skill support motion, cleaning (`start`, `stop`, `pause`, `clean_area`), docking, rebooting, firmware update, map editing, or arbitrary raw command injection.

## Available Tools & Resources

- **CLI Executable:** `narwal-local` (available via the project virtual environment `.venv/bin/narwal-local` or PATH).
- **Subcommands:** `snapshot`, `map`, `watch`, `doctor`.
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

## Task-to-Command Decision Guide

Choose the appropriate subcommand based on the specific operational goal. Do not run rigid multi-step procedures when a single command answers the question:

| User Intent | Command | Key Flags & Behavior | Expected Outcome |
|---|---|---|---|
| Quick status, battery, or active clean check | `narwal-local snapshot` | `--host HOST [--product-key KEY] [--listen-seconds 3]` | Identity, battery, qualified mode and available task metrics; charging/docking interpretations remain unknown. |
| Inspect floor plan, rooms, or obstacle map | `narwal-local map` | `--host HOST [--product-key KEY] [--out-dir artifacts]` | Downloads active occupancy grid, writes a timestamped PNG (`artifacts/map_<utc>_<token>.png`), and returns room metadata. |
| Stream live telemetry during cleaning | `narwal-local watch` | `--host HOST [--product-key KEY] --duration SECONDS` | **Mandatory duration** (`0 < duration <= 300`). Writes structured telemetry to a unique JSONL artifact. |
| Troubleshoot connection or timeout errors | `narwal-local doctor` | `--host HOST [--product-key KEY]` | Sequential diagnostic probe: TCP reachability -> WebSocket handshake -> device identity query -> base status query. Ping alone is not treated as success. |

### Command Selection Rules
- **Default to `snapshot`:** Use it for battery and reported task status. The default listen is 3 seconds; setup and queries have a separate budget, not a guaranteed few-second response.
- **Use `map` only when a visual floor plan or room layout is needed:** Map retrieval downloads and rasterizes large grid payloads. Do not run `map` merely to check battery level.
- **`watch` requires an explicit, finite duration:** The `--duration` parameter is strictly required (maximum 300 seconds). Never attempt to stream indefinitely. Watch `--duration` runs after connection setup and is not deducted from the setup `--budget`.
- **Use `doctor` on connection failures:** If `snapshot` or `map` fails with exit code 10 or 11, run `doctor` to pinpoint whether the failure is at the TCP socket, WebSocket handshake, or application query layer.
- **Discovery Fallback:** If the product key is unknown on a Flow 2 device, pass `--discover` to probe up to 3 known Flow 2 product keys serially within the shared budget.

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
| `2` | `EXIT_USAGE` | Invalid CLI arguments, non-finite bounds, or missing host/product key. |
| `3` | `EXIT_PARTIAL` | Partial success; basic identity retrieved or map image saved, but secondary queries or position sampling timed out. |
| `10` | `EXIT_TRANSPORT` | TCP connection refused, host unreachable, or WebSocket handshake failed. |
| `11` | `EXIT_QUERY` | Query timeout, rejected response code, or budget exceeded. |
| `12` | `EXIT_DECODE` | Protobuf payload decode error or frame corruption. |
| `13` | `EXIT_ARTIFACT` | File system error writing PNG or JSONL artifact. |

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
