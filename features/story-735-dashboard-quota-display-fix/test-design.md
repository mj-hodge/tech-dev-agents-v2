# STORY-735 Test Design — Dashboard Quota Display Fix

**Scope:** Small
**Coverage target:** 50%
**Test tool:** Vitest (pure logic + component rendering via jsdom/@testing-library/react)
**Backend:** Existing regression test only (SC-5) — no new backend tests needed

## Test Structure

```
frontend/src/components/
  AgentCard.test.tsx          — 32 tests (18 RED, 14 GREEN)

tests/ops_console/
  test_loki_client_quota.py   — SC-5 regression (existing, verified GREEN)
```

## Test Categories

### 1. Pure Logic: formatResetTime (7 tests — RED)

**Purpose:** Verify the new `formatResetTime(minutes)` utility converts integer minutes to human-readable "Xh Ym" or "Xm" strings.

| Test | What It Verifies |
|------|------------------|
| `converts 83 minutes to "1h 23m"` | Standard hours+minutes conversion |
| `converts 60 minutes to "1h 0m"` | Exact hour boundary |
| `converts 45 minutes to "45m"` | Sub-hour, minutes only |
| `converts 0 minutes to "0m"` | Zero edge case |
| `returns fallback for null input` | Null-safe, returns "—" |
| `returns fallback for undefined input` | Undefined-safe, returns "—" |
| `converts 300 minutes to "5h 0m"` | Full 5h block boundary |

**RED reason:** `formatResetTime` not yet exported from AgentCard.tsx.

### 2. Pure Logic: formatTokens (5 tests — GREEN)

**Purpose:** Verify existing `formatTokens` utility behavior is preserved (regression guard).

| Test | What It Verifies |
|------|------------------|
| `formats null as dash` | Null input returns "—" |
| `formats 0 as "0"` | Zero renders correctly |
| `formats 125000 as "125K"` | Thousands abbreviation |
| `formats 1500000 as "1.5M"` | Millions abbreviation |
| `formats 500 as "500"` | Sub-thousand renders raw |

### 3. SC-2: Fallback Indicator — p90_limit null (4 tests — 2 RED, 2 GREEN)

**Purpose:** When `p90_limit` is null, an asterisk/warning indicator with tooltip signals the 200K is an estimated baseline.

| Test | What It Verifies | State |
|------|------------------|-------|
| `shows estimated-baseline indicator when p90_limit is null` | `data-testid="quota-estimated-indicator"` present | RED |
| `indicator has tooltip explaining 200K estimated baseline` | Tooltip matches /estimated/ and /200K/ | RED |
| `does NOT show indicator when p90_limit has a value` | Indicator absent when limit is real | GREEN |
| `does NOT show indicator when quota is null` | No quota = no indicator | GREEN |

### 4. SC-3: Reset Time Display (5 tests — 3 RED, 2 GREEN)

**Purpose:** Each quota card shows time remaining until block reset, sourced from `reset_in_minutes`.

| Test | What It Verifies | State |
|------|------------------|-------|
| `shows formatted reset time on the card` | "1h 23m" visible for reset_in_minutes=83 | RED |
| `shows reset time label "resets in"` | Label text present | RED |
| `handles reset_in_minutes = 0` | "0m" renders correctly | RED |
| `falls back gracefully when reset_in_minutes is null` | No NaN, no crash | GREEN |
| `does not render reset time when quota is null` | No reset label when no quota | GREEN |

### 5. SC-4: sessions_in_block Prominence (4 tests — 3 RED, 1 GREEN)

**Purpose:** Each quota card shows session count prominently.

| Test | What It Verifies | State |
|------|------------------|-------|
| `shows sessions count on the card` | "3 sessions" text visible | RED |
| `shows sessions_in_block = 1 as singular` | "1 session" text visible | RED |
| `shows sessions_in_block = 0` | "0 sessions" text visible | RED |
| `does not render sessions when quota is null` | No sessions text when no quota | GREEN |

### 6. SC-1: Per-Agent Token Differentiation (4 tests — 3 RED, 1 GREEN)

**Purpose:** `current_block_tokens` rendered prominently so different agents show different values.

| Test | What It Verifies | State |
|------|------------------|-------|
| `renders current_block_tokens as formatted value` | "125K" visible on card | RED |
| `two agents render different token values` | dan=125K, derrick=42K produce different card text | RED |
| `renders tokens alongside limit denominator` | "125K" and "200K" both visible | RED |
| `does not render token count when quota is null` | No token display when no quota | GREEN |

### 7. Edge Cases: Quota Robustness (3 tests — all GREEN)

**Purpose:** Card never crashes, regardless of quota shape.

| Test | What It Verifies |
|------|------------------|
| `renders without crashing when quota is undefined` | Default agent (no quota key) |
| `renders without crashing when all quota fields are null` | Full null QuotaInfo |
| `renders with source="no_data" without details` | no_data source hides token/sessions text |

### 8. SC-5: Block-Aligned Query Regression (1 existing test — GREEN)

**Location:** `tests/ops_console/test_loki_client_quota.py::TestLokiClientQueryAgentQuota::test_query_agent_quota_queries_current_block`

**Verified:** 2026-04-27 — PASSED. Asserts query start aligns to boundary hour (0/5/10/15/20 UTC), window ≤ 5h, end ≈ now.

## Summary

| Category | Tests | RED | GREEN |
|----------|-------|-----|-------|
| formatResetTime (pure) | 7 | 7 | 0 |
| formatTokens (regression) | 5 | 0 | 5 |
| SC-2: p90_limit fallback | 4 | 2 | 2 |
| SC-3: reset time | 5 | 3 | 2 |
| SC-4: sessions count | 4 | 3 | 1 |
| SC-1: token display | 4 | 3 | 1 |
| Edge cases | 3 | 0 | 3 |
| SC-5: backend regression | 1 | 0 | 1 |
| **Total** | **33** | **18** | **15** |

## RED Reasons

All 18 failures are because AgentCard.tsx does **not yet render quota fields** — it only renders Azure Spend and presence info. Phase 8 will add:

1. Export `formatResetTime()` utility function
2. Render `current_block_tokens` / limit denominator on the card
3. Add `data-testid="quota-estimated-indicator"` with tooltip when `p90_limit === null`
4. Render "resets in Xh Ym" from `reset_in_minutes`
5. Render "N session(s)" from `sessions_in_block`

No import errors, no type errors. All 32 Vitest tests collect and run cleanly.

## Gates

- [x] No `pytest.raises(ImportError)` patterns
- [x] Every test calls real function/renders real component — mocks only for router context
- [x] Output-variance test: two agents with different tokens produce different card content
- [x] Null/None boundary: null quota, null p90_limit, null reset_in_minutes, all-null fields tested
- [x] Static analysis: no eslint config in project — N/A
- [x] SC-5 backend regression verified GREEN
- [x] Test isolation: single file runs independently
