# Project Working State & Changelog — Narwal Local Skill

**Project State:** Implemented, Tested, & Hardware Smoke Verified
**Active Working Directory:** `adhoc_jobs/narwal_skill`
**License:** MIT
**Target Repository:** Public GitHub `grapeot/narwal-skill` (initial publication authorized on 2026-10-06)

---

## 1. Project Overview & Pinned Dependencies

- **Project Purpose:** Lightweight, read-only Python CLI (`narwal-local`) and agent skill for Narwal robot vacuums over the local WebSocket protocol (port 9002).
- **Physical Hardware Validation Target:** Narwal Flow 2 (firmware `v01.09.10.02` smoke tested).
- **Candidate Compatibility (Upstream Protocol):** Narwal Flow (AX12), Freo Z10 Pro/Turbo (AX26), Freo Z10 Ultra (CX4), Freo X10 Pro (AX15), Freo 20 / Freo 20 Edge, Freo Z Ultra (CX7).
- **Unsupported Architectures:** Models on ZeroMQ port 6789 (Freo X Ultra), cloud-only models (Freo X Plus, J1/T10, J4), and models refusing port 9002 (standard Freo Z10).
- **Upstream Code Reference:** [`sjmotew/NarwalIntegration`](https://github.com/sjmotew/NarwalIntegration) pinned at commit `867706aa352729bd5367c258d4fd42de7cfd19a1`. Reuses independent client protocol, models, and map renderer with MIT attribution in `NOTICE`.

---

## 2. Implementation Roadmap & Current Status

- [x] **Milestone 0: Project Scaffolding & Specifications**
  - [x] Operational rules and privacy boundaries (`AGENTS.md`)
  - [x] Public project overview (`README.md`)
  - [x] Product Requirements Document (`docs/prd.md`)
  - [x] Technical Architecture RFC (`docs/rfc.md`)
  - [x] Testing Strategy & Plan (`docs/test.md`)
  - [x] Working State Tracking (`docs/working.md`)
- [x] **Milestone 1: Environment & Dependency Setup**
  - [x] Configured `pyproject.toml` with `websockets`, `bbpb`, `Pillow`, and dev dependencies
  - [x] Initialized local virtual environment using `uv venv` and installed package via `uv pip install -e ".[dev]"`
- [x] **Milestone 2: Client Extraction & Decoupling**
  - [x] Extracted read-only modules from upstream commit `867706aa352729bd5367c258d4fd42de7cfd19a1`
  - [x] Decoupled from Home Assistant (zero `homeassistant.*` dependencies)
  - [x] Implemented strict serial single-outstanding request queue
  - [x] Implemented empty-topic response matching for `field5` queries
  - [x] Implemented socket reset on query timeout to purge late replies
  - [x] Filtered out subscription ACKs prior to dispatching queries
  - [x] Enforced strict 6-topic read-only send allowlist
- [x] **Milestone 3: Core Telemetry Normalization & Map Pipeline**
  - [x] Implemented strict `null` vs `0` telemetry parser (`allow_nan=False`)
  - [x] Implemented packed varint decoding for fault codes
  - [x] Implemented working status field 13 disambiguation (`station_bag_dry_total_seconds`)
  - [x] Implemented occupancy grid decoding, coordinate policies (`grid`, `upstream`, `raw`), and Pillow PNG rendering
- [x] **Milestone 4: CLI Implementation (`narwal-local`)**
  - [x] Built subcommands: `snapshot`, `map`, `watch` (with mandatory bounded `--duration`), `doctor`
  - [x] Enforced single JSON document on `stdout` and human diagnostics on `stderr`
  - [x] Enforced finite ranges on all numeric CLI arguments before opening sockets
  - [x] Defined exit codes (0, 2, 3, 10, 11, 12, 13)
- [x] **Milestone 5: Offline Loopback Mock Server & Test Suite**
  - [x] Implemented in-process mock WebSocket server
   - [x] Built offline tests covering protocol quirks, timeouts and CLI argument boundaries
   - [x] Verified test suite passes: 46 passed, 1 skipped after review and compatibility fixes
  - [x] Verified `ruff check` passes
  - [x] Verified wheel build packages `LICENSE`, `NOTICE`, and `vendor/NOTICE`
- [x] **Milestone 6: Physical Hardware Smoke Verification (Narwal Flow 2)**
  - [x] Performed serial live smoke checks on Narwal Flow 2 (firmware `v01.09.10.02`)
  - [x] Verified `doctor`, `snapshot`, `map`, and `watch` return status `ok`
  - [x] Verified rendered map PNG dimensions match grid metadata and pass Pillow validation
   - [x] Verified query/subscription/heartbeat scope; existing robot activity was not frozen by a control command
- [x] **Milestone 7: Antigravity Skill Definition**
  - [x] Authored root discoverable skill `skills/narwal/SKILL.md`
  - [x] Authored focused references `compatibility.md` and `output-contract.md`
- [x] **Milestone 8: Publication & External Review**
   - [x] Independent DeepSeek privacy/code review completed; reproduced findings fixed and rechecked with no blocking findings remaining
   - [x] Public repository published at `grapeot/narwal-skill`; initial commit `1c02acf` and repository CI passed
   - [x] Master protection verified: required PRs, zero reviewers, strict `test` check, administrator enforcement, force pushes/deletions disabled
   - [x] Chinese/English ecosystem and skills registry catalog PRs merged; registry entry 79 in `analyze` / `life`

---

## Lessons Learned

The following empirical protocol facts and engineering lessons were verified during implementation and hardware smoke testing:

1. **`field5` Responses Have Empty Topics:**
   - In the Narwal port 9002 WebSocket protocol, responses to query requests arrive with an empty topic string `""`.
   - Responses cannot be matched by topic. The client must maintain a strict serial queue (single outstanding query).
2. **Late Replies Desynchronize the Request Queue Without Socket Reset:**
   - If a query times out on the client and the TCP connection remains open, the delayed response from the robot eventually arrives and corrupts the subsequent query.
   - Closing and resetting the WebSocket connection immediately upon query timeout is mandatory to purge queued frames.
3. **Subscription ACKs Consume Field 5 Slots:**
   - Subscribing to broadcast topics generates an asynchronous acknowledgment frame with an empty topic. The client must consume and discard subscription ACKs before dispatching subsequent queries.
4. **Fault Codes Have Multiple Shapes:**
    - The parser supports nested ErrorCode dictionaries/lists and integer/packed variants. Synthetic packed `AC 02` is code 300; the robot's real fault-state wire format has not been captured.
5. **Working Status Field 13 Is a Station Timer, Not Area:**
   - Protobuf tag 13 in `working_status` is `station_bag_dry_total_seconds` (station dust-bag drying timer in seconds), **never** cleaned area or mop washing duration. Cleaned area is present only in active task telemetry (field 2).
6. **Quiet Telemetry Does Not Prove Deep Sleep:**
    - Report absence without assigning a cause. Do not inject repeated wake bursts merely because broadcasts are absent.
7. **Single Connection per Source IP:**
   - The robot allows only one active connection per source IP address. If a second connection is opened from the same IP, the robot closes the earlier connection with `"connection with same ip, close old one"`.
8. **Cross-VLAN Reports Are Model-Specific:**
    - Upstream reports source-subnet restrictions on some setups. SNAT is a troubleshooting option, not a universal requirement or a behavior measured here.
9. **Never Infer "Healthy" from Absent Fault Lists:**
    - Missing fault reports yield null fault codes, null healthy and unknown health in base views.
10. **Coordinate Calibration Is Provisional:**
    - Floor plan grid resolution is measured in millimeters per cell. Robot trajectory coordinates vary by model. All coordinate outputs explicitly set `calibration: "not_calibrated"`.

---

## Changelog

- **2026-10-06 (Initial Scaffold):**
  - Created project scaffolding, initial `AGENTS.md`, `README.md`, and technical specifications (`docs/prd.md`, `docs/rfc.md`, `docs/test.md`, `docs/working.md`).
- **2026-10-06 (Implementation & Packaging):**
  - Implemented core library `narwal_skill` and CLI `narwal-local` with subcommands `snapshot`, `map`, `watch`, and `doctor`.
  - Decoupled `narwal_client` from Home Assistant; added `NOTICE` and MIT attribution.
  - Implemented single-outstanding request queue, empty-topic matching, and timeout socket reset.
  - Implemented 6-topic read-only send allowlist in `allowlist.py`.
  - Implemented strict null vs 0 telemetry parser, packed varint decoding, and Pillow map rasterization.
  - Configured `pyproject.toml` with `websockets`, `bbpb`, `Pillow`, and dev dependencies.
- **2026-10-06 (Verification & Hardware Smoke Testing):**
  - Executed offline test suite: 31 passed, 1 skipped. `ruff check` passed.
  - Verified wheel packaging contains `LICENSE`, `NOTICE`, and `vendor/NOTICE`.
  - Performed physical hardware smoke testing on Narwal Flow 2 (firmware `v01.09.10.02`). Verified read-only execution of `doctor`, `snapshot`, `map`, and `watch`. Verified rendered map PNG with Pillow.
- **2026-10-06 (Skill Authoring & Documentation Finalization):**
  - Authored root agent skill `skills/narwal/SKILL.md`.
  - Authored focused references `skills/narwal/references/compatibility.md` and `skills/narwal/references/output-contract.md`.
   - Updated `README.md`, `AGENTS.md`, and docs (`prd.md`, `rfc.md`, `test.md`, `working.md`) to reflect implemented behavior, verified tests, and hardware facts.
- **2026-10-06 (Review Remediation):**
  - Added query-specific payload validation and closed sessions with mismatched or unsolicited replies; same-schema duplicate ambiguity remains a protocol limitation.
  - Added nested fault-code parsing, third-key discovery/map coverage, connection-cap diagnostics, clipped-position warnings and failure artifact handling.
  - Added capture-file ignore patterns and migrated wheel license metadata; expanded regression coverage to 44 passed and 1 opt-in skip.
- **2026-10-06 (Post-Review Hardware Compatibility):**
  - Final live checks exposed a Flow 2 subscription configuration echo; added a narrow schema for it without relaxing unrelated-query guards.
  - Reran ruff and offline tests: 46 passed and 1 opt-in skip; reran doctor, snapshot with features/map, map and watch successfully on Flow 2.
- **2026-10-06 (Final Review & Workspace Registration):**
  - DeepSeek independently reran the suite and a 23-case response-guard matrix; privacy passed and no blocking code finding remained after remediation.
  - Fresh-context use of the root skill and CLI help succeeded against a synthetic loopback peer.
  - Registered one root skill through the workspace's private overlay and routing indexes; publication remains separately authorized.
- **2026-10-06 (Publication Preparation):**
  - User authorized a public repository, PR-required master protection with zero reviewers and admin enforcement, and submission/merge of the three catalog PRs.
  - Antigravity drafted the repository description, bilingual ecosystem rows, registry entry and PR descriptions; the main thread checked their capability and model-scope claims.
- **2026-10-06 (Public Release & Catalog Integration):**
  - Published [grapeot/narwal-skill](https://github.com/grapeot/narwal-skill) with default branch master and initial commit `1c02acf`; [repository CI](https://github.com/grapeot/narwal-skill/actions/runs/37569264150) passed.
  - Verified protection via API: PRs required with zero approving reviewers, strict test status check, administrators enforced, and no bypass exceptions or force pushes/deletions.
  - Merged [Chinese ecosystem #105](https://github.com/grapeot/context-infrastructure/pull/105) and [English ecosystem #62](https://github.com/grapeot/context-infrastructure-en/pull/62).
  - Merged [Superlinear registry #44](https://github.com/yage-ai/superlinear_skills_registry/pull/44), adding sequence 79 in analyze / life. Registry checker, lint, 18 Vitest tests, build, pull-request CI and post-merge CI passed; the merge commit's Vercel Production deployment reported success.
  - Descriptions were drafted by Antigravity and fact-checked against the unchanged read-only port-9002 product; private configuration, robot identifiers, maps and recordings were excluded from publication.
