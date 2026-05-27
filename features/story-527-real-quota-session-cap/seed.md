# STORY-527: Replace session-count cap with real token quota check

> Phase 1 | Scope: **Small** | Created: 2026-04-22
> Advance: auto (automated dispatch path — 1 → 7 → 8+PR → Done)

---

## Problem Statement

The daily session cap in `sdlc_phase_runner.py` (`_check_daily_session_cap`, line 370) uses an arbitrary 80 sessions/day hard limit as a proxy for "preserve weekly quota." This is a fiction — it counts SDK invocations, not actual token consumption. Many sessions are short resume-skipped runs that consume negligible tokens.

**2026-04-22 incident (13:20 UTC):** Daisy hit 80/80 sessions and was auto-paused while she still had **42 million tokens remaining** in her 5-hour billing block (67% used, 33% remaining per `ccusage blocks --json`). The cap fired on a false signal and took an agent offline for real work she could have completed.

The fix: replace the session-count heuristic with a real quota check using `ccusage blocks --json` — the same tool already used in `dispatch_poller.py` for the pre-claim rate-limit probe. Pause only when the **actual** quota signal indicates near-exhaustion.

---

## Target User

- **Daisy / Morris** — agents that get incorrectly paused mid-day due to the false session-count signal
- **Mark** — operator who loses agent throughput to false-positive pauses

---

## Acceptance Criteria

1. **AC-1** — `_check_daily_session_cap()` calls `ccusage blocks --json`, parses the active billing block, and bases its decision on **remaining tokens** rather than session count. Returns `True` (OK to proceed) when the active block has sufficient tokens remaining.

2. **AC-2** — When `ccusage` reports **low remaining tokens** (e.g., < 5M tokens remaining) AND more than 15 minutes remain until the block resets, return `False` (pause). This prevents starting work that will hit a hard rate limit.

3. **AC-3** — When fewer than 15 minutes remain until the block resets (regardless of remaining tokens), return `False`. Don't start work that can't finish before the rate-limit window resets.

4. **AC-4** — Fail-open behavior: if `ccusage` is unavailable (subprocess error, binary not found, malformed output), return `True` and log a warning. Never block legitimate work due to instrumentation failure.

5. **AC-5** — When `ccusage` returns no active billing block (rare edge case), return `True` and log a warning.

6. **AC-6** — The `DAILY_SESSION_CAP` env var is retained as a **fallback ceiling** (default raised to 300) for genuinely runaway scenarios. The session count is still tracked and enforced, but at a level that only catches true runaways — not normal workloads.

7. **AC-7** — Existing session-count increment logic is preserved (write to `sdk-sessions-today.count`), so the count file remains available for observability and the fallback ceiling.

---

## Scope Classification

**Small** — single function rewrite in `sdlc_phase_runner.py`, reusing patterns already established in `dispatch_poller.py`. No new dependencies, no schema changes, no cross-service coordination.

Phase path (automated dispatch): **1 → 7 → 8+PR → Done**.

---

## Technical Notes

### Existing ccusage pattern (dispatch_poller.py, lines 984–1024)

The pre-claim rate-limit check already:
- Runs `ccusage blocks --json` with 15s timeout
- Parses JSON for active blocks via `b.get("isActive")`
- Reads `totalTokens` and `resets_at` fields
- Implements 15-minute remaining-time threshold
- Fails open on any exception

The `_check_daily_session_cap` rewrite should extract a shared helper or duplicate the same pattern locally (prefer DRY if feasible without over-engineering).

### Key fields from ccusage blocks JSON

| Field | Type | Description |
|-------|------|-------------|
| `isActive` | bool | Whether this is the current billing block |
| `totalTokens` | int | Tokens consumed in this block |
| `remainingTokens` | int | Tokens remaining (if available) |
| `resets_at` | float | Unix timestamp when block resets |

### Token threshold

Use a configurable env var `QUOTA_LOW_TOKEN_THRESHOLD` (default: 5,000,000) for the "low remaining tokens" boundary. This allows tuning without code changes.

### Key files

| File | Change |
|------|--------|
| `deployment/hermes/sdlc_phase_runner.py` | Rewrite `_check_daily_session_cap()` to query ccusage |
| `features/story-527-real-quota-session-cap/test-design.md` | 5 pytest test cases |

---

## Out of Scope

- Changes to `dispatch_poller.py` (its pre-claim check is separate and already works)
- Dashboard/ops-console quota display (covered by STORY-513)
- Cross-agent quota aggregation
- Changing the ccusage tool itself

---

## Success Measure

After deploy: an agent with 150+ sessions but plentiful token quota is **not** paused. An agent approaching token exhaustion **is** paused regardless of session count. Zero false-positive pauses over a 4-hour observation window with real Medium-scope stories.
