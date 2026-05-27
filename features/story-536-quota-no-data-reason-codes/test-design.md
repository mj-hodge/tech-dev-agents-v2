# STORY-536: Test Design — Quota no_data Reason Classification

> Phase 7 | Scope: Small | RED state achieved
> Test file: `tests/ops_console/test_quota_no_data_reason_codes.py`

---

## Coverage Targets

| AC | Description | Tests |
|----|-------------|-------|
| AC-2 | Each branch tagged with a QuotaNoDataReason | 3 (one per branch) |
| AC-3 | Structured logger.warning with agent/reason/entries_seen | 3 (asserts on caplog) |
| AC-4 | Counter increments per branch; reset helper; copy safety | 4 |
| AC-5 | Public API contract — source stays "no_data", no new enum members | 5 |
| AC-6 | Happy-path does NOT increment counters | 1 |

**Total: 11 test cases** across 4 test classes.

---

## RED State Verification

```
ERROR collecting tests/ops_console/test_quota_no_data_reason_codes.py
ImportError: cannot import name 'QuotaNoDataReason' from
'tech_dev_agents.ops_console.services.loki_client'
1 error in 0.26s
```

The file fails at collection time because the three new symbols
(`QuotaNoDataReason`, `get_no_data_counters`, `_reset_no_data_counters`)
do not yet exist in `loki_client.py`. All 11 tests are RED.

Existing quota tests (15 total) remain GREEN:
- `tests/ops_console/test_loki_client_quota.py` — 7 tests PASS
- `tests/ops_console/test_routes_agents_quota_loki.py` — 8 tests PASS

---

## Test Structure

### Class: `TestNoDataReasonCodes` (AC-2, AC-3, AC-4)

Tests that each no_data branch classifies itself, logs a structured warning,
and increments the corresponding counter.

| Test | Branch Exercised | Counter Expected | Log Fields Checked |
|------|-----------------|------------------|--------------------|
| `test_loki_error_increments_counter_and_logs_reason` | LokiError (HTTP 500) | `loki_error=1` | `reason=loki_error`, `agent=dan` |
| `test_empty_result_increments_counter_and_logs_reason` | Empty Loki response | `empty_result=1` | `reason=empty_result`, `agent=daisy`, `entries_seen=0` |
| `test_parse_miss_increments_counter_and_logs_reason_with_entry_count` | 3 entries, none match `_parse_usage_line` | `parse_miss=1` | `reason=parse_miss`, `agent=derrick`, `entries_seen=3` |
| `test_happy_path_does_not_increment_any_counter` | Successful parse | all counters=0 | — |

### Class: `TestPublicApiContractUnchanged` (AC-5)

Tests that no external consumer sees any change.

| Test | Assertion |
|------|-----------|
| `test_loki_error_returns_no_data_source` | `result.source == "no_data"` |
| `test_empty_result_returns_no_data_source` | `result.source == "no_data"` |
| `test_parse_miss_returns_no_data_source` | `result.source == "no_data"` |
| `test_no_data_result_has_no_new_public_fields` | `QuotaSourceEnum` does not gain `loki_error`, `empty_result`, or `parse_miss` values |

### Class: `TestCounterResetHelper` (AC-4)

| Test | Assertion |
|------|-----------|
| `test_reset_zeroes_all_counters` | After firing all 3 branches then calling `_reset_no_data_counters()`, all counts = 0 |
| `test_get_no_data_counters_returns_copy` | Mutating the returned dict does not affect internal state |

---

## Fixtures

**`reset_counters` (autouse)** — calls `_reset_no_data_counters()` before and after each
test. Prevents cross-test counter pollution without relying on test ordering.

**`caplog`** (pytest built-in) — used with
`caplog.at_level(logging.WARNING, logger="tech_dev_agents.ops_console.services.loki_client")`
to capture structured warning messages.

---

## Mocking Strategy

All tests mock `httpx.AsyncClient` via `AsyncMock(spec=httpx.AsyncClient)`.
No real Loki or network calls are made.

| Scenario | Mock setup |
|----------|------------|
| LOKI_ERROR | `mock_http.get.return_value = httpx.Response(500, ...)` — triggers `LokiError` inside `query_range` |
| EMPTY_RESULT | `mock_http.get.return_value = _make_loki_response(streams=[])` — zero entries |
| PARSE_MISS | `_make_stream("agent", ["[USAGE] broken_format ..."])` — entries exist, `total_tokens=` absent |
| Happy path | `_make_stream("agent", ["[USAGE] total_tokens=50000 cost_usd=0.54"])` |

---

## Phase 8 Implementation Targets

To turn these tests GREEN, Phase 8 must add to `loki_client.py`:

1. **`QuotaNoDataReason` enum** (internal, not re-exported through response models):
   ```python
   class QuotaNoDataReason(str, Enum):
       LOKI_ERROR = "loki_error"
       EMPTY_RESULT = "empty_result"
       PARSE_MISS = "parse_miss"
   ```

2. **Counter dict**:
   ```python
   _NO_DATA_COUNTERS: dict[str, int] = {
       "loki_error": 0, "empty_result": 0, "parse_miss": 0,
   }
   ```

3. **`get_no_data_counters() -> dict[str, int]`** — returns `dict(_NO_DATA_COUNTERS)`

4. **`_reset_no_data_counters() -> None`** — zeroes all keys in `_NO_DATA_COUNTERS`

5. **`_no_data(reason, agent, entries_seen=0) -> QuotaInfo`** helper that:
   - Increments `_NO_DATA_COUNTERS[reason.value]`
   - Calls `logger.warning("quota no_data agent=%s reason=%s entries_seen=%d", agent, reason.value, entries_seen)`
   - Returns `QuotaInfo(source=QuotaSourceEnum.NO_DATA)`

6. **Three call-site rewrites** in `query_agent_quota()`:
   - `except LokiError` → `return _no_data(QuotaNoDataReason.LOKI_ERROR, agent_name)`
   - `if not entries` → `return _no_data(QuotaNoDataReason.EMPTY_RESULT, agent_name)`
   - `if sessions == 0` → `return _no_data(QuotaNoDataReason.PARSE_MISS, agent_name, len(entries))`

No changes to `responses.py`, routes, or any other file.

---

## External API Isolation

This story makes no outbound HTTP calls to external APIs. All HTTP is within
the internal Loki client (mocked in tests). Gate 2a does not apply.
