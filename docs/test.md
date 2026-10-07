# Testing Strategy & Test Plan — Narwal Local Skill

**Document Version:** 1.0.0
**Status:** Implemented & Verified
**Offline Test Suite:** 46 passed, 1 skipped (after review and hardware compatibility fixes)
**Hardware Validation:** Smoke verified on Narwal Flow 2 (firmware `v01.09.10.02`)

---

## 1. Test Architecture & Structure

Offline unit, loopback and packaging tests exercise failure paths without requiring a robot; hardware smoke testing is separately opt-in.

```text
tests/
├── unit/                 # Fast deterministic logic tests (no network)
│   ├── test_telemetry_and_map.py # Null/float/fault/coordinate/map tests
│   ├── test_allowlist_source.py # Send-scope audit
│   ├── test_bounds.py           # Numeric arguments, JSON and packed faults
│   └── test_wheel_notice.py     # Wheel license/NOTICE verification
├── loopback/             # In-process mock WebSocket tests (127.0.0.1)
│   ├── fake_robot.py         # Synthetic WebSocket peer
│   ├── frames.py             # Synthetic protobuf/frame builders
│   ├── test_session.py       # Responses, ACKs, late replies and deadlines
│   └── test_cli.py           # Commands, JSON, artifact and argument checks
└── hardware/             # Opt-in read-only physical hardware smoke tests
    └── test_live_read.py    # Gated live read validation on Narwal Flow 2
```

---

## 2. Test Execution & Results

### 2.1. Offline Test Execution
Run the complete offline test suite in the local `.venv`:

```bash
uv run ruff check src tests scripts
uv run pytest -q
```

**Results:**
- `ruff check`: All checks passed.
- `pytest`: **46 passed, 1 skipped** after review and compatibility fixes. The skipped test is `tests/hardware/test_live_read.py::test_live_snapshot_readonly` because hardware testing requires explicit opt-in.

### 2.2. Verified Test Scenarios
- **Numeric Bounds:** Non-finite and out-of-range timing arguments fail before connecting. Finite coordinate offsets may be negative within their allowed range.
- **Null Safety & Fault Shapes:** Missing fields emit null; observed zeros remain zero. Nested ErrorCode and packed variants are tested synthetically; packed `AC 02` is code 300.
- **Empty-Topic Matching:** Single-outstanding query queue correctly resolves `field5` replies with empty topics.
- **Late Reply Queue Purge:** Query timeouts trigger immediate socket closure and reset, preventing buffered replies from corrupting subsequent queries.
- **Map & Pillow Integrity:** Synthetic occupancy grids decode cleanly and render PNG artifacts verified by Pillow. Partial position sampling failure preserves map PNG with warning `position_sampling_failed`.
- **Wheel Packaging:** Built wheel packages `LICENSE`, `NOTICE`, `src/narwal_skill/vendor/NOTICE`, and `py.typed`.

---

## 3. Tier 4: Opt-In Hardware Validation

Physical hardware verification is strictly opt-in and gated behind environment configuration:

```bash
export NARWAL_LIVE_TEST=1
export NARWAL_HOST=192.0.2.10 # Replace with your robot's actual address
export NARWAL_PRODUCT_KEY=QxMSPG6VSO
uv run pytest tests/hardware/ -v
```

### Physical Hardware Verification Results (2026-10-06)
- **Target Device:** Narwal Flow 2 (firmware `v01.09.10.02`).
- **Smoke Tests Executed:** Serial runs of `doctor`, `snapshot` (with `--with-features` and `--with-map`), `map`, and `watch` returned status `ok`.
- **Map Artifact Verification:** Map PNGs opened and passed Pillow verification. Dimensions matched grid dimensions multiplied by `render_scale`.
- **Telemetry Verification:** Missing task progress correctly remained `null`. High-level `is_docked` and `charging_state` remained `null` with raw fields accessible.
- **Command Scope:** Sent commands were queries, telemetry subscription and heartbeat only. The robot can continue its own existing task; physical state invariance was not measured.

---

## 4. Public Hygiene & Fixture Rules

1. **Synthetic Fixtures:** Toy grids and fake per-device IDs are used. `QxMSPG6VSO` is a public model key, not a synthetic device identifier.
2. **RFC 5737 Documentation IPs:** Offline tests and CLI help tests use RFC 5737 addresses (`192.0.2.10`, `198.51.100.0/24`) or local loopback (`127.0.0.1`).
3. **Artifact Isolation:** Test runs write artifacts to temporary directories (`tmp_path`) or gitignored `artifacts/`.
