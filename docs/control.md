# Control Extension Design — Narwal Local Skill

**Document Version:** 1.0.0
**Status:** Implemented and tested; `--dry-run` smoke checked on a Flow 2. Control topics are not hardware validated here.
**Scope:** Opt-in, explicitly confirmed write commands layered on the read-only CLI

---

## 1. Motivation

The read-only CLI answers "what is the robot doing". It cannot answer "the robot is stuck / finished / idle — do something". The concrete trigger: the robot stopped mid-task after a drain blockage, the operator fixed it, and wanted to resume without walking to the app. Intervention (pause / resume / stop / dock) is the high-value path; starting a clean is the natural companion.

This document adds a **second, opt-in mode** to the tool. The default stays read-only and the existing commands are unchanged. Write commands live behind an explicit switch, an explicit per-invocation confirmation, and an audit trail.

---

## 2. Scope

### In scope (layers A and B)

| Layer | Command | Topic | Payload |
|---|---|---|---|
| A intervention | `pause` | `task/pause` | empty |
| A intervention | `resume` | `task/resume` | empty |
| A intervention | `stop` | `task/force_end` | empty (slow: robot stops before answering) |
| A intervention | `dock` | `supply/recall` | empty |
| B start | `start` (whole house) | `clean/start_clean` | CleanTask over every map room |
| B start | `clean --rooms` | `clean/start_clean` | CleanTask over selected rooms |

### Explicit non-goals (permanently forbidden)

`common/reboot`, `common/shutdown`, `common/yell`, `common/notify_app_event`, camera commands (`developer/take_picture`, `developer/led_control`, `developer/get_robot_debug_image`), `developer/ping`, live parameter mutation (`clean/set_fan_level`, `clean/set_mop_humidity`), dock maintenance (`supply/wash_mop`, `supply/wash_mop_by_robot_status`, `supply/dry_mop`, `supply/dry_dust_bag`, `supply/dry_station_bag`, `supply/dust_gathering`, `supply/ambient_light_ctrl`), `task/cancel`, `clean/easy_clean/start`, `clean/plan/start`, and any arbitrary raw command injection.

---

## 3. Architecture: two modes, one transport

```
narwal-local (read-only default)            narwal-local <control> --yes
        |                                          |
        v                                          v
  ReadOnlySession(allow_control=False)      ReadOnlySession(allow_control=True)
        |                                          |
   require_send_topic(read)                 require_send_topic(control)
        |                                          |
   validate_query_response                validate_control_response
```

- The session class keeps one outstanding field5 request, closes the socket on timeout, and consumes subscription acks exactly as before. Control commands reuse that machinery; only the allowlist gate and the response validator differ.
- `ReadOnlySession.allow_control` defaults to `False`. A control topic sent through a default session raises `ReadOnlyViolation`. The read-only guarantee is enforced in code, not documentation.

### Send allowlist split

- `ALLOWED_SEND_TOPICS` — the six read/query topics (unchanged).
- `CONTROL_SEND_TOPICS` — the five control topics above.
- `FORBIDDEN_SEND_TOPICS` — everything in the non-goals list; never sent under any flag.

`require_send_topic(short, control=False)`:
1. reject anything in `FORBIDDEN_SEND_TOPICS`;
2. allow anything in `ALLOWED_SEND_TOPICS`;
3. allow anything in `CONTROL_SEND_TOPICS` only when `control=True`;
4. otherwise reject.

---

## 4. Command payloads

Source of truth: the pinned upstream control client
`sjmotew/NarwalIntegration` @ `867706aa352729bd5367c258d4fd42de7cfd19a1`
(`narwal_client/client.py`, `start_rooms` / `_build_start_clean_payload`). The
control client itself is **not** vendored; this project re-implements only the
payloads it needs and keeps the upstream attribution in `NOTICE`.

### Empty-payload commands

`task/pause`, `task/resume`, `task/force_end`, `supply/recall` take an empty
protobuf body.

### CleanTask payload (`clean/start_clean`)

```text
StartClean_Request { 1: CleanTask }
CleanTask    { 1: map_id, 2: [CleanItem...], 3: {} TaskOption, 5: taskType }
CleanItem    { 1: ZoneOption{1: 1, 2: room_id}, 2: CleanParam, 3: order }
CleanParam   { 1: mode, 2: fan, 3: mop_strength, 4: water, <pass tag>: passes }
```

- `room_id` comes from the active map (`get_map` field 2.1 for `map_id`, room
  records for ids). There is no room enumeration without a map fetch.
- `taskType` is the work mode value for a uniform task (`1` vacuum, `2` mop,
  `3` vacuum-then-mop, `4` vacuum-and-mop).
- `CleanParam.mode` and the pass tag are derived from the work mode so they
  cannot drift: vacuum→(2, tag 5), mop→(3, tag 6), vacuum-then-mop→(5, tags
  5+6), vacuum-and-mop→(4, tag 7).
- Repeated `CleanTask.2` is emitted once per room. A single room is a
  one-element repeated field, which is wire-equivalent to the collapsed form
  the upstream builder uses.
- `clean/start_clean` only applies while the robot is on the dock; otherwise
  the robot answers `NOT_READY (4)`. This project does not gate on our own
  uncertain dock inference — the robot arbitrates (see §6).

---

## 5. Response contract

Control responses are field5 frames whose field 1 is a result code:

| Code | Name | Meaning |
|---|---|---|
| `1` | `SUCCESS` | Applied |
| `2` | `NOT_APPLICABLE` | Valid but nothing to act on |
| `3` | `CONFLICT` | Already in that state |
| `4` | `NOT_READY` | e.g. `clean/start_clean` while not docked |
| `6` | `APPLIED` | Applied (observed on some commands) |
| dict echo | — | Room-clean config echo; treated as success |

Anything else (bool, unknown int, a string, or an empty body) closes the socket
and raises `DecodeError`. This is intentionally stricter than the upstream
control client, which maps any non-int field 1 to success; here an unrecognized
shape is not treated as applied. A non-empty response topic is rejected exactly
as for queries.

Outcome mapping:

- code `1`/`6`/echo → envelope `status: ok`, exit `0`.
- code `2`/`3`/`4` → the command was delivered and answered, but the robot
  declined. Envelope `status: failed`, exit `20` (`EXIT_COMMAND`), error code
  `command_not_applied`, and the numeric code in `data.result`.
- timeout → exit `11`; transport/close → exit `10`.

---

## 6. Preflight, confirmation, audit

**Preflight (informational, never authoritative).** Before sending, the tool
queries base status and records a normalized summary in `data.preflight`. It
does not block on our own dock/charge inference, because those raw fields are
firmware-dependent and the local build keeps them as raw codes. The robot is
the authority: it answers `NOT_READY`/`CONFLICT` when the state is wrong.

**Confirmation.** Every control command requires `--yes`. Without it the tool
exits `2` with a usage error and sends no control topic. `--dry-run` opens a
read session, fetches identity/status (and, for `start`/`clean`, the active map)
to build the payload, records the topic and payload hex in the JSON envelope,
sends no control topic, and exits `0`; it does not require `--yes`.

**Audit.** Unless `--no-audit`, one JSONL line is appended to
`<out-dir>/control_audit.jsonl` (default `artifacts/`, gitignored): UTC time,
action, short topic, full topic, payload hex, result code, outcome. The path is
returned in `artifacts`. If the audit write fails, the command outcome is
preserved and a `audit_write_failed` warning is added instead.

---

## 7. CLI surface

```
narwal-local pause     [connection] --yes [--dry-run] [--out-dir DIR] [--no-audit]
narwal-local resume    [connection] --yes [--dry-run] [--out-dir DIR] [--no-audit]
narwal-local stop      [connection] --yes [--dry-run] [--out-dir DIR] [--no-audit]
narwal-local dock      [connection] --yes [--dry-run] [--out-dir DIR] [--no-audit]
narwal-local start     [connection] --yes [--dry-run] [--mode M] [--fan F] [--water W]
                                   [--passes N] [--out-dir DIR] [--no-audit]
narwal-local clean     [connection] --rooms ID[,ID...] --yes [--dry-run] [same options]
```

- `--mode`: `vacuum` | `mop` | `vacuum_then_mop` | `vacuum_and_mop` (default `vacuum_and_mop`).
- `--fan`: `mute` | `normal` | `strong` | `deep` | `super` (default `normal`).
- `--water`: `dry` | `normal` | `wet` (default `normal`).
- `--passes`: integer `1..3` (default `1`).
- `--rooms`: comma-separated robot room ids; validated against the active map.

`stop` uses the longer force-end budget (robot physically stops before
answering). All control commands still share the command `--budget`; `stop`
defaults its internal query timeout higher.

---

## 8. Privacy

- Control introduces no new personal data class. Payloads carry only model-level
  product keys, the per-device id (already required for addressing), map id, and
  room ids — all already present in read paths.
- Audit logs live in gitignored `artifacts/` (or the operator-chosen `--out-dir`).
  They contain the device id in the topic string, so they stay local like map
  PNGs and watch JSONL.
- No credentials, addresses, or home geometry are added to any public file.

---

## 9. Testing

1. **Unit (`tests/unit/test_control_payload.py`)** — byte-exact CleanTask
   encoding against a hand-built expected payload; empty-payload commands;
   mode/fan/water/passes validation; `require_send_topic` control gating.
2. **Loopback (`tests/loopback/test_control.py`)** — fake robot answers result
   codes `1/4/3/echo/unknown`; assert exit codes, envelope status,
   `command_not_applied`, `--dry-run` sends no control topic while still reading
   identity/status/map, `--yes` gating, and the
   audit line.
3. **Regression** — the read path is unchanged; `test_allowlist_source` is
   updated to assert control topics appear only in `control.py` and are refused
   without the control flag.
4. **Hardware** — manual, opt-in. Flow 2 firmware `v01.09.10.02`; start with
   `--dry-run`, then a live `pause`/`resume`/`dock`, then `start`.

---

## 10. Compatibility

Hardware validation stays scoped to the Narwal Flow 2. Control payloads inherit
upstream community research for other port-9002 models but are **not** hardware
validated here. The command topics are the same upstream "confirmed working"
set; the CleanTask schema is the newer (post-`v01.07.22`) form used by the pinned
upstream `start_rooms` path.
