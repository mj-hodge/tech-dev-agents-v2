# Phase 8b: Code Review -- STORY-022 Ops Dashboard Data Pipeline Fix

**Date:** 2026-04-07
**Reviewer:** Code Review Agent (Sonnet)
**Branch:** story-021/work-queue (contains STORY-022 commits)
**Test Results:** 18/18 Python GREEN, 21/21 TypeScript GREEN, 0 regressions

---

## Summary

STORY-022 fixes three categories of bugs preventing the ops dashboard from displaying real data: (1) MCP client envelope unwrapping, (2) cost tracking regex + systemd timer, and (3) agent detail cost enrichment. All changes are backend-only. The implementation closely follows the feature spec with no scope creep.

---

## Findings

| ID | Severity | File | Finding | Recommendation |
|----|----------|------|---------|----------------|
| F-01 | **Low** | `routes/agents.py:168-176` | `story_task` and `cost_today_task` are fired via bare `asyncio.create_task()` inside `asyncio.gather()` without try/except wrappers. If `monday_service.get_current_story()` or `cost_service.get_today_cost()` raises, the entire `gather()` will propagate the exception and return a 500 to the caller. The 7d/30d cost tasks are correctly wrapped in `_safe_cost_total`, but the other two are not. | Wrap `story_task` and `cost_today_task` in similar safe helpers, or use `asyncio.gather(..., return_exceptions=True)` and handle exceptions post-gather. This is low severity because: (a) the list endpoint already handles these failures gracefully, (b) a 500 on the detail page is acceptable-but-not-ideal degradation, and (c) the feature spec explicitly listed graceful degradation only for cost breakdown queries. |
| F-02 | **Info** | `routes/agents.py:168-176` | Tasks are created with `asyncio.create_task()` and then immediately passed to `asyncio.gather()`. This is redundant -- `asyncio.gather()` accepts coroutines directly and wraps them in tasks internally. The current code works correctly but the double-wrapping is unnecessary. | Consider passing coroutines directly to `asyncio.gather()` for cleaner code. No functional impact. |
| F-03 | **Low** | `loki_client.py:237` | `_DONE_COST_RE = re.compile(r"cost=\$?(?P<cost>[\d.]+)")` will match any `cost=` substring anywhere in the line, including hypothetical fields like `forecasted_cost=`. The `(?<!\w)` negative lookbehind used on `_DONE_TURNS_RE` is not applied to `_DONE_COST_RE`. | Add `(?<!\w)` lookbehind to `_DONE_COST_RE` for consistency: `re.compile(r"(?<!\w)cost=\$?(?P<cost>[\d.]+)")`. Low severity because current log formats do not contain such fields. |
| F-04 | **Info** | `loki_client.py:263` | The `"[DONE]" not in line` check is a fast-path guard that prevents regex execution on non-DONE lines. Good defensive practice. | No action needed -- noting as positive. |
| F-05 | **Info** | `client.ts:84,121,146` | All three envelope unwrapping methods use `Array.isArray()` guard with empty-array fallback. This is correct defensive coding that handles both malformed responses and type mismatches gracefully. | No action needed -- noting as positive. |
| F-06 | **Info** | `responses.py:97-98` | `cost_7d` and `cost_30d` default to `0.0`, making them additive non-breaking fields. Existing API consumers that do not read these fields are unaffected. | No action needed -- backwards compatible. |
| F-07 | **Info** | `cost-collector.timer` | `Persistent=true` ensures catch-up on missed runs after reboot. `OnCalendar=*-*-* 23:55:00` runs at end-of-day UTC. Both are appropriate for daily cost aggregation. | No action needed. |
| F-08 | **Low** | `cost-collector.service:8` | The `ExecStart` path is hardcoded to `/opt/agent/cost_collector.py`. If the deployment path changes, this service will silently fail. There is no `Restart=` directive, so a transient failure (e.g., Loki timeout) will not be retried until the next day. | Consider adding `Restart=on-failure` with `RestartSec=300` for transient failures. Low severity because daily aggregation can tolerate a missed day (Persistent=true catches up). |
| F-09 | **Info** | `test_story022_data_pipeline.py` | Test coverage is solid: 6 tests for regex parsing (both field orders, auth failure skip, missing turns, prefix handling, no-dollar-sign), 3 tests for systemd files, 3 tests for model fields. All ACs are covered. | No action needed. |
| F-10 | **Low** | `test_story022_data_pipeline.py:109` | `test_cost_collector_timer_has_persistent` silently passes if the file does not exist (`if timer_path.exists()` guard). The preceding test `test_cost_collector_systemd_timer_exists` would catch this, but the guard means this test provides no signal if run in isolation. | Remove the `if timer_path.exists()` guard -- let the test fail explicitly if the file is missing, since the assertion is the point of the test. |

---

## Review by Category

### 1. Correctness

**PASS.** The core bug -- `_DONE_RE` requiring `cost=` before `turns=` -- is correctly fixed by splitting into two independent field-extraction regexes. The MCP client envelope unwrapping matches the actual backend response shapes. The `cost_7d`/`cost_30d` enrichment correctly calls `cost_service.get_cost_breakdown()` and extracts `total_cost_usd`.

### 2. Defensive Coding

**PASS with note.** Edge cases are well-handled: auth failure lines (`cost=$?`) are skipped, missing `turns=` defaults to 0, envelope fields are guarded with `Array.isArray()`, and cost breakdown failures degrade to `0.0`. The one gap is F-01 (story/cost_today tasks in the detail endpoint lack error wrapping), which is acceptable for this scope.

### 3. Performance

**PASS.** The detail endpoint now fires 5 I/O calls concurrently via `asyncio.gather()` instead of sequentially. This reduces the endpoint latency from ~sum(all calls) to ~max(slowest call). The list endpoint still fetches story/cost sequentially per agent (F-02 pattern), but that is pre-existing and out of scope for this story.

### 4. Test Coverage

**PASS.** All acceptance criteria are covered:
- **AC-1** (envelope unwrapping): Covered by TypeScript test suite (21/21 GREEN).
- **AC-2** (regex fix + timer): 6 regex tests + 3 systemd file tests.
- **AC-3** (cost enrichment): 3 model/default tests.
- No integration tests for the concurrent gather path, but unit tests verify the data flow.

### 5. Backwards Compatibility

**PASS.** All changes are additive:
- New response fields (`cost_7d`, `cost_30d`) have defaults, so existing consumers are unaffected.
- MCP client changes are client-side only; the API contract is unchanged.
- The regex fix is strictly more permissive (accepts both field orders).
- New systemd files do not conflict with existing services.

---

## Verdict

### APPROVED

All acceptance criteria are met. The implementation matches the feature spec with good defensive coding patterns. The three low-severity findings (F-01, F-03, F-08) are noted for future improvement but do not block merge. F-10 is a minor test hygiene item.

No blocking issues. Ship it.
