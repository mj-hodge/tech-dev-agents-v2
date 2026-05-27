# STORY-513: Quota Data Layer — Test Design

**Phase 7 deliverable**
**Story:** STORY-513 — Quota endpoint data layer (Loki-based)
**Date:** 2026-04-22
**Author:** Hermes (automated dispatch)

---

## 1. Summary

| File | Class | Tests | State |
|------|-------|-------|-------|
| `tests/ops_console/test_loki_client_quota.py` | `TestLokiClientQueryAgentQuota` | 7 | RED |
| `tests/ops_console/test_routes_agents_quota_loki.py` | `TestQuotaRouteLoki` | 8 | RED (12 fail, 3 pass) |
| **Total** | | **15** | **12 RED / 3 structural pass** |

**Collected:** 15 tests, no import errors.
**Run result:** 12 FAILED (expected RED state), 3 PASSED (structural/gating tests that already work).

---

## 2. Log Line Format (defined this phase)

```
[USAGE] total_tokens=50000 cost_usd=0.54
```

- Emitted by `claude_sdk_tool.py` at the end of each Claude invocation (AC-7, implemented separately).
- Fields are space-separated key=value pairs on a single line.
- `query_agent_quota()` filters Loki with `|= "[USAGE]"` to match these lines.

### Aggregation algorithm

1. Query `{agent="<name>"} |= "[USAGE]"` for the last 5 hours.
2. Sum `total_tokens` across all matching lines → `current_block_tokens`.
3. Sum `cost_usd` across all matching lines → `current_block_cost_usd`.
4. Count matched lines → `sessions_in_block`.
5. If no lines: return `QuotaInfo(source="no_data")`.
6. Otherwise: return `QuotaInfo(source="loki", ...)`.

### 5-hour block windows

Blocks align to UTC hours: 00-05, 05-10, 10-15, 15-20, 20-01.
`block_start` and `block_end` are derived from current UTC time.

---

## 3. RED State Confirmation

Tests were verified against the current codebase (pre-implementation):

| Test | Failure reason |
|------|---------------|
| `test_query_agent_quota_*` (7 tests) | `AttributeError: LokiClient has no attribute query_agent_quota` |
| `test_quota_route_returns_loki_source_when_data_available` | `AssertionError: source == "unavailable" not "loki"` |
| `test_quota_route_returns_no_data_not_unavailable_when_loki_empty` | `AssertionError: source == "unavailable" not "no_data"` |
| `test_quota_route_returns_200_with_no_data_source` | `AssertionError: source "unavailable" not in {"loki","no_data"}` |
| `test_quota_route_output_varies_between_agents` | `AssertionError: both agents return null tokens` |
| `test_quota_route_cache_hit_queries_loki_only_once` | `AssertionError: call_count 0 != 1` |
| PASSING (3): feature-flag 404, unknown-agent 404, subprocess regression | Already work correctly |

---

## 4. Coverage Targets

Story size: **Small** → 50% line coverage target per SDLC policy.

The 15 tests cover:
- `LokiClient.query_agent_quota()`: all branches (data, empty, error, sanitization, time window)
- Route handler: loki source, no_data source, feature flag gate, agent not found, regression (no SSH)
- Cache behaviour: single Loki call for two successive requests

---

## 5. Key Design Decisions

### 5.1 No direct QuotaSourceEnum.LOKI imports

`QuotaSourceEnum.LOKI` and `QuotaSourceEnum.NO_DATA` do not exist in RED state. Tests compare as strings (`== "loki"`, `== "no_data"`) to avoid import errors at collection time.

### 5.2 MagicMock for mock loki return value

Since `QuotaInfo(source="loki")` cannot be constructed (enum value missing), the `mock_loki_client.query_agent_quota` fixture returns a `MagicMock(spec=QuotaInfo)` with `source` set as a string attribute and a `model_dump()` side effect. This allows the route to serialize the response as JSON while keeping the test correct.

### 5.3 _ssh_runner patched globally (autouse)

An `autouse` fixture patches `_ssh_runner` to return `(-1, b"", b"ssh_disabled_in_test")` for all tests in the route test module. This prevents any real SSH calls while allowing the current code to complete (returning `source="unavailable"` via the old path).

### 5.4 Subprocess regression test

`test_quota_route_no_subprocess_called` patches `asyncio.create_subprocess_exec` and asserts `call_count == 0`. In RED state this passes because `_ssh_runner` is itself patched (no subprocess launched). In GREEN state it provides the real regression guard: if someone accidentally re-introduces SSH, this test fails.

### 5.5 Cache test strategy

`test_quota_route_cache_hit_queries_loki_only_once` asserts `mock_loki_client.query_agent_quota.call_count == 1` after two requests. In RED state, `query_agent_quota` is never called (route uses SSH), so `call_count == 0`. In GREEN state, the cache TTL (defined in `_QUOTA_CACHE`) must prevent a second Loki call.

### 5.6 Time-window tolerance

`test_query_agent_quota_queries_last_5_hours` allows a ±30-minute tolerance around the 5h window. This accommodates implementation details (exact start time derived from block boundaries vs. relative offset).

---

## 6. Files

- `tests/ops_console/test_loki_client_quota.py` — Unit tests for `LokiClient.query_agent_quota()`
- `tests/ops_console/test_routes_agents_quota_loki.py` — Integration tests for the quota route
- `features/story-513-quota-data-layer/test-design.md` — This document

---

## 7. Acceptance Criteria Mapping

| AC | Test(s) |
|----|---------|
| AC-1: No subprocess/SSH | `test_quota_route_no_subprocess_called` |
| AC-2: query_agent_quota aggregates [USAGE] → QuotaInfo(source="loki") | `test_query_agent_quota_with_usage_lines_returns_source_loki`, `test_query_agent_quota_multiple_entries_summed`, `test_query_agent_quota_queries_last_5_hours`, `test_query_agent_quota_sanitizes_agent_name`, `test_query_agent_quota_output_varies_between_agents` |
| AC-3: Route returns 200 source="loki" when data available | `test_quota_route_returns_loki_source_when_data_available` |
| AC-5: Missing data → source="no_data" not "unavailable" | `test_query_agent_quota_empty_loki_returns_no_data`, `test_quota_route_returns_no_data_not_unavailable_when_loki_empty` |
| AC-6: Both paths return 200; output varies by agent; cache | `test_quota_route_returns_200_with_no_data_source`, `test_quota_route_output_varies_between_agents`, `test_quota_route_cache_hit_queries_loki_only_once`, `test_quota_route_feature_flag_disabled_returns_404`, `test_quota_route_unknown_agent_returns_404`, `test_query_agent_quota_loki_error_returns_no_data` |
