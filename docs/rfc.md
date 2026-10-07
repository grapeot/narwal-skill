# Technical Architecture & Protocol Design (RFC) — Narwal Local Skill

**RFC Identifier:** RFC-20261006-NARWAL-LOCAL
**Status:** Implemented & Verified
**License:** MIT

---

## 1. System Architecture

`narwal-local` is a standalone, read-only Python CLI and library providing direct WebSocket communication with Narwal robot vacuums on TCP port 9002.

```text
+-------------------------------------------------------------+
|                     narwal-local CLI                        |
|        (snapshot / map / watch / doctor subcommands)        |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
|                   narwal_skill Library                      |
|  +-------------------------+   +-------------------------+  |
|  |   Telemetry Normalizer  |   |   Map Renderer (Pillow) |  |
|  | (null vs 0, packed var) |   | (grid decode, PNG write)|  |
|  +-------------------------+   +-------------------------+  |
|  +-------------------------------------------------------+  |
|  |           Session Manager & Protocol Runner           |  |
|  | - Single-outstanding serial request queue             |  |
|  | - Empty-topic response matcher (field5)               |  |
|  | - Socket reset on query timeout (purges late replies) |  |
|  | - Send allowlist gatekeeper (6 read-only topics)      |  |
|  +-------------------------------------------------------+  |
+------------------------------+------------------------------+
                               | ws://<host>:9002
                               v
+-------------------------------------------------------------+
|             Narwal Robot Vacuum (Local Network)             |
+-------------------------------------------------------------+
```

---

## 2. Upstream Code Reuse & Provenance

This project vendors protocol models, constants, and map rendering helpers from [`sjmotew/NarwalIntegration`](https://github.com/sjmotew/NarwalIntegration) at commit `867706aa352729bd5367c258d4fd42de7cfd19a1` (MIT License, Copyright 2026 sjmotew):
- Extracted modules: `protocol.py`, `const.py`, `models.py`, `map_renderer.py`.
- **Decoupled from Home Assistant:** Zero dependencies on `homeassistant.*`.
- **Excluded Control Logic:** Upstream actuation methods (`start`, `clean_area`, `stop`, `motion`, `dock`, `camera`) are strictly excluded.
- **Attribution:** Preserved in `NOTICE` and package data `src/narwal_skill/vendor/NOTICE`.

---

## 3. Protocol Framing & Critical Quirks

### 3.1. Framing
Frames contain four header bytes: `0x01`, topic-byte-length + 2, `0x22` (request/broadcast) or `0x2a` (response), and topic-byte-length. A UTF-8 topic and protobuf payload follow. Response topics are empty.

### 3.2. Empty-Topic Replies (`field5`)
Response frames are marked field5 (`0x2a`) and have an empty topic; their payload contains the result. To prevent misattribution:
1. **Strict Serial Queue:** Exactly one outstanding query is permitted at any given time.
2. **Socket Reset on Timeout:** When a query times out, the client immediately closes the WebSocket connection and reconnects. This purges any late responses buffered in TCP or socket queues before the next query is dispatched.
3. **Subscription Replies:** Field5 subscription responses are consumed before subsequent queries. The parser accepts known integer success codes and a Flow 2 configuration echo containing numeric publication entries, a numeric field 2 and firmware text. Echo units are not interpreted; it is not accepted as base/map/feature query data.

Payload schemas are checked per query, and mismatches/unsolicited replies invalidate the connection. The protocol has no request ID, so same-schema delayed duplicates remain indistinguishable. There is no universal guarantee against all possible duplicate replies.

### 3.3. Send Topic Allowlist
The library enforces a strict allowlist in `allowlist.py` before any binary frame is built:
- `common/get_device_info`
- `status/get_device_base_status`
- `map/get_map`
- `common/get_feature_list` (snapshot `--with-features` only)
- `common/active_robot_publish`
- `status/app_status_heartbeat`

All actuation, motion, recall, maintenance, and raw-send endpoints are blocked.

---

## 4. Telemetry & Map Pipelines

- **Telemetry Normalization:**
  - Float32 battery level; non-finite floats serialized as `null`.
   - Fault codes accept nested ErrorCode messages and packed/integer variants; raw data and decode errors are retained. Synthetic packed `AC 02` is code 300, not a verified fault capture. Absent data yields null fault codes and unknown health.
   - Mode 5 is `cleaning_alt` with `stuck_inferred: false`. Docked and charging interpretations remain null; raw codes are separately preserved.
  - Tag 13 is `station_bag_dry_total_seconds` (station bag drying timer in seconds), **not** cleaned area. Cleaned area is parsed only from task field 2.
- **Map & Coordinates:**
  - Decodes binary occupancy grid; rasterizes PNG with room segments and obstacle layers.
  - Coordinate policies: `grid` (default), `upstream`, `raw`. All outputs set `calibration: "not_calibrated"`.
  - Grid resolution is in millimeters per cell. Bounding-box area is not living area.

---

## 5. Doctor Diagnostics Sequence

1. **TCP Reachability:** Connects to port 9002 within remaining budget. Closed port exits with code 10.
2. **WebSocket Handshake:** Performs HTTP 101 protocol upgrade.
3. **Device Identity Query:** Sends `common/get_device_info`; validates non-empty product key and device ID bytes. Ping frames alone are **never** treated as application success.
4. **Base Status Query:** Validates `status/get_device_base_status` payload shape.
