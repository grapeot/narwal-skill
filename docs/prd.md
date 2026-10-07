# Product Requirements Document (PRD) — Narwal Local Skill

**Document Version:** 1.0.0
**Status:** Implemented & Hardware Smoke Tested
**Scope:** Read-Only CLI (`narwal-local`) & Agent Skill

---

## 1. Problem Statement & Motivation

Users and automation developers who own Narwal robot vacuums require direct, scriptable access to device telemetry, maps, and status. Upstream solutions primarily integrate via Home Assistant, while the vendor mobile application utilizes local and cloud channels. AI coding agents and lightweight automation tools require a fast, deterministic, read-only local CLI that outputs clean JSON without external daemon dependencies or risk of unintended physical vacuum movement.

---

## 2. Goals & Non-Goals

### Goals
- **Direct Local WebSocket Transport:** Direct connection to `ws://<host>:9002` with zero cloud, broker, or Home Assistant dependencies.
- **Strictly Read-Only:** Provide status queries, visual map extraction, and live telemetry listening without any actuation or state-modifying endpoints.
- **Machine-Readable Envelope:** Versioned JSON document (`schema_version: "1.0"`) on `stdout` with human logs isolated to `stderr`.
- **Strict Null Safety:** Emit explicit JSON `null` for unpolled or unavailable metrics; preserve observed numerical zeros (`0` or `0.0`).
- **Bounded Execution:** Mandatory timeouts on all network operations, strict setup budget, and a required duration cap (`--duration`, max 300s) on streaming watch commands.
- **Decoupled Offline Verification:** Offline tests with loopback WebSocket mocks and failure injection.

### Non-Goals
- No motion or cleaning commands (`start`, `stop`, `pause`, `clean_area`, `dock`).
- No raw command injection or arbitrary parameter mutations.
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

---

## 5. Output Specification & Exit Codes

Commands emit a single versioned JSON envelope (`schema_version: "1.0"`) containing `status` (`ok` | `partial` | `failed`), `observed_at`, `device`, `data`, `warnings`, `errors`, and `artifacts`.

- `0`: Operation succeeded (`ok`).
- `2`: Usage error, non-finite argument, or missing configuration.
- `3`: Partial success (`partial`).
- `10`: Network transport error (TCP/WebSocket).
- `11`: Query timeout, rejected response, or budget exceeded.
- `12`: Binary protobuf decoding failure.
- `13`: Artifact disk write error.

---

## 6. Acceptance Criteria

- **AC-1:** `snapshot` emits valid JSON with battery and firmware within timeout bounds. *(Verified)*
- **AC-2:** Unpolled/inapplicable telemetry emits JSON `null`, never `0` or empty strings. *(Verified)*
- **AC-3:** `map` outputs a valid PNG verified by Pillow and structured room segmentation metadata. *(Verified)*
- **AC-4:** `watch` enforces mandatory bounded duration and exits cleanly without lingering sockets. *(Verified)*
- **AC-5:** `doctor` accurately isolates network transport faults from query timeouts. *(Verified)*
- **AC-6:** Library strictly enforces a 6-topic read-only send allowlist with zero actuation methods. *(Verified)*
