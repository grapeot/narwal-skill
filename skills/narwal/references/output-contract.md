# Output Contract & Telemetry Schema — Narwal Local Skill

Executed commands output one JSON envelope to stdout; diagnostics go to stderr. Argparse syntax errors exit 2 with stderr usage and empty stdout; help/version print text instead of JSON.

---

## 1. Top-Level JSON Envelope Schema

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "observed_at": "2026-10-06T19:00:00.123456Z",
  "device": {
    "model": "Narwal Flow 2",
    "product_key": "QxMSPG6VSO",
    "device_id": "fake-device-001",
    "firmware_version": "v01.09.10.02"
  },
  "data": {},
  "warnings": [],
  "errors": [],
  "artifacts": []
}
```

### Envelope Fields

| Field | Type | Description |
|---|---|---|
| `schema_version` | string | Constant `"1.0"`. |
| `status` | string | `"ok"` (all requested steps succeeded), `"partial"` (partial results obtained), or `"failed"`. |
| `observed_at` | string | ISO 8601 UTC timestamp with microsecond precision when the command executed. |
| `device` | object | Identified robot attributes (`model`, `product_key`, `device_id`, `firmware_version`). Unresolved fields are `null`. |
| `data` | object | Command-specific structured telemetry and operational data. |
| `warnings` | array | Non-fatal protocol anomalies (e.g. `broadcast_decode`, `position_sampling_failed`, `listen_budget_clipped`). |
| `errors` | array | Fatal or partial error objects with structured diagnostics. |
| `artifacts` | array | File system artifacts written by the command (e.g. PNG maps, JSONL recordings). |

---

## 2. Structured Error Object Schema

Errors are emitted in the `errors` array without stack traces:

```json
{
  "code": "query_timeout",
  "message": "query timed out after 8.0s",
  "exception_type": "QueryTimeout",
  "detail": "status/get_device_base_status timed out after 8.0s"
}
```

When the robot closes a connection due to another concurrent connection from the same IP, the preserved reason string appears in `detail`:
`"connection with same ip, close old one"`

---

## 3. Exit Codes

| Exit Code | Identifier | Trigger Conditions |
|---|---|---|
| `0` | `EXIT_OK` | Complete success (`status == "ok"`). |
| `2` | `EXIT_USAGE` | Invalid CLI options, non-finite float bounds (`NaN`, `inf`), or missing required connection parameters. |
| `3` | `EXIT_PARTIAL` | Result produced despite partial failures (e.g. identity query succeeded, but follow-up queries timed out; or map PNG was saved but position sampling timed out). |
| `10` | `EXIT_TRANSPORT` | TCP socket refused, host unreachable, or WebSocket HTTP 101 handshake failure. |
| `11` | `EXIT_QUERY` | Query timeout, rejected response payload, or overall setup `--budget` expired. |
| `12` | `EXIT_DECODE` | Binary protobuf decoding failure or frame framing error. |
| `13` | `EXIT_ARTIFACT` | Disk I/O error writing PNG or JSONL artifact. |

---

## 4. Telemetry Normalization Rules

### Null vs. Numerical Zero
- **Explicit `null`:** Telemetry metrics that are unpolled, unavailable, or unsupported by the vacuum model are emitted as JSON `null`.
- **Preserved `0` / `0.0`:** Observed numerical zeros (e.g., 0 elapsed cleaning seconds, 0 area covered at the start of a task, or exact 0% progress) are preserved as `0` or `0.0`.
- **Non-Finite Floats:** The normalizer converts non-finite values to null; `allow_nan=False` additionally prevents invalid JSON output.

### Battery & Health State
- **Battery Level:** Reported float32 in `data.base_query.battery_percent` or `data.base_broadcast.battery_percent`; normal hardware values are 0..100, not an independent physical measurement.
- **Fault Codes:** Accept nested ErrorCode dictionaries/lists (identity code at field 1), integers/lists, or packed bytes. Raw field data is preserved; unsupported shapes return unknown with `fault_decode_error`. Synthetic `AC 02` tests packed code 300; no real fault-state capture was obtained.
- **Health Determination:** Base views have `fault_codes`, `healthy` and `health` fields. Absent lists produce null/null/unknown; the build never positively asserts health.

### Working Mode Semantics
- Raw mode is in `data.base_query.mode.raw` or `data.base_broadcast.mode.raw`, with `code`, `semantic`, and `substage` alongside it.
- Mode `5` is mapped to semantic `"cleaning_alt"` with `stuck_inferred: false`. It cannot be definitively labelled as drying, maintenance, or stuck from the enum code alone.
- `is_docked` and `charging_state` are always null; raw fields are in `dock_raw` and `charging_status_raw`.

### Working Status Task Metrics
Working status values originate exclusively from `status/working_status`:
- **Progress:** `data.task.progress` contains raw value, `progress_percent`, `normalization`, and `ambiguous`. Flow 2 `(1, 100]` and exact 0 use the percent policy; `(0, 1]` remains ambiguous. Other models remain unnormalized.
- **Area (`data.task.area_m2`):** Device-reported square meters from field 2.
- **Elapsed Time (`elapsed_seconds`):** Task duration in seconds from field 3.
- **Field 13 (`station_bag_dry_total_seconds`):** Protobuf tag 13 is the station dust-bag drying timer in seconds. It is **never** interpreted as cleaned area or mop washing duration.

---

## 5. Map & Coordinate Specifications

### Coordinate Policies
When rendering robot and dock markers onto the occupancy grid PNG, `narwal-local` supports three policies:

| Policy | Transformation Formula | Description |
|---|---|---|
| `grid` (default) | `pixel = raw_coord - origin + offset` | Grid cell offset mapping. |
| `upstream` | `pixel = (raw_coord * 100 / resolution) - origin + offset` | Scaled millimeter transformation. |
| `raw` | *(no pixel transformation)* | Suppresses drawing position markers on the PNG. |

All coordinate outputs explicitly set `calibration: "not_calibrated"`.

### Map Metadata
- `resolution_mm_per_cell`: Grid resolution in millimeters per cell.
- `area_raw`: Static map area from field 33, with `area_unit: "unknown"`.
- `rooms`: Records with `room_id`, `name`, `label`, `room_type_code` and `room_type_enum`. Empty names become null and labels fall back to `room-{id}`.

---

## 6. Watch JSONL Event Specification

When running `narwal-local watch`, the CLI writes a line-delimited JSONL file (`artifacts/watch_<utc>_<token>.jsonl`).

Each `map/display_map` broadcast line includes:

```json
{
  "topic": "map/display_map",
  "observed_at": "2026-10-06T19:00:05.123456Z",
  "position": {
    "raw_x": 120.5,
    "raw_y": -45.2,
    "heading_rad": 1.57,
    "device_timestamp_ms": 1728241205000,
    "lost_context": false,
    "calibration": "not_calibrated",
    "units": "protocol_float"
  },
  "trajectory": {
    "count": 2,
    "points": [[120.5, -45.2], [118.0, -44.0]],
    "truncated": false,
    "calibration": "not_calibrated"
  }
}
```

- When `raw_x` and `raw_y` are 0 with timestamp 0 or absent, `lost_context` is set to `true`.
- The `--raw` flag appends a companion raw hex capture JSONL. Because raw captures and rendered maps contain private home floor plans, all watch recordings must remain in local private directories.
