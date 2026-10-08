# AGENTS.md — Narwal Local Skill

## Project Identity & Scope

**Narwal Local Skill** is a lightweight Python CLI (`narwal-local`) and agent skill for Narwal robot vacuums communicating via the local WebSocket protocol on port 9002. It requires no Home Assistant instance, no cloud accounts, and no proprietary vendor bridges.

The default mode is read-only. An opt-in control mode (pause / resume / stop / dock / start / clean) is reachable only from a session opened for control, behind an explicit `--yes` confirmation and an audit log.

- **Working Language:** English for all source code, docstrings, comments, agent skills, documentation, and Git commits.
- **Licensing & Distribution:** MIT license; public repository `https://github.com/grapeot/narwal-skill`. Initial publication and downstream catalog PRs were explicitly authorized on 2026-10-06. The control extension was authorized on 2026-10-07. Later commits, pushes and public actions still require user authorization.
- **Branch Protection:** Default branch `master` requires pull requests, zero required reviewers, and administrator enforcement; do not bypass protection.
- **Hardware Validation Scope:** Direct physical hardware validation is strictly scoped to the **Narwal Flow 2** (firmware `v01.09.10.02` smoke tested). Other WebSocket-enabled models inherit candidate compatibility from upstream protocol documentation, not our own physical validation. The control payloads inherit upstream community research and are not hardware validated here.
- **Operational Safety Boundary:** Read-only by default. The read allowlist is 6 query topics. A separate control allowlist (pause / resume / stop / dock / start / clean) is reachable only with `allow_control=True`. A permanent deny list (`common/reboot`, `common/shutdown`, `common/yell`, camera/developer topics, live parameter mutation, dock maintenance, `task/cancel`, `clean/plan/start`, `clean/easy_clean/start`, raw injection) is never sent under any flag.

---

## Directory Structure & Layout

The project follows a standard modular layout:

```text
narwal_skill/
├── AGENTS.md               # Agent instructions, operational rules, and project boundaries
├── README.md               # User-facing project overview, capabilities, and quickstart
├── NOTICE                  # Upstream attribution and provenance notice
├── LICENSE                 # Project MIT license
├── .env.example            # Sample environment variables with synthetic documentation IPs
├── .gitignore              # Git ignore rules for caches, venv, logs, and artifacts
├── pyproject.toml          # Package configuration and build metadata
├── docs/
│   ├── prd.md              # Product Requirements Document
│   ├── rfc.md              # Technical Architecture & Protocol Design RFC
│   ├── test.md             # Offline loopback, smoke, and hardware test strategy
│   └── working.md          # Working log, changelog, and protocol lessons learned
├── src/
│   └── narwal_skill/       # Core Python library and narwal-local CLI package
├── tests/
│   ├── unit/               # Fast, offline unit tests and schema validators
│   ├── loopback/           # Mock WebSocket server tests (empty topic, late replies, timeouts)
│   └── hardware/           # Opt-in read-only physical hardware smoke tests
├── scripts/                # Verification and developer utility scripts
└── skills/
    └── narwal/             # Agent skill definition
        ├── SKILL.md        # Single discoverable root skill
        └── references/     # Focused local skill references and protocol notes
            ├── compatibility.md
            └── output-contract.md
```

---

## Python Environment & Dependency Management

- Use an isolated virtual environment located in `.venv/` inside this project directory (`adhoc_jobs/narwal_skill/.venv`).
- Manage environment and dependencies exclusively using `uv`:
  - Create environment: `uv venv`
  - Install dependencies: `uv pip install -e ".[dev]"`
  - Run tools and tests: `uv run pytest`, `uv run narwal-local ...`
- Do not use global `pip` or install packages into the system environment.

---

## Public Repository Hygiene & Privacy Guardrails

Because this project is prepared for an eventual open-source MIT release on GitHub:

1. **No Real Device Identifiers or Network Data:**
   - Never commit private IP addresses, MAC addresses, serial numbers, per-device cloud IDs, Wi-Fi SSIDs, or home data. Public model-level product keys from upstream compatibility tables are protocol constants, not private per-device identifiers.
   - All examples, `.env.example`, mock fixtures, and unit tests must strictly use RFC 5737 documentation IP addresses (`192.0.2.10`, `198.51.100.0/24`, `203.0.113.0/24`) and synthetic device IDs (e.g., `test-device-id-001`, `fake-product-key`).
2. **No Real Floor Plans or Sensor Captures:**
   - Never commit raw home map files, actual room names matching private residences, or unredacted raw network packet captures (`.pcap`).
   - Mock test fixtures must use synthetic, procedurally generated floor plan grids and fake room labels.
3. **Artifact Isolation:**
   - Any runtime exports (such as map PNGs, JSONL watch recordings, diagnostic logs) must be placed in `artifacts/` or `data/`, which are ignored by `.gitignore`.
4. **Credential Isolation:**
   - Live credentials and hardware IP addresses reside only in local uncommitted `.env` files. Never stage or commit `.env`.

---

## Architecture & Code Guidelines

1. **Upstream Code Reuse:**
   - Core WebSocket transport and protobuf serialization logic reuses the independent `narwal_client` from `sjmotew/NarwalIntegration` pinned at commit `867706aa352729bd5367c258d4fd42de7cfd19a1`.
   - The original MIT license notice and copyright attribution are maintained in `NOTICE` and `src/narwal_skill/vendor/NOTICE`.
   - Completely decoupled from Home Assistant: zero dependencies on Home Assistant core (`homeassistant.*`).
2. **Implemented CLI Commands (`narwal-local`):**
   - `snapshot`: Inspect device identity, firmware, battery, docking state, and active task telemetry.
   - `map`: Fetch active map, render a clean visual PNG, and output structured room/grid metadata.
   - `watch`: Stream live telemetry with an explicit, mandatory time-bound duration (`--duration`, max 300s) and structured JSONL logging.
   - `doctor`: Step-by-step diagnostic probe separating transport errors from query/parse failures.
   - `pause` / `resume` / `stop` / `dock` / `start` / `clean`: Opt-in control commands. Each requires `--yes`; `--dry-run` previews without sending; writes append to `control_audit.jsonl`.
3. **CLI I/O Contract:**
   - **stdout:** Machine-readable versioned JSON envelope (`status`: `ok` | `partial` | `failed`, `observed_at`, `device`, `data`, `warnings`, `errors`, `artifacts`).
   - **stderr:** Human-readable diagnostic progress logs and error descriptions.
   - **Missing Data Semantic:** Telemetry fields that are unpolled, unavailable, or unsupported by a model must be emitted as `null`, **never defaulted to `0`**.
4. **Protocol Quirks & Traps:**
   - Responses to `field5` queries arrive with an **empty topic string**; they cannot be matched by topic. The client enforces a strict serial request queue (single outstanding request) and resets the WebSocket connection on timeout to purge late replies.
   - Subscription ACKs arrive with empty topics and are consumed before issuing subsequent queries.
   - Working status `field13` represents the **station timer** (seconds), NOT the cleaned area.
   - Quiet telemetry does not prove deep sleep. Broadcast-window timings are model-specific and may reflect client stimuli. Do not spam wake requests or assert those timings for every robot.
   - TCP/WebSocket connection success does not guarantee query success; internal application state must be explicitly verified.
5. **Agent Skill Contract:**
   - Maintain a single root `skills/narwal/SKILL.md` that is discoverable.
   - Focused protocol notes and schema references belong in `skills/narwal/references/`.

---

## Agent Development Workflow

- **Document Before Doing:** Keep `docs/working.md` updated with every substantive change, including the Changelog, Current Status, and Lessons Learned.
- **No Unsolicited Commits or Pushes:** Do not run `git commit`, `git push`, or configure remote origins unless the user explicitly gives a direct command to do so.
- **Testing Discipline:** Run meaningful offline loopback tests. The current suite has 80 passed and 1 opt-in skip; Flow 2 smoke checks were rerun after response guards changed. Never invent results or treat one firmware's smoke test as universal validation.
