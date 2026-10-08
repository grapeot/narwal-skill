# Product Requirements Document (PRD) — Narwal Local Skill

**Document Version:** 1.1.0
**Status:** Implemented & Hardware Smoke Tested
**Scope:** Read-Only CLI (`narwal-local`) & Agent Skill, with an opt-in control mode

---

## 1. Problem Statement & Motivation

Users and automation developers who own Narwal robot vacuums require direct, scriptable access to device telemetry, maps, and status. Upstream solutions primarily integrate via Home Assistant, while the vendor mobile application utilizes local and cloud channels. AI coding agents and lightweight automation tools require a fast, deterministic local CLI that outputs clean JSON without external daemon dependencies. Beyond observation, operators need bounded intervention: the robot can finish, stall, or need a restart, and a scriptable pause / resume / stop / dock / start is the difference between remote recovery and walking to the appliance.

---

## 2. Goals & Non-Goals

### Goals
- **Direct Local WebSocket Transport:** Direct connection to `ws://<host>:9002` with zero cloud, broker, or Home Assistant dependencies.
- **Read-Only by Default:** Provide status queries, visual map extraction, and live telemetry listening without any actuation.
- **Opt-In Control:** Pause, resume, stop, dock, and start commands, reachable only from a session opened for control, behind an explicit `--yes` confirmation and an audit log.
- **Machine-Readable Envelope:** Versioned JSON document (`schema_version: "1.0"`) on `stdout` with human logs isolated to `stderr`.
- **Strict Null Safety:** Emit explicit JSON `null` for unpolled or unavailable metrics; preserve observed numerical zeros (`0` or `0.0`).
- **Bounded Execution:** Mandatory timeouts on all network operations, strict setup budget, and a required duration cap (`--duration`, max 300s) on streaming watch commands.
- **Decoupled Offline Verification:** Offline tests with loopback WebSocket mocks and failure injection.

### Non-Goals
- No permanent deny-list topics under any flag: reboot, shutdown, yell, camera/developer commands, live parameter mutation, dock maintenance, `task/cancel`, `clean/plan/start`, `clean/easy_clean/start`, or raw command injection.
- No map editing (no virtual walls or room renaming).
- No long-running background daemon, HTTP server, or MCP service.
- No support for non-9002 transports (Freo X Ultra ZeroMQ on 6789, Freo X Plus cloud-only, legacy J1/J4/T10).

---

## 3. Hardware Scope & Model Compatibility

- **Validated Hardware Target:** Narwal Flow 2 (verified via physical hardware smoke testing on firmware `v01.09.10.02`).
- **Candidate Models:** Narwal Flow (AX12), Freo Z10 Ultra (CX4), Freo Z10 Pro/Turbo (AX26), Freo X10 Pro (AX15), Freo 20, Freo 20 Edge, and Freo Z Ultra (CX7; requires explicit device ID).
- **Incompatible Models:** Freo X Ultra (ZeroMQ), Freo X Plus (cloud-only), Narwal J1/J4/T10, and standard Freo Z10 (port 9002 refused).

---

## 4. Functional Requirements

### FR-1: `snapshot`
Queries device identity, firmware version, battery level, operational working mode, and active task telemetry. Exits immediately upon completion. Unreported fields remain `null`.

### FR-2: `map`
Retrieves active floor plan data, decodes occupancy grid cells, renders a 2D visualization to disk (`artifacts/map_<utc>_<token>.png`), and emits structured room segmentation metadata.

### FR-3: `watch`
Subscribes to live broadcasts (`status/working_status`, `map/display_map`) for an explicitly required time window (`--duration <sec>`, maximum 300s). Records events into a unique JSONL artifact.

### FR-4: `doctor`
Runs sequential diagnostic checks: TCP socket reachability, WebSocket HTTP 101 handshake, device identity query, and base status query. Ping frames alone are not treated as application success.

### FR-5: Control commands (opt-in)
`pause`, `resume`, `stop`, `dock`, `start`, and `clean` are write commands. Each opens a session with `allow_control=True`, requires `--yes` (or `--dry-run` to preview), and appends one audit line unless `--no-audit`. `start` and `clean` fetch the active map for `map_id` and room ids. A robot decline (`NOT_APPLICABLE`, `CONFLICT`, `NOT_READY`) exits `20`, not as a transport or decode failure.

---

## 5. Output Specification & Exit Codes

Commands emit a single versioned JSON envelope (`schema_version: "1.0"`) containing `status` (`ok` | `partial` | `failed`), `observed_at`, `device`, `data`, `warnings`, `errors`, and `artifacts`.

- `0`: Operation succeeded (`ok`).
- `2`: Usage error, non-finite argument, missing configuration, or a control command without `--yes`.
- `3`: Partial success (`partial`).
- `10`: Network transport error (TCP/WebSocket).
- `11`: Query timeout, rejected response, or budget exceeded.
- `12`: Binary protobuf decoding failure.
- `13`: Artifact disk write error.
- `20`: Control command declined by the robot.

---

## 6. Acceptance Criteria

- **AC-1:** `snapshot` emits valid JSON with battery and firmware within timeout bounds. *(Verified)*
- **AC-2:** Unpolled/inapplicable telemetry emits JSON `null`, never `0` or empty strings. *(Verified)*
- **AC-3:** `map` outputs a valid PNG verified by Pillow and structured room segmentation metadata. *(Verified)*
- **AC-4:** `watch` enforces mandatory bounded duration and exits cleanly without lingering sockets. *(Verified)*
- **AC-5:** `doctor` accurately isolates network transport faults from query timeouts. *(Verified)*
- **AC-6:** The default session enforces a 6-topic read-only send allowlist; control topics are refused without `allow_control=True`. *(Verified)*
- **AC-7:** Control commands require `--yes`, support `--dry-run`, write an audit line, and exit `20` on a robot decline. *(Verified)*
