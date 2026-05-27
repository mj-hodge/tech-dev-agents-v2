# Test Design — STORY-803

## Fix the needs_info loop: override bypass + git-add bug + Phase-8-no-commits self-pause

> **Phase 7 deliverable.** All tests are in RED state. Phase 8 implements the production
> code; the test suite drives TDD.
>
> Scope: **medium**. Coverage target: 60%.
>
> Story path: 1 → 4 → 6 → **7** → 8 → Done

---

## 1. Summary

| # | File | Tests | State |
|---|------|-------|-------|
| T-1 | `tests/deployment/test_phase_runner_override_directive.py` (extended) | `TestStrengthenedPreamble` | 🔴 RED |
| T-2a | `tests/deployment/test_phase_runner_needs_info.py` (extended) | `TestPostNeedsInfoDirectiveFields` (2 tests) | 🔴 RED / 🟢 GREEN |
| T-2b | `tests/ops_console/test_dispatch_needs_info_directive_guard.py` (NEW) | `TestDirectiveGuardUnder60s` | 🔴 RED |
| T-2c | same file | `TestDirectiveGuardNotFiredWithoutDirective` | 🟢 GREEN (regression) |
| T-2d | same file | `TestDirectiveGuardAfter60s` | 🟢 GREEN (regression) |
| T-3 | `tests/deployment/test_phase_runner_override_directive.py` (extended) | `TestOverrideReappliesPerPhase` | 🟢 GREEN (regression) |
| T-4, T-5 | `tests/deployment/test_commit_and_push_question.py` (new content) | 4 tests across 3 classes | 🔴 RED |
| T-6a–c | `tests/deployment/test_phase_runner_phase8_silent_exit.py` (NEW) | 3 tests across 3 classes | 🔴 RED |
| T-7 | `tests/deployment/test_dispatch_poller_retry_classification.py` (extended) | `TestPhase8SilentExitNeverRetry` (2 tests) | 🔴 RED |
| T-8 | `tests/ops/test_grafana_dashboard_story_803.py` (NEW) | 4 tests | 🔴 RED |

**Total: 20 tests. 16 RED (to be made GREEN in Phase 8). 4 GREEN (regression tests, must stay GREEN).**

---

## 2. Test Map to Acceptance Criteria

| AC | Spec section | Test(s) | File |
|----|--------------|---------|------|
| AC-1 | §3.1.1 preamble rewrite | T-1 | `test_phase_runner_override_directive.py` |
| AC-2 | §3.1.2 60s guard (two layers) | T-2a, T-2b, T-2c, T-2d | `test_phase_runner_needs_info.py`, `test_dispatch_needs_info_directive_guard.py` |
| AC-3 | §3.1.3 per-phase re-injection (regression) | T-3 | `test_phase_runner_override_directive.py` |
| AC-4 | §3.2.1+3.2.2 `git add --` + helpers | T-4, T-5 | `test_commit_and_push_question.py` |
| AC-5 | §3.2.3 workdir tree dump on failure | T-5 | `test_commit_and_push_question.py` |
| AC-6 | §3.3 retry + fast-failure + failed transition | T-6a, T-6b, T-6c, T-7 | `test_phase_runner_phase8_silent_exit.py`, `test_dispatch_poller_retry_classification.py` |
| AC-7 | §7 6-story replay | manual — not unit-tested | `verification.md` (Phase 8 / post-deploy) |
| AC-8 | §3.4 Loki dashboard panel | T-8 | `tests/ops/test_grafana_dashboard_story_803.py` |

---

## 3. Test Group Details

### T-1 — Strengthened preamble (AC-1)

**File:** `tests/deployment/test_phase_runner_override_directive.py`
**Class:** `TestStrengthenedPreamble`
**State:** 🔴 RED

**What it verifies:** `_apply_override_directives` uses the hardened preamble from spec §3.1.1.
The current preamble starts with "CRITICAL OVERRIDE — READ AND FOLLOW BEFORE ANYTHING ELSE" and
does NOT explicitly negate seed-triggered pauses. After Phase 8, the preamble must open with
"STOP — READ THIS BEFORE THE SEED" and include the word "OVERRIDDEN" beside mention of
"staging access required" (the most common bypass trigger).

**Arrange:** temp workdir with `features/story-803-test/DIRECTIVE.md`
**Act:** call `_apply_override_directives(workdir, folder, prompt, story_id, phase_num)`
**Assert:**
- `"STOP — READ THIS BEFORE THE SEED"` in result
- `"OVERRIDDEN"` in result
- `"staging access required"` in result (case-insensitive)

**RED reason:** current preamble string does not match. Phase 8 replaces it.

---

### T-2a — `_post_needs_info` sends directive fields (AC-2 client layer)

**File:** `tests/deployment/test_phase_runner_needs_info.py`
**Class:** `TestPostNeedsInfoDirectiveFields` (2 tests)
**State:** F-01 🔴 RED, F-02 🟢 GREEN

`test_post_needs_info_sends_directive_present_and_phase_started_at` (**RED**):
- Calls `_post_needs_info(... directive_present=True, phase_started_at=1746100000.0)`
- Asserts body includes `directive_present` and `phase_started_at` fields
- RED reason: current signature has no such params → TypeError

`test_post_needs_info_backward_compatible_without_new_kwargs` (**GREEN**):
- Calls without new kwargs
- Asserts still makes exactly 1 HTTP request
- Regression test — must stay GREEN after Phase 8 adds optional params with defaults

---

### T-2b–d — Server-side directive guard (AC-2 server layer)

**File:** `tests/ops_console/test_dispatch_needs_info_directive_guard.py` (NEW)
**State:** T-2b 🔴 RED, T-2c 🟢 GREEN, T-2d 🟢 GREEN

**T-2b** `test_needs_info_endpoint_rejects_under_60s_with_directive` (RED):
- Arrange: dispatch row with `claimed_at = now() - 5s`; POST body `directive_present=True`
- Act: POST `/api/dispatch/needs-info/STORY-TEST-803`
- Assert: `resp.status_code == 409`, body contains `"directive_bypass_attempted"`,
  `db_svc.needs_info` NOT called (state unchanged)
- RED reason: guard doesn't exist in `needs_info_story()` → returns 200

**T-2c** `test_needs_info_endpoint_allows_under_60s_without_directive` (GREEN):
- Same timing but `directive_present=False` → 200, `db_svc.needs_info` called
- Guard is conditional on `directive_present`; preserves pre-803 behavior

**T-2d** `test_needs_info_endpoint_allows_after_60s_with_directive` (GREEN):
- `claimed_at = now() - 90s`, `directive_present=True` → 200 (past threshold)
- Guard does not fire after 60s — agent has done real work

---

### T-3 — Per-phase override re-injection (AC-3 regression)

**File:** `tests/deployment/test_phase_runner_override_directive.py`
**Class:** `TestOverrideReappliesPerPhase`
**State:** 🟢 GREEN (already satisfied — `_apply_override_directives` is inside per-phase loop)

Drives 3 different phase prompts through `_apply_override_directives` with a directive file present.
Asserts: each result contains the directive body; `override_directive_applied` event fires 3×;
each result contains its unique phase-prompt sentinel.

---

### T-4 — `git add --` for dashed folder paths (AC-4)

**File:** `tests/deployment/test_commit_and_push_question.py`
**Class:** `TestCommitAndPushQuestionDashedPath`
**State:** 🔴 RED

**What it verifies:** `_commit_and_push_question` uses `["git", "-C", workdir, "add", "--", path]`
(literal-path form) instead of `["git", "-C", workdir, "add", path]` (no `--` separator).

**Arrange:** temp git repo with `features/story-644-bsr-competitor-category-monitor/QUESTION.md`
**Act:** call `_commit_and_push_question(workdir, branch, question_path, story_id)` with all
  `subprocess.run` calls captured
**Assert:** at least one `git add` call captured; `"--"` in the call; `"--"` immediately
  precedes `question_path`

**RED reason:** current code uses `["git", "-C", workdir, "add", question_path]` — no `"--"`.

---

### T-5 — Workdir subtree dump on commit failure (AC-4, AC-5)

**File:** `tests/deployment/test_commit_and_push_question.py`
**Classes:** `TestCommitAndPushQuestionSubtreeDump`, `TestFeaturesSubtreeSnapshotHelper`
**State:** 🔴 RED (3 tests)

`test_commit_and_push_question_failure_dumps_subtree` (**RED**):
- Arrange: story folder with `seed.md`, `feature-spec.md`, `some-other-file.txt` but NOT `QUESTION.md`
- Act: call `_commit_and_push_question` with `_emit_event` captured
- Assert: result is `False`; a `question_commit_failed(stage="precheck")` event was emitted;
  event payload contains `"feature_subtree"` key with a non-empty list including `"seed.md"`
- RED reason: current precheck event does not include `feature_subtree`

`test_features_subtree_snapshot_helper_exists` (**RED**):
- Asserts `_features_subtree_snapshot(workdir, story_folder)` exists and returns a list
  containing `"seed.md"` and `"feature-spec.md"`
- RED reason: helper not yet added

`test_derived_story_folder_helper_exists` (**RED**):
- Asserts `_derived_story_folder(question_path)` exists and correctly parses the story
  folder from `"features/<folder>/QUESTION.md"` paths (including dashed names and edge cases)
- RED reason: helper not yet added

---

### T-6a — Phase 8 zero-commits retry with RETRY_NUDGE (AC-6)

**File:** `tests/deployment/test_phase_runner_phase8_silent_exit.py`
**Class:** `TestPhase8ZeroCommitsRetry`
**State:** 🔴 RED

**Arrange:** minimal workdir; `_run_phase_sdk` mock returns `(0, "Phase completed.")`; `subprocess.run`
  mocked to return `0` for `rev-list` count; `time.time` mocked to make elapsed = 120s (>= 90s)
**Act:** call `run_sdlc_phases(story_id="STORY-803", scope="small", ...)`
**Assert:** `_run_phase_sdk` called ≥ 2× for Phase 8; second call prompt contains "RETRY" (case-insensitive)
**RED reason:** current code writes synthetic QUESTION.md then needs_info — only one SDK call.

---

### T-6b — Phase 8 fast-failure (< 90s elapsed) skips retry (AC-6 R-A-6)

**File:** `tests/deployment/test_phase_runner_phase8_silent_exit.py`
**Class:** `TestPhase8ZeroCommitsFastFailure`
**State:** 🔴 RED

**Arrange:** minimal workdir; mock SDK returns `(0, "...")` instantly (elapsed ≈ 0s < 90s);
  `rev-list` returns `"0\n"`
**Act:** `run_sdlc_phases`
**Assert:**
1. Phase 8 `_run_phase_sdk` called exactly once (no retry)
2. Return reason contains `"phase8_silent_exit"`
3. No `QUESTION.md` written

**RED reason:** current code writes QUESTION.md → needs_info; no fast-failure logic.

---

### T-6c — Both Phase 8 attempts at 0 commits → FAILED, no QUESTION.md (AC-6)

**File:** `tests/deployment/test_phase_runner_phase8_silent_exit.py`
**Class:** `TestPhase8RetryZeroCommitsFailed`
**State:** 🔴 RED

**Arrange:** mock time to make elapsed = 120s (retry fires); both SDK calls return `(0, ...)`; `rev-list` always `"0\n"`; `_post_needs_info` captured
**Act:** `run_sdlc_phases`
**Assert:**
1. Reason contains `"phase8_silent_exit"`
2. No `QUESTION.md` written
3. `_post_needs_info` NOT called for Phase 8 (story goes to FAILED, not needs_info)

**RED reason:** current code returns `"needs_info"` with synthetic QUESTION.md.

---

### T-7 — `phase8_silent_exit` in `NEVER_RETRY_CLASSES` (AC-6)

**File:** `tests/deployment/test_dispatch_poller_retry_classification.py`
**Class:** `TestPhase8SilentExitNeverRetry` (2 tests)
**State:** 🔴 RED

`test_dispatch_poller_phase8_silent_exit_in_never_retry_classes` (**RED**):
1. `_classify_failure("phase_8_failed: phase8_silent_exit")` returns a class in `NEVER_RETRY_CLASSES`
2. `_report_fail(..., error_message="phase_8_failed: phase8_silent_exit")` issues NO retry POST
3. `/api/dispatch/fail/STORY-803` IS still called (story transitions to failed)

`test_phase8_silent_exit_pattern_is_in_pattern_map` (**RED**):
- `_classify_failure("phase8_silent_exit")` returns non-`"unknown"` class in `NEVER_RETRY_CLASSES`

**RED reason:** `_NEVER_RETRY_PATTERN_MAP` has no `"phase8_silent_exit"` key.

---

### T-8 — Grafana dashboard panel (AC-8)

**File:** `tests/ops/test_grafana_dashboard_story_803.py` (NEW)
**Class:** `TestGrafanaDashboardStory803Panel` (4 tests)
**State:** 🔴 RED

Tests parse `deploy/grafana-agent-dashboard.json` and assert:

1. `test_grafana_dashboard_has_story_803_panel`: panel titled `"needs_info loop guards (24h)"` exists with ≥ 3 targets
2. `test_grafana_dashboard_story_803_panel_has_directive_bypass_query`: a target expr contains `"directive_bypass_attempted"`
3. `test_grafana_dashboard_story_803_panel_has_question_commit_query`: a target expr contains `"question_commit_failed"`
4. `test_grafana_dashboard_story_803_panel_has_phase8_no_commits_query`: a target expr contains `"phase8_no_commits"`

**RED reason:** panel not yet in dashboard JSON. Phase 8 adds the JSON block.

---

## 4. RED State Confirmation

```
$ pytest tests/deployment/test_phase_runner_override_directive.py::TestStrengthenedPreamble \
         tests/deployment/test_phase_runner_needs_info.py::TestPostNeedsInfoDirectiveFields \
         tests/deployment/test_commit_and_push_question.py \
         tests/deployment/test_phase_runner_phase8_silent_exit.py \
         tests/deployment/test_dispatch_poller_retry_classification.py::TestPhase8SilentExitNeverRetry \
         tests/ops_console/test_dispatch_needs_info_directive_guard.py::TestDirectiveGuardUnder60s \
         tests/ops/test_grafana_dashboard_story_803.py \
         -v --tb=no -q

20 tests, 16 FAIL, 4 PASS

# FAIL (16) — to be made GREEN in Phase 8:
T-1   test_apply_override_directives_strengthened_preamble
T-2a  test_post_needs_info_sends_directive_present_and_phase_started_at
T-2b  test_needs_info_endpoint_rejects_under_60s_with_directive
T-4   test_commit_and_push_question_uses_double_dash_in_git_add
T-5   test_commit_and_push_question_failure_dumps_subtree
T-5   test_features_subtree_snapshot_helper_exists
T-5   test_derived_story_folder_helper_exists
T-6a  test_phase8_zero_commits_retries_with_enhanced_prompt
T-6b  test_phase8_zero_commits_fast_failure_skips_retry
T-6c  test_phase8_retry_zero_commits_transitions_to_failed
T-7   test_dispatch_poller_phase8_silent_exit_in_never_retry_classes
T-7   test_phase8_silent_exit_pattern_is_in_pattern_map
T-8   test_grafana_dashboard_has_story_803_panel (×4)

# PASS (4) — regression tests, must remain GREEN:
T-3   test_phase_runner_reapplies_override_per_phase
T-2a  test_post_needs_info_backward_compatible_without_new_kwargs
T-2c  test_needs_info_endpoint_allows_under_60s_without_directive
T-2d  test_needs_info_endpoint_allows_after_60s_with_directive
```

---

## 5. Failure Mode Verification

All 16 failing tests fail for the **right reason** (production code is absent/wrong), not for import errors or test bugs:

| Test | Actual failure reason |
|------|-----------------------|
| T-1 | `AssertionError: preamble must open with 'STOP — READ THIS BEFORE THE SEED'` |
| T-2a | `AssertionError: JSON body missing 'directive_present' field` |
| T-2b | `AssertionError: Expected 409, got 200` |
| T-4 | `AssertionError: 'git add' call must contain '--' separator` |
| T-5 subtree | `AssertionError: question_commit_failed event missing 'feature_subtree'` |
| T-5 helpers | `pytest.fail: _features_subtree_snapshot not found` |
| T-6a | `AssertionError: _run_phase_sdk called 1× for Phase 8, expected ≥2` |
| T-6b | `AssertionError: return reason does not contain 'phase8_silent_exit'` |
| T-6c | `AssertionError: QUESTION.md must not be written` |
| T-7 | `AssertionError: 'phase8_silent_exit' not in NEVER_RETRY_CLASSES` |
| T-8 | `AssertionError: No panel titled 'needs_info loop guards (24h)' found` |

---

## 6. Defensive Gate Coverage

| Gate | Covered by |
|------|-----------|
| Gate 1 (Null/None boundary) | `test_post_needs_info_backward_compatible_without_new_kwargs` (None defaults) |
| Gate 13 (Failure taxonomy) | T-7: retryable vs never-retry; T-6b/c: fast-fail vs retry |
| Gate 10 (Error observability) | T-5: `_emit_event` called with stage + reason + feature_subtree |
| Gate 9 (Failure recovery) | T-6c: retry exhausted → FAILED (not silently dropped or needs_info) |

---

## 7. Coverage Targets

| Component | Tests | Target |
|-----------|-------|--------|
| `sdlc_phase_runner._apply_override_directives` | T-1, T-3 | preamble text + per-phase event |
| `sdlc_phase_runner._post_needs_info` | T-2a (2 tests) | new params + backward compat |
| `sdlc_phase_runner._commit_and_push_question` | T-4, T-5 (3 tests) | git add `--` + subtree dump |
| `sdlc_phase_runner` Phase 8 zero-commit block | T-6a, T-6b, T-6c | retry, fast-fail, fail-clean |
| `dispatch_poller.NEVER_RETRY_CLASSES` | T-7 (2 tests) | phase8_silent_exit classification |
| `ops_console.routes.dispatch.needs_info_story` | T-2b, T-2c, T-2d | 409 guard + 200 pass-through |
| `deploy/grafana-agent-dashboard.json` | T-8 (4 tests) | panel + 3 LogQL queries present |

Overall target for medium scope: **60%** of changed lines.

---

## 8. Build Order for Phase 8 (from feature-spec.md §4)

Phase 8 implements in this order (each commit makes specific tests GREEN):

1. Tests RED (this deliverable)
2. `_has_directive_file` + `_features_subtree_snapshot` + `_derived_story_folder` helpers → T-5 helpers GREEN
3. `_apply_override_directives` preamble rewrite → T-1 GREEN
4. `_commit_and_push_question` `git add --` + dump fields → T-4, T-5 subtree GREEN
5. `_post_needs_info` body fields + signature → T-2a GREEN
6. Phase-runner phase-loop directive-bypass pre-check + 409 handling (client layer)
7. Server-side guard in `dispatch.py` `needs_info_story` route → T-2b GREEN
8. Phase-8 zero-commits retry + fast-failure + transition to failed → T-6a, T-6b, T-6c GREEN
9. `NEVER_RETRY_CLASSES` adds `phase8_silent_exit` → T-7 GREEN
10. Grafana dashboard JSON panel → T-8 GREEN

---

## 9. Files Modified / Created

**New test files:**
- `tests/ops_console/test_dispatch_needs_info_directive_guard.py` — T-2b, T-2c, T-2d
- `tests/deployment/test_phase_runner_phase8_silent_exit.py` — T-6a, T-6b, T-6c
- `tests/ops/test_grafana_dashboard_story_803.py` — T-8

**Extended test files:**
- `tests/deployment/test_phase_runner_override_directive.py` — T-1 (class C), T-3 (class D)
- `tests/deployment/test_phase_runner_needs_info.py` — T-2a (class F)
- `tests/deployment/test_commit_and_push_question.py` — T-4, T-5 (all classes)
- `tests/deployment/test_dispatch_poller_retry_classification.py` — T-7 (class F)

**Production files to be changed in Phase 8:**
- `deployment/hermes/sdlc_phase_runner.py` — preamble, helpers, git add, body fields, Phase 8 zero-commit block
- `deployment/hermes/dispatch_poller.py` — `NEVER_RETRY_CLASSES`, `_NEVER_RETRY_PATTERN_MAP`
- `tech_dev_agents/ops_console/routes/dispatch.py` — `needs_info_story` directive guard
- `deploy/grafana-agent-dashboard.json` — panel JSON

---

## 10. Follow-ups (out of scope)

- Per-VM heuristic on directive file freshness (mtime check vs seed mtime)
- Operator UI for `phase8_silent_exit` triage button
- Removing the synthetic-QUESTION.md path on Phase 6 deliverable miss (needs 30-day Loki soak first)
