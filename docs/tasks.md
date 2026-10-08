# Task & Judgment Guide — Narwal Local Skill

**Reader:** anyone using `narwal-local` — developers, automation scripts, and AI agents acting on their behalf.
**What it solves:** the CLI exposes low-level primitives (`snapshot`, `watch`, `pause`, `start`, …). This page fills the gap between those primitives and real tasks: how to read the fields into "what is it doing right now", how to compose a primitive sequence for a task, and which tasks this tool **cannot** do.

The full primitive list, payloads, exit codes, and coordinate policy live in `docs/control.md`, `README.md`, and `skills/narwal/references/output-contract.md`. This page does not repeat them.

---

## 1. Start with the Picture: Verbs, Not Tasks

Each `narwal-local` subcommand maps to one protocol action or one query. One invocation does one thing, then disconnects. It keeps no state. It does not know when the last clean was, why the robot stopped yesterday, or anything about a prior run. Every judgment is made at the moment of the call, from the JSON that call returns.

That yields the rule that runs through this whole page: **the robot is the authority; the CLI is a messenger.** You can read its state and ask it to act, but it decides whether to accept. Any assumption that "I sent it, therefore it happened" is wrong.

Three gaps follow, and they recur:

- **No history.** "Why did it stop before I got back" cannot be answered from one snapshot. Either open a bounded window (`watch`) to see it live, or read the App's task history.
- **No stuck verdict.** The protocol has no clean error bit. Mode 5 is ambiguous (see §2), and absent broadcasts do not prove sleep. Judgment comes from cross-checking fields, not from one signal.
- **No dock-maintenance commands.** Wash mop, dry mop, dust gathering, and the like are on the permanent deny list (§4). This tool does not send them.

---

## 2. Judgment: How to Tell What It Is Doing

One `snapshot` returns identity, battery, mode, task telemetry, and a short broadcast listen. Reading those into conclusions depends on the table below; the notes after it list the fields that must **not** be trusted.

| What you see | What you can conclude | What you cannot |
|---|---|---|
| `mode.semantic` = `standby` | Idle or mid-transition, no active task | Cannot separate idle from "just finished, not yet docked" |
| `mode.semantic` = `cleaning` / `cleaning_v2` / `custom_cleaning` | Actively cleaning | — |
| `mode.semantic` = `cleaning_alt` (raw 5) | Active, **maybe** cleaning, **maybe** stuck | Cannot call it stuck from the mode alone; watch position (see §3.2) |
| `mode.semantic` = `remapping` (raw 7) | Mapping/exploring; camera is available | — |
| `task.area_m2` rising, `task.elapsed_seconds` rising | The task is progressing | `area_m2` is cleaned-this-session, not floor area |
| `task.progress.progress_percent` | The reported completion percent | **Not remaining time, not an ETA** |
| `base_query.battery_percent` | A battery reading | Not whether it is charging vs charged |

### Fields not to trust blindly

**`is_docked` and `charging_state` are always `null`.** By design: on this firmware those raw codes vary by version and the tool does not interpret them for you. Raw codes are in `dock_raw` and `charging_status_raw`. To tell whether it is docked or charging, use the **battery trend** (rising across a `watch` window, or between two snapshots) and historical position — not these fields.

**`dock_raw` field 11 / 47 values vary by firmware.** The `2` / `3` values in the upstream notes come from upstream captures; a locally observed frame can show `11=1, 47=2`, which does not match that table while the battery is still recovering. Treat the dock raw codes as a hint, never a verdict.

**Mode 5 (`cleaning_alt`) is not proof of being stuck.** Upstream observed it while the robot was physically stuck, but the same mode can be a normal clean. Judging stuck requires looking at position separately.

**`task.station_bag_dry_total_seconds` is not cleaned area.** It is the station dust-bag drying timer (observed constant at 18000 = 5 h). Area is only in `task.area_m2`.

**`progress` percent is ambiguous when unnormalized.** On Flow 2, `(1, 100]` and exact 0 are treated as percent; `(0, 1]` is flagged `ambiguous`. Other models keep the raw value with no conversion.

---

## 3. Task Recipes

Each task states its intent, preconditions, primitive sequence, interpretation points, and what to do when things differ. The sequence is the **default path**, not a script — the robot is the authority, so handle a decline per §5.

### 3.1 Confirm it is OK right now

One command:

```bash
narwal-local snapshot --env-file .env
```

Read `base_query.mode` and `task`: `standby` with null task fields means idle; a cleaning mode with `area_m2` / `elapsed_seconds` set means actively cleaning. A `no_broadcast` warning only says this short window saw no broadcast; **it does not** mean it is asleep or offline (§5).

To be more sure it is really moving and not parked at one spot, use §3.2.

### 3.2 Why did it stop / is it stuck

A single snapshot cannot answer "why"; it can only answer "is it moving right now". To see movement, open a bounded live stream:

```bash
narwal-local watch --env-file .env --duration 20 --out-dir .local/artifacts
```

Compare `map/display_map` `position.raw_x/raw_y` across the JSONL samples: changing means moving. Repeated samples at the same point, or `lost_context: true`, is when stuck becomes plausible. **Note `calibration` is always `not_calibrated`** — coordinates are provisional relative positions, usable only to tell "did it move", never for absolute location.

To recover while it is stuck, use the intervention commands in §3.4. To ask "how did it stop last time", read the App's task history — this tool has none.

### 3.3 Start a clean

Two forms: whole house and selected rooms, both via `clean/start_clean`.

```bash
# Whole house: enumerate every room on the active map
narwal-local start --env-file .env --yes

# Selected rooms (ids come from the current map)
narwal-local clean --env-file .env --rooms 3,5 --mode vacuum_and_mop --yes
```

Precondition: **`clean/start_clean` only applies while the robot is docked**; otherwise it answers `NOT_READY` (exit 20). If unsure about parameters, run `--dry-run` first: it reads the map and puts the topic and payload hex in the JSON, but **sends no control topic**.

Interpretation: room ids and `map_id` come from the current `get_map`, not config. Room ids are map numbers; the tool never maps `room-1` to "Living room" for you — that is exactly the kind of guess that goes wrong.

### 3.4 Intervene: pause / resume / stop / dock

| Intent | Command | Topic sent |
|---|---|---|
| Pause the current task | `pause` | `task/pause` |
| Resume | `resume` | `task/resume` |
| Force-stop the current task | `stop` | `task/force_end` |
| Send the robot to its dock | `dock` | `supply/recall` |

```bash
narwal-local pause  --env-file .env --yes
narwal-local resume --env-file .env --yes
narwal-local stop   --env-file .env --yes
narwal-local dock   --env-file .env --yes
```

Interpretation: `stop` is a **force stop** — the robot physically stops before answering, so it is slow (the tool waits up to 15 s internally). Do not read the wait as failure and do not re-send. `dock` sends the robot home; on arrival it runs its own wind-down, so you do not stack a `stop` after it. To stop a **dock-side action already in progress** (for example, while drying), this tool has no command — see §4.

### 3.5 Observe for a while

To watch a trend over minutes (is the battery charging, is the task progressing), use `watch`. `--duration` is required and capped at 300 s; run it again for a longer view. It writes one JSONL line per broadcast to `--out-dir`. This is the only way to get "a stretch of time" — there is no background daemon, so nothing exists outside the window.

---

## 4. Dock Maintenance: This Page Is Empty

Washing the mop, drying the mop, dust gathering, and the station ambient light map to upstream topics `supply/wash_mop`, `supply/dry_mop`, `supply/dust_gathering`, `supply/ambient_light_ctrl`, and others. All of them are on the **permanent deny list**: no flag sends them, and `require_send_topic` rejects them outright.

That is deliberate, not an omission. These actions drive the dock's water path and heater; a mistaken trigger (for example, re-running a mop wash while the drain is backed up, which can directly stall the robot) costs more than "one tap from a distance" saves. Starting and intervening already cover the main recovery cases; maintenance stays in the App.

So a task like "recall to dock, then wash the mop" is only half doable here (the `dock` half); the other half is in the App.

---

## 5. Failure Handling and Exit Codes

A control command's exit code is not black and white:

- `0` success (`status: ok`).
- `20` the command reached the robot and the **robot declined** (`NOT_APPLICABLE` / `CONFLICT` / `NOT_READY`). That is a normal answer, not a bug. **Do not retry in a loop** — a decline usually means a precondition is unmet (e.g. starting while not docked), and a retry just gets declined again.
- `11` timeout (`stop` is slow by design; only a wait well past 15 s is a real timeout).
- `10` transport failure (cannot connect, handshake failed).
- `12` response shape unrecognized; the tool closes the connection.
- `2` bad arguments, missing `--yes`, or a room id that is not on the map.

Two general rules: **every control command needs `--yes`** (without it nothing is sent and it exits 2); when unsure, `--dry-run` first. Unless `--no-audit`, each write appends a line to `control_audit.jsonl` under `--out-dir` (time, action, topic, payload, result code, outcome); an audit-write failure only adds an `audit_write_failed` warning and does not change the command's own exit code.

`map` and `watch` additionally use `3` (`partial`) and `13` (artifact write failure). The full table is in `README.md` and `skills/narwal/references/output-contract.md`.

One connection per source IP is allowed. A second connection makes the robot drop the first (`connection with same ip, close old one`). Do not run multiple `narwal-local` commands in parallel.

---

## 6. Known Product Gaps

These are boundaries, not bugs. Stating them keeps readers from assuming the tool does more.

| Gap | Impact | Workaround |
|---|---|---|
| No persistent history | Cannot explain a past stop | App task history, or a live `watch` |
| No stuck verdict layer | Judgment is cross-field | §3.2 position sampling |
| No dock-maintenance commands | Wash/dry/dust cannot be sent | App |
| No charging field | `is_docked` / `charging_state` always null | Battery trend |
| Coordinates uncalibrated | Only relative movement is meaningful | Do not use for absolute location |
| No room aliases | Only map room ids | Read `rooms` from `map`; never guess a household name |

---

## 7. How This Relates to the Other Docs

- Commands, parameters, payloads, exit codes: `README.md`, `docs/control.md`.
- Protocol quirks and the JSON contract: `skills/narwal/references/output-contract.md`.
- Task decisions and interpretation rules for agents: the playbook in `skills/narwal/SKILL.md`.
- Model compatibility and network: `skills/narwal/references/compatibility.md`.
