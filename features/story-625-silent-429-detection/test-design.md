# Test Design: STORY-625 — Silent 429 Rate-Limit Detection

**Phase:** 7 (Test Design)
**Scope:** Small
**State:** RED — 3 FAIL, 4 PASS (regression guards), 17 pre-existing unaffected

---

## Coverage Target

The fix adds a fallback heuristic in `_run_phase_sdk()` in `deployment/hermes/sdlc_phase_runner.py`.
Tests are added to the existing `tests/deployment/test_poller_rate_limit_recovery.py` as **Group G**.

---

## Test File

`tests/deployment/test_poller_rate_limit_recovery.py` → class `TestSilent429Detection`

---

## Test Matrix

| ID | Name | Type | AC | Expected State |
|----|------|------|----|----------------|
| G1 | `test_fallback_heuristic_log_line_present` | grep | AC-5 | **RED** — "SILENT RATE LIMIT" not in source yet |
| G2 | `test_fallback_checks_duration_threshold` | grep | AC-1 | **RED** — `duration < 10` not in source yet |
| G3 | `test_fallback_gates_on_nonzero_returncode` | grep | AC-3 | GREEN — `returncode != 0` already present elsewhere |
| G4 | `test_fallback_sets_unknown_reset_time` | grep | AC-2 | GREEN — `"unknown"` already in existing path |
| G5 | `test_silent_429_returns_minus_429` | behavioral | AC-1, AC-2 | **RED** — returns 1, should return -429 |
| G6 | `test_rc0_fast_exit_not_rate_limited` | behavioral | AC-3 | GREEN — existing code correctly doesn't rate-limit rc=0 |
| G7 | `test_slow_failure_not_rate_limited` | behavioral | AC-4 | GREEN — existing code correctly doesn't rate-limit slow exits |

**RED tests (3):** G1, G2, G5 — all fail for the right reason (implementation absent)  
**Regression guards (4 GREEN):** G3, G4, G6, G7 — verify existing correct behavior is preserved

---

## Test Groups

### Group G — Silent 429 Detection (all 7 tests, class `TestSilent429Detection`)

#### Grep-level assertions (G1–G4)

These verify implementation artifacts exist in source after Phase 8:

- **G1** — `"SILENT RATE LIMIT"` (or lowercase) in `sdlc_phase_runner` source.  
  The fallback must emit an identifiable log line so Morris can diagnose incidents.

- **G2** — `"duration < 10"` in source.  
  The fast-exit threshold that separates API-level 429s (<5s) from real phase failures (SDK startup alone >10s).

- **G3** — `"returncode != 0"` in source.  
  The rc guard that prevents false positives on rc=0 fast exits (2026-04-22 incident regression guard).

- **G4** — `"unknown"` in source.  
  `reset_time = "unknown"` activates the 1-hour mtime-based conservative cap in `_is_paused()` (dispatch_poller.py ~line 187).

#### Behavioral tests (G5–G7)

These call `_run_phase_sdk()` with a mocked subprocess and verify the return code.

**Mock setup (shared helper `_run_with_mocked_subprocess`):**
- `subprocess.run` → `MagicMock(returncode=<rc>, stderr=<stderr>)`
- `time.time` → `side_effect=[start, start+duration]` to control `duration`
- `_save_partial_work`, `_capture_session_id_from_log`, `_emit_event` → no-op MagicMocks
- `_read_story_session_id` → returns `None` (no prior session)
- No mock for glob (naturally returns [] in test env), no mock for open (pause flag write silently swallowed by try/except)

**G5 — Silent 429 returns -429:**
```
Input:  returncode=1, stderr="stop_sequence error=True turns=1 tools=0", duration=3s
Expect: rc == -429
Why:    rc=1 + duration<10 + output<200 chars + no "hit your limit" → fallback fires
```

**G6 — rc=0 fast exit NOT rate-limited (regression guard):**
```
Input:  returncode=0, stderr="", duration=3s
Expect: rc == 0
Why:    rc=0 exits are legitimate (resume-detect-already-complete); 
        heuristic must gate on rc!=0 (2026-04-22 false-positive lesson)
```

**G7 — Slow failure NOT rate-limited:**
```
Input:  returncode=1, stderr="", duration=60s
Expect: rc == 1
Why:    Real failures take >10s; heuristic must gate on duration<10
```

---

## Acceptance Criteria Mapping

| AC | Description | Tests |
|----|-------------|-------|
| AC-1 | Fallback fires on: `rate_limited=False AND duration<10 AND rc!=0 AND (output<200 OR error_markers)` | G2, G5 |
| AC-2 | Pause flag written with `reset_time=unknown` | G4, G5 |
| AC-3 | rc=0 fast exits NOT treated as rate-limited | G3, G6 |
| AC-4 | Normal slow failures (rc!=0, duration>10s) NOT rate-limited | G7 |
| AC-5 | Informational log line present | G1 |
| AC-6 | Existing "hit your limit" detection not broken | pre-existing tests |

---

## Pre-existing Tests (no changes, all GREEN)

All 17 pre-existing tests in Groups A–F continue to pass:
- Group A (3): Pre-claim probe removal
- Group B (5): Runtime 429 handling
- Group C (3): Release-and-idle on environmental failure
- Group D (3): Morris dispatch-poller disabled by default
- Group E (4): Session cap removal
- Group F (2): No systemctl disable

---

## RED State Verification

```
$ python3 -m pytest tests/deployment/test_poller_rate_limit_recovery.py -v --tb=no

PASSED  TestRuntimeRateLimitHandling::test_poller_survives_429_and_recovers_on_reset
PASSED  TestRuntimeRateLimitHandling::test_poller_writes_paused_until_file_on_429
PASSED  TestRuntimeRateLimitHandling::test_poller_calls_release_on_429
PASSED  TestRuntimeRateLimitHandling::test_parse_reset_time_fallback_on_unparseable
PASSED  TestRuntimeRateLimitHandling::test_poller_resumes_after_paused_until_expires
PASSED  TestReleaseAndIdleOnEnvFailure::test_environmental_failure_calls_release_not_fail
PASSED  TestReleaseAndIdleOnEnvFailure::test_poller_never_calls_systemctl_disable
PASSED  TestReleaseAndIdleOnEnvFailure::test_poller_stays_alive_after_environmental_failure
PASSED  TestMorrisPollerDisabled::test_service_file_has_agent_role_env_var
PASSED  TestMorrisPollerDisabled::test_poller_reads_agent_role_from_env
PASSED  TestMorrisPollerDisabled::test_poller_sends_x_agent_role_header
PASSED  TestSessionCapRemoval::test_phase_runner_no_daily_session_cap
PASSED  TestSessionCapRemoval::test_phase_runner_no_daily_session_count
PASSED  TestSessionCapRemoval::test_phase_runner_runs_without_cap_check
PASSED  TestSessionCapRemoval::test_phase_runner_session_ceiling_reference_removed
PASSED  TestNoSystemctlDisable::test_no_systemctl_disable_in_dispatch_poller
PASSED  TestNoSystemctlDisable::test_no_sudo_systemctl_in_dispatch_poller
FAILED  TestSilent429Detection::test_fallback_heuristic_log_line_present
FAILED  TestSilent429Detection::test_fallback_checks_duration_threshold
PASSED  TestSilent429Detection::test_fallback_gates_on_nonzero_returncode
PASSED  TestSilent429Detection::test_fallback_sets_unknown_reset_time
FAILED  TestSilent429Detection::test_silent_429_returns_minus_429
PASSED  TestSilent429Detection::test_rc0_fast_exit_not_rate_limited
PASSED  TestSilent429Detection::test_slow_failure_not_rate_limited

3 failed, 24 passed
```

---

## Phase 8 Implementation Guidance

The fix is localized to `deployment/hermes/sdlc_phase_runner.py`, immediately after line 1019 (the existing `rate_limited = (...)` assignment):

```python
# STORY-625: Fallback heuristic for silent API-level 429s.
# When Anthropic rate-limits at HTTP transport, the CLI exits in <5s with
# rc=1 and minimal output — before it can render the "hit your limit" message.
if not rate_limited and duration < 10 and proc.returncode != 0:
    output_len = len(all_output.strip())
    has_error_markers = (
        "stop_sequence" in all_output
        or "error" in all_output.lower()[:200]
    )
    if output_len < 200 or has_error_markers:
        rate_limited = True
        reset_time = "unknown"  # triggers 1h conservative cap in _is_paused()
        print(
            f"[DISPATCH] SILENT RATE LIMIT detected — rc={proc.returncode}, "
            f"duration={duration}s, output_len={output_len}. "
            f"Writing pause flag with reset_time=unknown.",
            flush=True,
        )
```

After this block, the existing `if rate_limited:` handler (line 1029) writes the pause flag and returns `-429`.
