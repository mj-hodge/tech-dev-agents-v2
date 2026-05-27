# Seed: STORY-625 — Silent 429 Rate-Limit Detection

## Problem Statement

When Anthropic rate-limits at the HTTP API level (fast 429 rejection), the Claude Code CLI exits in under 5 seconds with `rc=1`, `stop=stop_sequence`, `error=True`, `turns=1`, `tools=0` — but never prints the friendly "hit your limit" message that the current detection logic relies on.

The rate-limit detection in `deployment/hermes/sdlc_phase_runner.py` (lines 1016-1018) only checks for the literal string `"hit your limit"` in `all_output`. When a fast API-level 429 occurs before the CLI renders that message, `rate_limited` stays `False`, no pause flag is written to `/var/run/dispatch-poller-paused-until`, and the poller keeps re-claiming stories every 6 minutes — burning retries against the same rate-limit wall.

## Target User

Autonomous dev agents (Dan, Derrick, Daisy, Devon) running via the dispatch poller.

## Success Criteria

1. When the SDK exits fast (<10s) with `rc=1` and minimal/empty output (no "hit your limit" text), the phase runner treats it as a rate-limit event.
2. The pause flag is written with `reset_time=unknown`, which triggers the 1-hour conservative cap in `_is_paused()` in `dispatch_poller.py`.
3. The poller stops re-claiming stories during the rate-limit window.
4. Legitimate fast exits (e.g., resume-detected-already-complete at rc=0) are NOT false-positived.

## Scope Classification

**Small** — isolated change to a single detection block in one file (`sdlc_phase_runner.py`), plus a test file.

### Rationale
- Single component affected: rate-limit detection block in `run_phase()`
- No API/DB changes
- No architectural impact
- Existing pause-flag infrastructure already handles `reset_time=unknown`

## Affected Files

| File | Change |
|------|--------|
| `deployment/hermes/sdlc_phase_runner.py` | Add fallback heuristic after line 1019 (after existing "hit your limit" check) |
| `tests/deployment/test_poller_rate_limit_recovery.py` | Add new test group (G) for silent-429 scenario |

## Technical Analysis

### Current Behavior (lines 1016-1018)
```python
rate_limited = (
    "hit your limit" in all_output.lower()
    or "you've hit your limit" in all_output.lower()
)
```

### Root Cause
The Claude Code CLI sometimes gets a 429 at the HTTP transport level before it has a chance to render the user-facing "hit your limit" message. The CLI exits with `rc=1` but the output contains only raw SDK metadata (`stop_sequence`, `error=True`, `turns=1`, `tools=0`) or is nearly empty (<200 chars).

### Proposed Fix
After the existing check (line 1019), add a fallback heuristic:

```python
if not rate_limited and duration < 10 and proc.returncode != 0:
    output_len = len(all_output.strip())
    has_error_markers = (
        "stop_sequence" in all_output
        or "error" in all_output.lower()[:200]
    )
    if output_len < 200 or has_error_markers:
        rate_limited = True
        reset_time = "unknown"  # triggers 1h conservative cap
        print(
            f"[DISPATCH] SILENT RATE LIMIT detected — rc={proc.returncode}, "
            f"duration={duration}s, output_len={output_len}. "
            f"Writing pause flag with reset_time=unknown.",
            flush=True,
        )
```

### Why This Is Safe
1. **rc=0 is excluded** — legitimate fast completions (resume-detected-already-complete) exit with `rc=0` per the 2026-04-22 false-positive incident documented in the code comments.
2. **Duration < 10s gate** — real phase work always takes >10 seconds even if it fails for non-rate-limit reasons (network errors, syntax errors, etc. all involve SDK startup time).
3. **The "unknown" reset_time** is already handled by `_is_paused()` in `dispatch_poller.py` — it applies a 1-hour conservative cap based on file mtime.

## Acceptance Criteria

| # | Criterion | Verification |
|---|-----------|-------------|
| AC-1 | Fallback heuristic fires when: `rate_limited=False AND duration<10 AND rc!=0 AND (output_len<200 OR has_error_markers)` | Unit test |
| AC-2 | Pause flag written with `reset_time=unknown` on silent 429 | Unit test |
| AC-3 | rc=0 fast exits are NOT treated as rate-limited (no regression of 2026-04-22 false positive) | Unit test |
| AC-4 | Normal failures (rc!=0, duration>10s) are NOT treated as rate-limited | Unit test |
| AC-5 | Informational log line printed for silent rate-limit detection | Code review |
| AC-6 | Existing "hit your limit" detection still works (no regression) | Existing tests |

## Phase Path

Small scope: **1 → 7 → 8 → Done**

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Heuristic threshold | duration < 10s | Real SDK sessions take >10s for startup alone; API-level 429s exit in <5s |
| Output threshold | < 200 chars | Normal phase output is thousands of chars; rate-limit exits produce minimal output |
| Reset time | "unknown" | Existing infrastructure handles this with 1h conservative cap; no new code needed |
| rc gate | rc != 0 only | Prevents false positive on legitimate fast rc=0 exits (lesson learned 2026-04-22) |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| False positive on non-rate-limit fast failures | Low | Medium (1h unnecessary pause) | rc=0 excluded; duration<10 + minimal output is a strong signal; 1h cap is bounded |
| Future SDK output format changes | Low | Low | Heuristic is broad (output length + error markers); not fragile to exact format |

**Frontend:** false

## Test Criteria

- Silent 429 (rc=1, duration<10s, minimal output) triggers rate-limit pause flag
- rc=0 fast exits are NOT false-positived
- Normal failures (rc!=0, duration>10s) are not treated as rate-limited
- Pause flag is written with `reset_time=unknown`

## Validation

Run `pytest tests/deployment/test_poller_rate_limit_recovery.py` — all tests including new silent-429 group pass.
