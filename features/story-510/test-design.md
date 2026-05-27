# STORY-510: Test Design — Harden `/api/agents/{name}/quota` Endpoint

**Phase:** 7 (Test Design)
**Story:** STORY-510 — Harden `/api/agents/{name}/quota` (ccusage-backed + Weekly Trend)
**Scope:** Medium
**Input:** `seed.md`, `analysis.md`, `feature-spec.md`
**Status:** RED (tests written; implementation in Phase 8)

---

## 1. Test Philosophy

All tests mock the SSH/subprocess layer. No real SSH connections, no real `ccusage` binary.
The injectable `_ssh_runner` symbol (to be introduced in Phase 8 at the module level of
`tech_dev_agents/ops_console/routes/agents.py`) is replaced per-test via `monkeypatch.setattr`.
The agent-side helper (`scripts/quota_ccusage.py`) is tested by mocking `subprocess.run` and
`shutil.which` — the script itself never opens a network connection.

**RED-state mechanism:**
- `tests/ops_console/test_routes_agents_quota.py` fails at import time because
  `QuotaSourceEnum`, `PacingStatusEnum`, `QuotaDaily`, `QuotaWeeklyResponse` do not yet
  exist in `tech_dev_agents/ops_console/models/responses.py`.
- `tests/scripts/test_quota_ccusage.py` fails at fixture setup time because
  `scripts/quota_ccusage.py` does not yet exist.

Both failures are intentional and traceable to unimplemented Phase 8 symbols.

---

## 2. Test Files

| File | Purpose | Test count |
|------|---------|-----------|
| `tests/ops_console/test_routes_agents_quota.py` | HTTP route tests for `/quota` and `/quota/weekly` | 25 test functions |
| `tests/scripts/test_quota_ccusage.py` | Agent-side helper unit tests for `scripts/quota_ccusage.py` | 21 test functions |
| `tests/ops_console/fixtures/ccusage_blocks_healthy.json` | Healthy ccusage current-block output (normalised by `quota_ccusage.py`) | — |
| `tests/ops_console/fixtures/ccusage_blocks_no_active.json` | No active block output (source=ccusage, numerics null) | — |
| `tests/ops_console/fixtures/ccusage_weekly_healthy.json` | 7-day weekly trend fixture | — |
| `tests/ops_console/fixtures/ccusage_jsonl_fallback.json` | JSONL fallback fixture (source=jsonl, valid numerics) | — |

---

## 3. Acceptance Criteria Coverage

| AC | Criterion | Tests |
|----|-----------|-------|
| AC-1 | GET /quota returns valid QuotaResponse under every failure mode | T510-01, T510-02, T510-03, T510-04, T510-05, T510-06 |
| AC-2 | source="unavailable" + null numerics when ccusage absent | T510-02, T510-05, T510-06, TC-S01, TC-S02 |
| AC-3 | Response includes all required fields (new + STORY-496 back-compat) | T510-01 |
| AC-4 | GET /quota/weekly returns 7-day trend shape | T510-08, T510-09 |
| AC-5 | ccusage availability probe observable via logs | T510-13 |
| AC-6 | Endpoint returns within 10s SSH timeout budget | T510-05 (timeout path covered) |
| AC-7 | Unit tests cover all failure modes with mocked SSH | All T510-* tests |
| AC-8 | AGENT_QUOTA_ENABLED gates both endpoints | T510-10 |

---

## 4. Test Cases — Route Layer

### TestQuotaHappyPath

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| T510-01 | Happy path ccusage source | Runner returns `ccusage_blocks_healthy.json` | 200, source=ccusage, pacing_status=on_track, all numerics populated, STORY-496 fields preserved |
| T510-01b | Pacing approaching_limit | tokens=700k, remaining=100min, p90=792k | 200, pacing_status=approaching_limit |
| T510-01c | Pacing exceeded | tokens=800k, remaining=50min, p90=792k | 200, pacing_status=exceeded |

### TestQuotaEmptyStdout

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| T510-02 | Empty stdout returns unavailable | Runner returns `(0, b"", b"")` | 200, source=unavailable, pacing_status=unknown, all numerics null |

### TestQuotaMalformedStdout

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| T510-03 | bash error returns unavailable | Runner returns bash error message | 200, source=unavailable |
| T510-03b | Spinner prefix recovered | stdout=`"⠋ Loading...\n{...json...}"` | 200, source=ccusage (last-line parse recovers) |

### TestQuotaNoActiveBlock

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| T510-04 | No active block → ccusage + null numerics | Runner returns `ccusage_blocks_no_active.json` | 200, source=ccusage, tokens=null, pacing_status=unknown, p90_limit populated |

### TestQuotaSshTimeout

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| T510-05 | SSH timeout → unavailable | Runner returns `(-1, b"", b"ssh timeout")` | 200, source=unavailable, never 5xx |

### TestQuotaAgentUnreachable

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| T510-06 | Agent unreachable → unavailable | Runner returns `(-1, b"", b"ssh error: ConnectionRefused")` | 200, source=unavailable |

### TestQuotaJsonlFallback

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| T510-07 | JSONL fallback source | Runner returns `ccusage_jsonl_fallback.json` (source=jsonl) | 200, source=jsonl, pacing derived from numerics |

### TestQuotaWeeklyHappyPath

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| T510-08 | Weekly returns 7 days | Runner returns `ccusage_weekly_healthy.json` | 200, len(days)==7, total_tokens=5411200, day shape correct |
| T510-08b | Weekly unavailable returns empty | SSH failure | 200, source=unavailable, days=[] |

### TestQuotaWeeklyPadding

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| T510-09 | 4 days padded to 7 | Runner returns 4-day payload | 200, len(days)==7, ≥3 zero-padded days |

### TestQuotaFeatureFlagDisabled

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| T510-10a | /quota disabled → 404 | agent_quota_enabled=False | 404 |
| T510-10b | /quota/weekly disabled → 404 | agent_quota_enabled=False | 404 |

### TestQuotaAgentNotFound

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| T510-11a | Unknown agent /quota → 404 | get_agent raises AgentNotFoundError | 404, "not found" in detail |
| T510-11b | Unknown agent /quota/weekly → 404 | get_agent raises AgentNotFoundError | 404 |
| T510-11c | Invalid agent name → 404/422 | Name with `..` special chars | 404 or 422 (rejected by _validate_agent_name) |

### TestQuotaCacheHit

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| T510-12 | /quota 2× → runner called once | Counting runner | call_count == 1 |
| T510-12b | /quota/weekly 2× → runner called once | Counting runner | call_count == 1 |

### TestQuotaSourceTransitionLogging

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| T510-13 | Transition ccusage→unavailable logs warning | First probe=ccusage, cache cleared, second probe=timeout | `[QUOTA] ccusage unavailable on dan` in WARNING logs exactly once |

### TestDerivePacing (unit tests for `_derive_pacing`)

| ID | Name | Inputs | Expected |
|----|------|--------|---------|
| T510-14a | on_track | tokens=100k, remaining=150, p90=800k | on_track |
| T510-14b | approaching_limit | tokens=350k, remaining=150, p90=800k | approaching_limit |
| T510-14c | exceeded | tokens=400k, remaining=150, p90=800k | exceeded |
| T510-14d | tokens=None → unknown | None, 150, 800k | unknown |
| T510-14e | p90=None → unknown | 100k, 150, None | unknown |
| T510-14f | remaining=None → unknown | 100k, None, 800k | unknown |
| T510-14g | source=unavailable → unknown | 100k, 150, 800k, "unavailable" | unknown |
| T510-14h | elapsed clamped at 0.05 | remaining=295 (5min elapsed) | no exception, valid status |
| T510-14i | p90=0 → unknown | 100k, 150, 0 | unknown (no ZeroDivisionError) |

---

## 5. Test Cases — Agent-Side Helper (`scripts/quota_ccusage.py`)

### TestCcusageNotInstalled

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| TC-S01 | ccusage missing → unavailable | shutil.which returns None | source=unavailable, tokens=null |
| TC-S02 | ccusage missing weekly → unavailable | shutil.which returns None | source=unavailable, days=[] |
| TC-S03 | Missing binary never raises | shutil.which raises Exception | no exception propagated |

### TestCcusageNonZeroExit

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| TC-S04 | Non-zero exit → fallback | subprocess.run returns rc=1 | source in {jsonl, unavailable} |
| TC-S05 | FileNotFoundError race → fallback | subprocess.run raises FileNotFoundError | source in {jsonl, unavailable} |
| TC-S06 | Timeout → fallback | subprocess.run raises TimeoutExpired | source in {jsonl, unavailable} |

### TestCcusageSpinnerPrefix

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| TC-S07 | Spinner prefix recovered | stdout="⠋ Loading...\n{json}" | source=ccusage, tokens=412900 |
| TC-S08 | All junk → fallback | stdout="Loading...\nconnection refused" | source in {jsonl, unavailable} |

### TestCcusageNoActiveBlock

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| TC-S09 | No active block → ccusage + null numerics | activeBlock=null in ccusage JSON | source=ccusage, tokens=null |

### TestCcusageHappyPath

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| TC-S10 | Healthy output normalised | HEALTHY_CCUSAGE_BLOCKS | source=ccusage, tokens=412900, cost≈1.87, p90=792000 |
| TC-S11 | Single JSON line on stdout | HEALTHY_CCUSAGE_BLOCKS | exactly 1 non-empty stdout line |
| TC-S12 | No exception propagates | shutil.which raises | no exception (exit 0 guaranteed) |

### TestCcusageWeeklyHappyPath

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| TC-S13 | Weekly returns 7 days | HEALTHY_CCUSAGE_WEEKLY | source=ccusage, len(days)==7 |
| TC-S14 | Day entry shape | HEALTHY_CCUSAGE_WEEKLY | each day has date, tokens, cost_usd, blocks_used |
| TC-S15 | Weekly totals present | HEALTHY_CCUSAGE_WEEKLY | total_tokens=5411200 |

### TestCcusageWeeklyPadding

| ID | Name | Setup | Assertion |
|----|------|-------|-----------|
| TC-S16 | 4 days → padded to 7 | 4-day payload | len(days)==7, ≥3 zero days |
| TC-S17 | 1 day → padded to 7 | 1-day payload | len(days)==7 |

### TestParseLastJsonLine (unit tests for `_parse_last_json_line`)

| ID | Name | Input | Expected |
|----|------|-------|---------|
| TC-S18 | Single JSON line | `'{"source": "ccusage"}'` | `{"source": "ccusage"}` |
| TC-S19 | Junk then JSON | `"Loading...\nfoo bar\n{...}"` | parsed JSON dict |
| TC-S20 | All junk → None | `"Loading...\nconnection refused"` | None |
| TC-S21 | Empty string → None | `""` | None |

---

## 6. Mock Strategy

### Route tests (`test_routes_agents_quota.py`)

```python
# Pattern: monkeypatch the module-level _ssh_runner symbol
monkeypatch.setattr(
    agents_mod,  # tech_dev_agents.ops_console.routes.agents
    "_ssh_runner",
    _make_ssh_runner(rc=0, stdout=fixture_bytes, stderr=b""),
)
```

- **Autouse guard:** Every test in the module has a guard fixture that replaces
  `_ssh_runner` with a sentinel that fails loudly if called without an override.
  This prevents accidental real SSH calls.
- **Cache clearing:** `_QUOTA_CACHE` is cleared before and after every test to prevent
  cache-hit bleed between test cases.
- **Feature flag:** A local `quota_settings` fixture supplies `agent_quota_enabled=True`.
  The flag-disabled tests create a separate client with `agent_quota_enabled=False`.

### Agent-side helper tests (`test_quota_ccusage.py`)

```python
# Pattern 1: mock shutil.which
with patch("shutil.which", return_value=None):  # or "/usr/bin/ccusage"
    ...

# Pattern 2: mock subprocess.run
with patch("subprocess.run", return_value=_make_proc(stdout=..., returncode=...)):
    result = _run_script(module, weekly=False)
```

- Script is loaded via `importlib.util` (same pattern as `tests/scripts/conftest.py`
  for `fleet_review`). Raises `ImportError` with clear message if file missing.
- `_run_script()` helper captures stdout via `contextlib.redirect_stdout`, splits
  on newlines, and parses the last non-empty line as JSON.

---

## 7. Fixtures

### JSON Fixtures (in `tests/ops_console/fixtures/`)

| File | Content | Used by |
|------|---------|---------|
| `ccusage_blocks_healthy.json` | Normalised quota_ccusage.py output: source=ccusage, all fields | T510-01, T510-03b, T510-12, T510-13 |
| `ccusage_blocks_no_active.json` | source=ccusage, no active block, p90_limit present | T510-04 |
| `ccusage_weekly_healthy.json` | 7-day weekly trend | T510-08, T510-12b |
| `ccusage_jsonl_fallback.json` | source=jsonl, valid numerics | T510-07 |

These fixtures represent what `quota_ccusage.py` prints to stdout (i.e., the normalised
schema), **not** raw `ccusage` command output. This matches the server's parse path in
`_parse_quota_stdout` / `_parse_weekly_stdout`.

### Pytest Fixtures (local to `test_routes_agents_quota.py`)

| Fixture | Scope | Purpose |
|---------|-------|---------|
| `quota_settings` | function | Settings with `agent_quota_enabled=True` |
| `quota_app` | function (async) | FastAPI app with quota endpoints live |
| `quota_client` | function (async) | Authenticated httpx client for quota-enabled app |
| `mock_agent_service` | function | AgentService mock; `dan` and `derrick` in registry |
| `_block_real_ssh` | function (autouse) | Guard: replaces `_ssh_runner` with a failing sentinel |
| `_clear_quota_cache` | function (autouse) | Clears `_QUOTA_CACHE` before/after each test |

---

## 8. Test Infrastructure Notes

### Pytest configuration
- Framework: `pytest` + `pytest-asyncio` (existing project setup)
- Async tests: `@pytest.mark.asyncio` on every async test method
- Fixtures: `@pytest_asyncio.fixture` for async fixtures
- HTTP client: `httpx.AsyncClient` with `ASGITransport` (no network)

### asyncio mode
The project uses the existing `pytest-asyncio` setup without an explicit asyncio mode
declaration. Async tests are decorated individually with `@pytest.mark.asyncio` (same
pattern as `tests/ops_console/test_routes_agents.py`).

### Model validation
Since Pydantic models are tested indirectly via the HTTP response body, assertions check
`resp.json()["quota"]["source"]` (string) rather than comparing enum instances.
`_derive_pacing` unit tests check `str(result) in {"on_track", ...}` to tolerate both
enum and raw-string returns.

---

## 9. Phase 8 Contract (what must be implemented to go GREEN)

The following symbols must exist for all tests to pass:

### `tech_dev_agents/ops_console/models/responses.py`
- `QuotaSourceEnum(str, Enum)` with values: `ccusage`, `jsonl`, `unavailable`
- `PacingStatusEnum(str, Enum)` with values: `on_track`, `approaching_limit`, `exceeded`, `unknown`
- `QuotaInfo` extended with: `source`, `pacing_status`, `current_block_tokens`, `current_block_cost_usd`, `time_remaining_minutes`
- `QuotaDaily(BaseModel)` with: `date`, `tokens`, `cost_usd`, `blocks_used`
- `QuotaWeeklyResponse(BaseModel)` with: `agent`, `source`, `total_tokens`, `total_cost_usd`, `days`

### `tech_dev_agents/ops_console/routes/agents.py`
- `_ssh_runner(agent_name, remote_cmd, timeout_s=10.0) -> tuple[int, bytes, bytes]` (module-level)
- `_QUOTA_CACHE: TTLCache` (module-level, with `.clear()` method)
- `_fetch_agent_quota(agent_name) -> QuotaInfo` (rewritten, delegates to `_ssh_runner`)
- `_fetch_agent_quota_weekly(agent_name) -> QuotaWeeklyResponse` (new)
- `_parse_quota_stdout(raw: bytes) -> QuotaInfo` (new)
- `_parse_weekly_stdout(agent_name, raw: bytes) -> QuotaWeeklyResponse` (new)
- `_derive_pacing(tokens, remaining_min, p90, source) -> PacingStatusEnum` (new)
- `_log_source_transition(agent_name, previous, current) -> None` (new)
- `GET /agents/{name}/quota/weekly` route (new)

### `scripts/quota_ccusage.py`
- `main(weekly: bool = False)` — entry point, prints one JSON line to stdout
- `_parse_last_json_line(text: str) -> dict | None` — tolerates non-JSON prefix lines
- `_jsonl_fallback_quota()` and `_jsonl_fallback_weekly()` — or equivalent fallback logic
- Always exits 0; never propagates exceptions

---

## 10. Exit Criteria (Phase 7 → 8)

- [x] `test-design.md` written with full test-case table covering every AC from `seed.md`.
- [x] `tests/ops_console/test_routes_agents_quota.py` written and in RED state (import error on new model symbols).
- [x] `tests/scripts/test_quota_ccusage.py` written and in RED state (ImportError on missing script).
- [x] Four JSON fixture files created in `tests/ops_console/fixtures/`.
- [x] Every AC from `seed.md` mapped to at least one test case.
- [x] No real SSH or subprocess calls in any test.
- [x] Autouse guard prevents accidental real SSH in the route test suite.
