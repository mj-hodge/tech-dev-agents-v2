# STORY-764 — Test Design: Strip Prior `[RETRY N/N]` Prefix Before Adding New One

## Scope & Coverage

| Field | Value |
|-------|-------|
| Scope | Small |
| Coverage target | 60% of affected code paths |
| Test file | `tests/deployment/test_retry_prefix_strip.py` |
| Test runner | `pytest` |
| Timeout | 15s per test (pyproject.toml) |

## Test Structure

```
tests/deployment/
└── test_retry_prefix_strip.py    # 11 tests across 5 groups (A–E)
```

## Acceptance Criteria → Test Mapping

| AC | Test(s) |
|----|---------|
| AC-1: `_strip_retry_prefix` helper exists | A1 |
| AC-2: Regex `^\s*\[RETRY \d+/\d+\]\s*` | A1–A6 |
| AC-3: Idempotent (no prefix → unchanged) | A2 |
| AC-4: Every add-site calls helper first | B1 |
| AC-5: Edge cases (0, 100, 4990, 5000 chars) | A3–A6 |
| AC-6: 3-retry-cycle cumulative length ≤ 5000 | C1 |
| AC-7: Zero regressions | (full suite — separate run) |
| AC-8: Logging post-strip length | D1 |
| AC-9: Overflow guard — skip retry + warn | E1 |

## Test Groups

### Group A — `_strip_retry_prefix` helper (unit tests)

| Test | What It Verifies |
|------|------------------|
| `test_strip_simple_retry_prefix` | Strips `[RETRY 1/3] ` from start, preserves rest |
| `test_strip_no_prefix_returns_unchanged` | Idempotent — prompt without prefix returned unchanged |
| `test_strip_double_digit_retry_prefix` | Handles `[RETRY 10/10] ` (multi-digit) |
| `test_strip_leading_whitespace_before_prefix` | Handles `  [RETRY 2/3] ` with leading spaces |
| `test_strip_no_trailing_space_after_bracket` | Handles `[RETRY 1/3]prompt` (no space after `]`) |
| `test_strip_does_not_mangle_mid_prompt_retry` | `[RETRY 1/3]` in middle of prompt is NOT stripped |

### Group B — Integration: `_report_fail` calls helper

| Test | What It Verifies |
|------|------------------|
| `test_report_fail_retry_uses_strip_helper` | The retry prompt passed to POST /api/dispatch has the old prefix stripped and the new one added |

### Group C — Regression: length stability across retries

| Test | What It Verifies |
|------|------------------|
| `test_three_retry_cycles_stay_under_5000` | Starting with a 4990-char prompt, after 3 retry cycles the prompt length never exceeds 5000 |

### Group D — Logging (AC-8)

| Test | What It Verifies |
|------|------------------|
| `test_retry_logs_prompt_length_after_strip` | stdout contains `retry prompt length: N chars (after strip)` |

### Group E — Overflow guard (AC-9)

| Test | What It Verifies |
|------|------------------|
| `test_retry_skips_when_prompt_still_exceeds_5000` | When a 5000-char prompt + new prefix would exceed limit, retry is skipped and warning is logged |

## Output-Variance

Not applicable — this is a string-transform helper, not an endpoint that returns computed data. Group A tests inherently verify different inputs produce different outputs.

## API Mock Verification

No route mocks needed — all tests mock `session.post` directly at the Python level (same pattern as existing `test_dispatch_poller_retry_classification.py`).

## RED State Rationale

All tests will be RED because:
- `_strip_retry_prefix` does not yet exist as a named function (it's an inline `re.sub` on line 654)
- AC-8 logging line does not yet exist
- AC-9 overflow guard does not yet exist
- Group B asserts the helper is called explicitly (not inline regex)
