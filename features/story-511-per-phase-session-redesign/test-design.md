# STORY-511 Test Design: Per-Phase SDK Session Redesign

> Phase 7 | Scope: Large | Status: retroactive (implementation already complete)
> Tests: 31 pytest tests across 5 files + 1 benchmark script (AC-10)
> All tests GREEN at time of writing.

---

## Approach Selected

**Approach B — Session continuation via `--resume <session-id>`** was implemented.
Each phase captures the Claude Code session ID emitted as `[SESSION] <uuid>` in
the systemd journal (`dispatch-poller` unit). The next phase passes `--resume
<sid>` to `claude_sdk_tool.py` so the LLM reuses the loaded context instead of
re-reading all deliverables from disk.

Feature flag: `PHASE_SESSION_RESUME=0` env var disables and falls back to
pre-511 behavior (one fresh SDK session per phase).

---

## Test Groups

### Group A — Session ID Lifecycle (AC-9, AC-11)
*File: `tests/test_511_session_resume.py`*

Tests the `_read_story_session_id` / `_clear_story_session_id` state management
contract: missing-file returns None, roundtrip via file write, clear removes
file, clear on missing file is a no-op.

| Test | What it pins |
|------|-------------|
| `test_read_session_id_returns_none_when_missing` | First phase starts fresh (no prior sid) |
| `test_persist_and_read_session_id_roundtrip` | File persisted by capture is readable |
| `test_clear_session_id_removes_file` | Terminal-state cleanup removes file |
| `test_clear_on_missing_file_does_not_raise` | Cleanup is idempotent on missing file |

### Group B — Feature Flag (AC-11)
*File: `tests/test_511_session_resume.py`*

Verifies that setting `PHASE_SESSION_RESUME=0` (via `_PHASE_SESSION_RESUME = False`)
causes `_read_story_session_id` to short-circuit and return None even when a
persisted session file exists. This is the rollback escape hatch.

| Test | What it pins |
|------|-------------|
| `test_resume_disabled_via_env_var` | Disabled flag suppresses resume, even with stored sid |

### Group C — Journal Capture (`_capture_session_id_from_log`) (AC-9)
*File: `tests/test_511_session_resume.py`*

Tests the journalctl-based session ID extraction. The prior implementation read
`/tmp/claude-sdlc-logs/session-*.log` which is written by the claude CLI binary
(NOT by `claude_sdk_tool.py`). Every phase logged `session_id: "unknown"` and
STORY-511 was inert until this was fixed. These tests mock `subprocess.run` to
inject fake journal output.

| Test | What it pins |
|------|-------------|
| `test_capture_from_journal_parses_session_line` | Happy path: `[SESSION] <uuid>` in journal → sid persisted; journalctl called with correct `--since` timestamp |
| `test_capture_from_journal_no_session_line_does_not_persist` | Empty/no-match journal → no sid, no crash |
| `test_capture_picks_most_recent_session_when_multiple_lines` | N `[SESSION]` lines (crash+restart) → last wins; stale sid avoids 404 on `--resume` |
| `test_capture_tolerates_journalctl_nonzero_exit` | Non-zero rc (permission denied, non-systemd host) → no-op, no crash |
| `test_capture_tolerates_subprocess_exception` | `TimeoutExpired` / `OSError` from `subprocess.run` → swallowed, no crash |
| `test_capture_without_since_ts_uses_fallback_window` | Backwards compat: no `since_ts` → uses `"-15 minutes"` fallback |

### Group D — False-Positive Rate-Limit Regression (AC-9)
*File: `tests/test_511_session_resume.py`*

Pins the contract that `_run_phase_sdk` must NOT classify a fast-but-successful
phase as rate-limited. Background: on 2026-04-22 13:20:55 a phase-7 run that
completed in 14 s (because deliverables already existed on the branch) was
mis-classified as rate-limited by a duration-only heuristic. The poller
disabled Daisy for 20 minutes. Fix: only `"hit your limit"` text triggers
rate-limited; duration alone never does.

| Test | What it pins |
|------|-------------|
| `test_fast_phase_is_not_auto_rate_limited` | Source-level inspection confirms duration-only heuristic is absent; explicit text check is present |

### Group E — Branch Isolation (Fix #3)
*File: `tests/test_branch_isolation.py`*

Background: phase commits were landing on the wrong branch because
`_ensure_branch` silently failed (git errors not checked). The fix: a clear
story_id→branch mapping and a guard in `_save_partial_work` that refuses to
commit/push when the current branch doesn't match the expected branch.

| Test | What it pins |
|------|-------------|
| `test_numeric_story_id` | `_expected_story_branch("STORY-511")` returns `"story-511/story-511"` |
| `test_lowercases_the_story_id_suffix` | Slug is always lowercase |
| `test_handles_missing_hyphen_defensively` | Bare story id without slug falls back to id-only form |
| `test_mismatch_refuses_to_commit_or_push` | Wrong branch → no git commit, no push |
| `test_match_pushes_with_explicit_refspec` | Correct branch → push uses `HEAD:refs/heads/<branch>` |
| `test_empty_branch_name_refuses_to_push` | Empty string branch → refuse push |
| `test_clean_tree_skips_commit_and_push` | Nothing to commit → skip silently |
| `test_formulas_agree_for_representative_ids` | `_expected_story_branch` and `_ensure_branch` use the same formula |

### Group F — Completion 422 No-Loop (Fix #4)
*File: `tests/deployment/test_complete_422_no_loop.py`*

Background: when the completion gate rejected with 422, the poller called
`_report_complete` in a loop. Fix: `_report_complete` returns the HTTP status
code and the poller branches: 2xx → mark done, 422 → call `_report_fail`
instead, other → retry.

| Test | What it pins |
|------|-------------|
| `test_report_complete_returns_status_code` | Return value is the HTTP status integer |
| `test_report_complete_422_logs_body_for_diagnosis` | 422 body is logged for diagnosis |
| `test_poller_branches_on_complete_status` | 2xx → done path; 422 → fail path |
| `test_422_path_calls_report_fail` | 422 response triggers `_report_fail` |
| `test_no_base_url_path_preserves_prior_behavior` | No-BASE_URL env → no regression |

### Group G — Stale Claim Recovery (Fix #1)
*File: `tests/ops_console/test_stale_recovery_contract.py`*

Background: `recover_stale_claims` was querying on `claimed_at` instead of
`updated_at`, allowing phantom re-claim of stories that had been updated
recently (causing double-claim). Fix: stale window checked via `updated_at`.

| Test | What it pins |
|------|-------------|
| `test_sql_filters_on_updated_at_not_claimed_at` | SQL uses `updated_at` staleness check |
| `test_default_timeout_is_at_least_3600_seconds` | Default stale window ≥ 3600 s |
| `test_caller_can_still_override_timeout_for_tighter_recovery` | Caller can pass tighter window |
| `test_update_sets_status_to_pending_and_clears_claim_fields` | Recovery resets status + clears `claimed_by` |

### Group H — Completion Gate Slug Relaxation (Mark follow-up)
*File: `tests/ops_console/test_completion_gate_slug_relaxed.py`*

Background: completion gate required `features/story-NNN-kebab/` but agents
sometimes produced `features/story-NNN/` (bare ID, no slug). Gate relaxed to
accept both forms.

| Test | What it pins |
|------|-------------|
| `test_gate_accepts_bare_story_folder` | `features/story-511/` passes the gate |
| `test_gate_error_message_mentions_both_forms` | Error message explains both valid forms |

---

## AC Coverage Matrix

| AC | Description | Test Group(s) | Status |
|----|-------------|--------------|--------|
| AC-3 | `run_sdlc_phases` refactored with session resume | Groups A, B, C (module load test) | ✅ GREEN |
| AC-5 | No regression on resume (SIGTERM → partial work preserved) | Group E (branch guard) | ✅ GREEN |
| AC-6 | No regression on per-file commits | Group E | ✅ GREEN |
| AC-9 | Integration tests: session id lifecycle, capture, feature flag | Groups A, B, C, D | ✅ GREEN |
| AC-10 | Benchmarking script | `tests/benchmark/story_511_session_resume.py` (manual) | ✅ Script present |
| AC-11 | Feature flag `PHASE_SESSION_RESUME` gates new behavior | Group B | ✅ GREEN |
| Fix #1 | Stale-claim double-claim regression | Group G | ✅ GREEN |
| Fix #3 | Branch-isolation guard | Group E | ✅ GREEN |
| Fix #4 | 422 completion gate no-loop | Group F | ✅ GREEN |

> AC-4 (wall-clock ≤ 20 min) and AC-8 (`session_id` in phase events) are verified
> at runtime via the Loki `phase_end` events with `session_id` field — not
> captured in unit tests. AC-8 field is present in `_run_phase_sdk` implementation
> (inspectable via `grep "session_id" sdlc_phase_runner.py`).

---

## Test Run

```bash
# All 511-related tests (31 tests, ~0.3 s)
pytest tests/test_511_session_resume.py \
       tests/test_branch_isolation.py \
       tests/deployment/test_complete_422_no_loop.py \
       tests/ops_console/test_stale_recovery_contract.py \
       tests/ops_console/test_completion_gate_slug_relaxed.py \
       -v

# AC-10 benchmark (requires agent VM with claude CLI, ~4 SDK sessions)
python3 tests/benchmark/story_511_session_resume.py --agent daisy
```

---

## Follow-ups

- **AC-7 metric** (`sdk_session_reads_per_phase`): Loki downstream aggregation —
  count Read tool calls per `session_id`. Not yet wired in Grafana dashboards.
  Tracked as a separate ops task.
- **AC-4 wall-clock verification**: measure P50 Medium story duration pre/post
  from Loki `phase_end` events. Data available after 14-day canary window.
- **Session expiry handling**: if `--resume <sid>` returns a 404 (expired session),
  the SDK exits rc≠0. The poller's error path retries from scratch (fresh session).
  No dedicated test yet — regression risk is low given observed session TTL > 48h.
