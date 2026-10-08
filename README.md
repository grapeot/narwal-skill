# Narwal Local Skill

A lightweight, cloud-independent Python CLI (`narwal-local`) and agent skill for Narwal robot vacuums communicating over the local WebSocket protocol on port 9002.

The default mode is read-only. An opt-in control mode adds explicitly confirmed pause / resume / stop / dock / start commands.

No Home Assistant instance, no cloud accounts, and no proprietary vendor bridges required.

---

## Overview

**Narwal Local Skill** provides direct, scriptable local access to Narwal robot vacuums. Designed for developers, automation scripts, and AI coding agents, it queries robot telemetry, extracts visual maps with room segmentation, streams live status updates, and runs comprehensive connection diagnostics. An opt-in control mode adds a small, explicitly confirmed set of write commands for intervention and starting a clean.

### CLI Subcommands (`narwal-local`)

**Read-only (default):**

- **`snapshot`**: Single-shot query combining device identity, firmware version, battery level, operational working mode, and active cleaning task telemetry.
- **`map`**: Retrieves the active floor plan grid, renders a visual PNG artifact (`artifacts/map_<utc>_<token>.png`), and outputs structured room segmentation metadata.
- **`watch`**: Streams real-time telemetry updates for an explicitly bounded duration (`--duration <sec>`, required, max 300s) and writes a structured JSONL artifact.
- **`doctor`**: Runs sequential diagnostic checks across TCP port reachability, WebSocket handshake, device identity bytes, and base status queries.

**Control (opt-in, requires `--yes`):**

- **`pause` / `resume` / `stop` / `dock`**: Pause, resume, force-stop, or send the robot back to its dock.
- **`start`**: Start a whole-house clean over every room on the active map.
- **`clean --rooms ID,...`**: Start a clean for selected rooms.

Control commands require `--yes` and write an audit line unless `--no-audit` is passed. `--dry-run` builds the payload and records the topic and payload hex without sending a control topic.

For how to read the robot's state and compose these primitives into a task (and what the tool cannot do), see [`docs/tasks.md`](docs/tasks.md).

### Operational Safety Boundaries

- **Read-only by default:** The default session allows an allowlist of query topics only. Control topics are reachable only from a session opened for control, which every control command does explicitly.
- **Permanently forbidden:** reboot, shutdown, yell, camera, developer, live parameter mutation, dock maintenance, `task/cancel`, `clean/plan/start`, `clean/easy_clean/start`, and any raw command injection. These are on a deny list that no flag relaxes.
- **Confirmation and audit:** Every write command needs `--yes`; `--dry-run` previews without sending. Writes append to a gitignored `control_audit.jsonl`.
- **Machine-Readable Envelope:** Commands emit a single versioned JSON document (`schema_version: "1.0"`) to `stdout` and human diagnostics to `stderr`.
- **Accurate Telemetry Semantics:** Missing or unpolled attributes emit explicit JSON `null`, never defaulting to numerical `0`.

---

## Device Compatibility

Narwal Local Skill operates via the local WebSocket protocol on **TCP port 9002**.

| Model | Hardware Status | Upstream ID | Notes |
|---|---|---|---|
| **Narwal Flow 2** | **Hardware Validated** | `QxMSPG6VSO`, `iSuVlI1If2`, `mkbqaprvrb` | Smoke tested on physical hardware (firmware `v01.09.10.02`). |
| **Narwal Flow** (AX12) | Candidate | `QoEsI5qYXO` | Inherits protocol support from upstream community client. |
| **Freo Z10 Ultra** (CX4) | Candidate | `DrzDKQ0MU8` | Inherits protocol support from upstream community client. |
| **Freo Z10 Pro / Turbo** (AX26) | Candidate | `qV6BujoYLz` | Inherits protocol support from upstream community client. |
| **Freo X10 Pro** (AX15) | Candidate | `CNbforyZWI` | Inherits protocol support from upstream community client. |
| **Freo 20 / Freo 20 Edge** | Candidate | `fjhpiem4ba`, `ulonq49mm1` | Inherits protocol support from upstream community client. |
| **Freo Z Ultra** (CX7) | Candidate (Limited) | `hEA7OEshlx`; other variants unresolved | Tested upstream variant needs an explicit Device ID; no live broadcast progress. |
| **Freo Z10** (standard) | **Incompatible** | — | Port 9002 refused (`ECONNREFUSED`); under upstream investigation. |
| **Freo X Ultra** (AX18/AX19) | **Incompatible** | — | Uses ZeroMQ on TCP port 6789 and Tuya cloud; no WebSocket on port 9002. |
| **Freo X Plus** | **Incompatible** | — | Cloud-only protocol; no local port 9002 interface. |
| **Narwal J1 / J4 / T10** | **Incompatible** | — | Legacy cloud-only protocols; no compatible local WebSocket. |

> **Hardware Scope Note:** Only the **Narwal Flow 2** has been directly validated on physical hardware in this project. All candidate models inherit protocol compatibility from upstream reverse-engineering documentation.

---

## Installation & Setup

### Prerequisites

- Python 3.12 or newer
- Local network access to TCP port 9002; the same subnet is recommended. Some cross-VLAN setups may need source NAT depending on firmware and routing.

### Install from GitHub

Manage dependencies in an isolated virtual environment using `uv`:

```bash
# Clone public repository
git clone https://github.com/grapeot/narwal-skill.git
cd narwal-skill

# Create and activate virtual environment
uv venv
source .venv/bin/activate

# Install package in editable mode with development dependencies
uv pip install -e ".[dev]"
```

### Configuration Precedence

Configuration is resolved in strict priority order:
1. Command-line flags (`--host`, `--product-key`, `--device-id`, `--port`, `--budget`)
2. Process environment variables (`NARWAL_HOST`, `NARWAL_PRODUCT_KEY`, `NARWAL_DEVICE_ID`, `NARWAL_PORT`)
3. Explicit environment file (`--env-file PATH`)

*(Note: A `.env` file in the current working directory is never loaded automatically without `--env-file`.)*

```bash
# Example local configuration file (.env)
NARWAL_HOST=192.0.2.10
NARWAL_PORT=9002
NARWAL_PRODUCT_KEY=QxMSPG6VSO
NARWAL_DEVICE_ID=test-device-id-001
```

---

## CLI Usage

```bash
# Quick device identity, battery, and task status inspection
narwal-local snapshot --host 192.0.2.10 --product-key QxMSPG6VSO

# Fetch active floor plan and render visual PNG artifact to artifacts/
narwal-local map --host 192.0.2.10 --product-key QxMSPG6VSO --out-dir ./artifacts

# Stream live telemetry for 30 seconds into a structured JSONL artifact
narwal-local watch --host 192.0.2.10 --product-key QxMSPG6VSO --duration 30 --out-dir ./artifacts

# Run diagnostic verification across TCP, WebSocket, and query layers
narwal-local doctor --host 192.0.2.10 --product-key QxMSPG6VSO
```

### Control Commands (Opt-In)

Control commands send write topics. Preview without sending, then confirm with `--yes`:

```bash
# Preview a whole-house clean: builds the payload, sends no control topic
narwal-local start --host 192.0.2.10 --product-key QxMSPG6VSO --dry-run

# Pause the current task and write an audit line
narwal-local pause --host 192.0.2.10 --product-key QxMSPG6VSO --yes

# Send the robot back to its dock
narwal-local dock --host 192.0.2.10 --product-key QxMSPG6VSO --yes

# Clean selected rooms (ids come from the active map)
narwal-local clean --host 192.0.2.10 --product-key QxMSPG6VSO --rooms 3,5 --mode vacuum_and_mop --yes
```

`clean/start_clean` only applies while the robot is on its dock; otherwise the robot answers `NOT_READY`. The CLI reports the robot's answer rather than guessing.

### Exit Codes

- `0`: Operation completed successfully (`status: "ok"`).
- `2`: Invalid CLI arguments, non-finite bounds, missing configuration, missing `--yes`, or an unknown `--rooms` id (`status: "failed"`).
- `3`: Partial success; primary identity or map image retrieved, but sub-queries timed out (`status: "partial"`).
- `10`: Network transport error (TCP connection refused, host unreachable, WebSocket handshake failure).
- `11`: Query timeout, rejected response payload, or overall setup budget exceeded.
- `12`: Binary protobuf decoding failure or frame corruption.
- `13`: File system artifact write failure.
- `20`: Control command reached the robot and the robot declined it (`NOT_APPLICABLE`, `CONFLICT`, `NOT_READY`).

---

## Agent Skill Integration

This repository includes a ready-to-use agent skill definition under `skills/narwal/`:
- **Root Skill:** [`skills/narwal/SKILL.md`](skills/narwal/SKILL.md)
- **References:** [`skills/narwal/references/compatibility.md`](skills/narwal/references/compatibility.md) and [`skills/narwal/references/output-contract.md`](skills/narwal/references/output-contract.md)

To install this skill in an AI agent harness (e.g. Antigravity, OpenCode, Claude Code):
1. Provide the repository URL (`https://github.com/grapeot/narwal-skill`) or local checkout path to the coding agent.
2. Have the agent review its workspace routing instructions (`AGENTS.md` or `rules/skills/INDEX.md`).
3. Register exactly **one** root skill entry pointing to `skills/narwal/SKILL.md`. Sub-references remain internal.
4. Keep private robot IP addresses and device IDs in local environment files, leaving the skill documentation clean and portable.

Keep device configuration and generated maps in private local files; the repository provides the portable CLI and root skill.

---

## Acknowledgments & Upstream Attribution

This project builds upon protocol reverse-engineering by the open-source community:
- Vendors read-only protocol framing, protobuf models, and map rasterization from [`sjmotew/NarwalIntegration`](https://github.com/sjmotew/NarwalIntegration) pinned at commit `867706aa352729bd5367c258d4fd42de7cfd19a1` (MIT License, Copyright (c) 2026 sjmotew). See [`NOTICE`](NOTICE) and [`src/narwal_skill/vendor/NOTICE`](src/narwal_skill/vendor/NOTICE).
- Upstream reverse-engineering contributions by [@sjmotew](https://github.com/sjmotew), [@jgus](https://github.com/jgus), [@Sean-StarLabs](https://github.com/Sean-StarLabs), [@sytchi](https://github.com/sytchi), [@StratoGh0st99](https://github.com/StratoGh0st99), and community contributors.

---

## License & Disclaimer

This is an **unofficial, community-developed project** and is not affiliated with, sponsored by, or endorsed by Narwal.

Distributed under the [MIT License](LICENSE).
