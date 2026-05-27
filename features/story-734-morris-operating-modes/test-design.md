# Test Design — STORY-734: Morris Operating Modes (Quota-Aware + Overnight Scheduling)

**Story:** STORY-734
**Phase:** 7 (Test Design)
**Date:** 2026-04-27
**Author:** Derrick (tech-agent-derrick@gorillacommerce.co)

---

## Test File

`tests/deployment/test_morris_operating_modes_734.py`

All tests are pure-Python unit tests using `pytest` and `unittest.mock`. No external services required.

---

## Test Criteria Mapping

### TC-1 — Auto-transition to light

**Test:** `test_auto_transition_to_light_at_73_pct`

Given `get_quota_pct()` returns 0.73, current mode is `full`, verify `check_and_auto_transition()` calls `set_mode("light", "quota_approaching", "auto")` exactly once and posts a Teams DM.

**Setup:** Mock `get_quota_pct` → 0.73, mock mode state file with `{"mode": "full", ...}`, mock `set_mode`, mock Teams `post_dm`.

**Assert:** `set_mode` called once with `("light", "quota_approaching", "auto")`. Teams DM contains `"[MODE] Morris → light"` and `"73%"`.

---

### TC-2 — Auto-transition to minimal

**Test:** `test_auto_transition_to_minimal_at_92_pct`

Given `get_quota_pct()` returns 0.92, verify transition from any mode to `minimal`.

**Setup:** Mock quota → 0.92, test from `full` and from `light`.

**Assert:** `set_mode("minimal", "quota_critical", "auto")` called.

---

### TC-3 — Hysteresis (no premature recovery)

**Test:** `test_hysteresis_minimal_stays_at_88_pct`

Given 0.88 (below 0.90), current mode `minimal`, verify mode stays `minimal` — does NOT recover prematurely.

**Setup:** Mock quota → 0.88, mode state `{"mode": "minimal", ...}`.

**Assert:** `set_mode` is NOT called. Return value is `"minimal"`.

---

### TC-4 — Recovery to light

**Test:** `test_recovery_minimal_to_light_at_55_pct`

Given 0.55 with current mode `minimal`, verify transition to `light` (not `full`).

**Setup:** Mock quota → 0.55, mode state `{"mode": "minimal", ...}`.

**Assert:** `set_mode("light", "quota_recovering", "auto")` called. NOT `"full"`.

---

### TC-5 — Recovery to full

**Test:** `test_recovery_light_to_full_at_45_pct`

Given 0.45 and not overnight, verify transition from `light` to `full`.

**Setup:** Mock quota → 0.45, mode `light`, mock `overnight_window()` → False.

**Assert:** `set_mode("full", "quota_healthy", "auto")` called.

---

### TC-6 — Overnight gate

**Test:** `test_overnight_window_blocks_briefing`

Given 03:00 ET (08:00 UTC), `overnight_window()` returns True and orchestrator loop's briefing call is suppressed even if mode is `full`.

**Additional test:** `test_overnight_window_returns_true_during_overnight`

Mock datetime to 06:00 UTC (01:00 ET). Verify `overnight_window()` returns True.

**Additional test:** `test_overnight_window_returns_false_outside`

Mock datetime to 14:00 UTC (09:00 ET). Verify `overnight_window()` returns False.

**Note:** The overnight window check (05:00–12:00 UTC) is a simple time range check. The briefing suppression in overnight mode happens because the cron fires `set_mode.py light` at 05:00 UTC, and briefing doesn't run in light mode. TC-6 tests that `overnight_window()` returns correct values AND that the light-to-full recovery at 0.45 quota is blocked during overnight.

---

### TC-7 — Quota unavailable → no transition

**Test:** `test_quota_unavailable_skips_transition`

Given `get_quota_pct()` returns None, verify `check_and_auto_transition()` exits without changing mode.

**Setup:** Mock `get_quota_pct` → None, current mode `full`.

**Assert:** `set_mode` is NOT called. Return value is current mode unchanged.

---

### TC-8 — Mark cooldown

**Test:** `test_mark_cooldown_prevents_auto_override`

Given Mark manually set mode to `minimal` 30 minutes ago, verify auto-transition does NOT override it (even with quota at 0.40).

**Setup:** Mode state: `{"mode": "minimal", "actor": "mark", "set_at": "<30 min ago>"}`. Mock quota → 0.40.

**Assert:** `set_mode` is NOT called. Mode stays `minimal`.

**Additional test:** `test_mark_cooldown_expires_after_120_min`

Same setup but `set_at` is 121 minutes ago. Auto-transition SHOULD fire.

---

### TC-9 — Light mode suppresses subagents

**Test:** `test_light_mode_suppresses_rebase_subagent`

Given mode is `light`, verify that `invoke_rebase_subagent` is not called during `execute_interventions()` and a log message `[MODE light] subagent suppressed` is emitted.

**Setup:** Mock mode → `light`, create PR conflict finding, mock `invoke_rebase_subagent`.

**Assert:** `invoke_rebase_subagent` NOT called. Log contains `"[MODE light] subagent suppressed"`.

---

### TC-10 — Shell jobs unaffected

**Test:** `test_shell_infra_allowed_in_all_modes`

Verify `mode_allows(mode, "shell_infra")` returns True for all three modes.

**Assert:** `mode_allows("full", "shell_infra")` → True, `mode_allows("light", "shell_infra")` → True, `mode_allows("minimal", "shell_infra")` → True.

---

### TC-11 — Teams DM on every transition

**Test:** `test_teams_dm_posted_on_set_mode`

Mock Teams client/`post_dm`. Call `set_mode()` with various modes and reasons. Assert a DM is posted for each call.

**Setup:** Mock `interventions.post_dm`.

**Assert:** For each `set_mode()` call, `post_dm` is called exactly once with `[MODE]` prefix in the message.

---

### TC-12 — Overnight crons idempotent

**Test:** `test_install_cron_idempotent`

Running `install-orchestrator-cron.sh` twice does not duplicate the overnight entries.

**Approach:** This is a shell test. Parse the script for the `install_cron_block` call with marker `"# morris-operating-modes-734"`. The existing `install_cron_block()` function uses `grep -q` to check for the marker — verify the marker is present in the new block.

**Alternative (unit-level):** Verify that the cron block string contains the marker `# morris-operating-modes-734` and that `install_cron_block` is called with this marker.

---

## Additional Unit Tests

### `test_get_mode_returns_full_on_missing_file`
When mode state file doesn't exist, `get_mode()` returns `"full"`.

### `test_get_mode_returns_full_on_corrupt_json`
When mode state file contains invalid JSON, `get_mode()` returns `"full"` and logs a warning.

### `test_set_mode_creates_file_atomically`
Verify `set_mode()` writes via temp file + rename (atomic write pattern).

### `test_mode_allows_job_class_matrix`
Exhaustive test of all mode × job_class combinations against the spec table.

### `test_set_mode_cli_validates_mode`
`set_mode.py` with invalid mode exits non-zero.

### `test_set_mode_cli_sets_correct_mode`
`set_mode.py light --reason test` writes correct state.

### `test_daily_contact_summary_exits_in_light_mode`
Mock `get_mode()` → `"light"`, verify `daily_contact_summary.main()` calls `sys.exit(0)`.

### `test_orchestrator_minimal_mode_skips_poll`
Mock mode → `"minimal"`, verify `main()` returns early without fetching queue.

### `test_get_quota_pct_returns_none_on_error`
Mock HTTP error from quota endpoint, verify returns None.

### `test_get_quota_pct_returns_correct_value`
Mock successful response with `{"percent_used": 0.73}`, verify returns 0.73.

---

**End of Test Design.** Ready for Phase 8 (Implementation).
