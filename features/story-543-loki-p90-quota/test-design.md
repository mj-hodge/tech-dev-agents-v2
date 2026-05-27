# STORY-543 — Test Design

## Scope

Unit tests for the P90 token quota baseline feature. All tests mock Loki HTTP responses and verify computation logic.

## Test Cases

### 1. `_compute_p90()` pure function tests

| # | Test | Input | Expected |
|---|------|-------|----------|
| 1a | Happy path — 10 blocks | `[100K, 120K, 130K, ..., 200K]` | P90 = 90th percentile value |
| 1b | Exactly 3 blocks (minimum) | `[100K, 150K, 200K]` | Returns a value (P90 of 3 items) |
| 1c | Fewer than 3 blocks | `[100K, 150K]` | Returns `None` |
| 1d | Empty list | `[]` | Returns `None` |
| 1e | All identical values | `[100K] * 10` | Returns `100K` |

### 2. `query_historical_block_tokens()` integration tests

| # | Test | Setup | Expected |
|---|------|-------|----------|
| 2a | 8 days of usage data | Mock Loki returns entries spanning 8 days | Returns list of per-block totals, grouped by 5h block |
| 2b | Loki error | Mock returns 500 | Returns empty list (graceful degradation) |
| 2c | No usage entries | Mock returns empty streams | Returns empty list |

### 3. Updated `query_agent_quota()` with P90

| # | Test | Setup | Expected |
|---|------|-------|----------|
| 3a | P90 available | Mock historical query yields P90=180K, current block=100K | `remaining_tokens=80K`, `p90_limit=180K`, `percent_used ~55.6` |
| 3b | P90 fallback (insufficient data) | Fewer than 3 historical blocks | `remaining_tokens` based on 200K default, `p90_limit=None` |
| 3c | P90 fallback (Loki error on history) | Historical query fails | Falls back to 200K, current block still works |
| 3d | P90 cached | Two calls within 1h | Historical query called only once |
| 3e | P90 cache expired | TTL elapsed | Historical query called again |

## Test Utilities

- Reuse existing `_make_loki_response()`, `_make_usage_stream()` helpers from test file
- Add `_make_historical_usage_entries()` to generate multi-day usage data
