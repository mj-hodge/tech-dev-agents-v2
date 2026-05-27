# Test Design — STORY-537: Git Fail-Fast in Phase Runner

## Overview

| Field | Value |
|-------|-------|
| Scope | small |
| Coverage target | 50% (critical paths) |
| Test file | `tests/deployment/test_phase_runner_git_fail_fast.py` |
| Functions under test | `_ensure_branch`, `_save_partial_work`, `run_sdlc_phases` |
| Test framework | pytest with unittest.mock |

## Test Strategy

All tests mock `subprocess.run` to simulate git command success/failure. The tests
verify that the **hardened** versions of `_ensure_branch` and `_save_partial_work`:
1. Check return codes on every git subprocess call
2. Emit structured `git_command_failed` events via `_emit_event`
3. Raise `RuntimeError` (or return early) on non-recoverable failures
4. Preserve the existing branch-exists fallback as a recoverable flow

## Test Groups

### Group A — `_ensure_branch` return-code checks (AC-1, AC-6, AC-8)

Tests that critical git commands in `_ensure_branch` emit events and raise on failure.

| Test | What It Verifies | AC |
|------|------------------|----|
| `test_ensure_branch_fetch_failure_raises` | `git fetch origin <branch>` rc!=0 emits `git_command_failed` and raises `RuntimeError` | AC-1, AC-6 |
| `test_ensure_branch_checkout_main_failure_raises` | `git checkout main` rc!=0 in greenfield path emits event and raises | AC-1, AC-8 |
| `test_ensure_branch_pull_ff_only_failure_raises` | `git pull --ff-only origin main` rc!=0 emits event and raises | AC-1, AC-6 |
| `test_ensure_branch_ls_remote_failure_raises` | `git ls-remote` rc!=0 emits event and raises (can't determine if branch exists) | AC-1, AC-8 |
| `test_ensure_branch_event_includes_command_context` | Emitted event has `command`, `returncode`, `stderr`, `story_id` fields | AC-6 |

### Group B — `_ensure_branch` branch-exists fallback preserved (AC-2)

Tests that the recoverable "branch already exists" flow still works.

| Test | What It Verifies | AC |
|------|------------------|----|
| `test_ensure_branch_checkout_b_fails_fallback_succeeds` | `checkout -b` rc!=0 falls back to `checkout` (no raise) on resume path | AC-2 |
| `test_ensure_branch_checkout_b_fails_fallback_succeeds_greenfield` | `checkout -b` rc!=0 falls back to `checkout` (no raise) on greenfield path | AC-2 |
| `test_ensure_branch_both_checkout_fail_raises` | `checkout -b` AND fallback `checkout` both fail — emits event and raises | AC-2, AC-1 |

### Group C — `_ensure_branch` happy path (regression guard)

| Test | What It Verifies | AC |
|------|------------------|----|
| `test_ensure_branch_happy_path_no_raise` | All git commands succeed — no exceptions, no events emitted | all |
| `test_ensure_branch_already_on_correct_branch` | Already on story branch — returns immediately, no git mutations | all |

### Group D — `_save_partial_work` return-code checks (AC-3, AC-4, AC-6)

| Test | What It Verifies | AC |
|------|------------------|----|
| `test_save_partial_work_add_failure_aborts_push` | `git add -A` rc!=0 emits event, does NOT call commit or push | AC-3 |
| `test_save_partial_work_commit_failure_aborts_push` | `git commit` rc!=0 emits event, does NOT call push | AC-3 |
| `test_save_partial_work_push_failure_emits_event_no_raise` | `git push` rc!=0 emits event with stderr but does NOT raise | AC-4 |
| `test_save_partial_work_push_stderr_in_event` | Push failure event includes truncated stderr content | AC-4, AC-6 |

### Group E — `_save_partial_work` happy path (regression guard)

| Test | What It Verifies | AC |
|------|------------------|----|
| `test_save_partial_work_happy_path_commits_and_pushes` | All commands succeed — add, commit, push all called in order | all |
| `test_save_partial_work_no_dirty_files_skips` | No dirty files — no add/commit/push (existing behavior preserved) | all |

### Group F — `run_sdlc_phases` handles `_ensure_branch` failure (AC-5)

| Test | What It Verifies | AC |
|------|------------------|----|
| `test_run_sdlc_phases_branch_failure_returns_false` | `_ensure_branch` raises RuntimeError → returns `(False, None)` | AC-5 |
| `test_run_sdlc_phases_branch_failure_emits_event` | `_ensure_branch` failure emits `branch_setup_failed` event | AC-5, AC-8 |
| `test_run_sdlc_phases_branch_failure_no_phases_run` | After `_ensure_branch` failure, `_run_phase_sdk` is never called | AC-5 |

## Summary

- **Total tests:** 18
- **Group A:** 5 tests (ensure_branch fail-fast)
- **Group B:** 3 tests (branch-exists fallback)
- **Group C:** 2 tests (happy path regression)
- **Group D:** 4 tests (save_partial_work fail-fast)
- **Group E:** 2 tests (save_partial_work happy path)
- **Group F:** 3 tests (run_sdlc_phases integration)

## RED State

All tests are RED before Phase 8 implementation because:
- `_ensure_branch` currently wraps everything in `try/except` and ignores return codes
- `_save_partial_work` currently ignores return codes on add/commit/push
- `run_sdlc_phases` currently does not catch `RuntimeError` from `_ensure_branch`
- No `git_command_failed` events are currently emitted anywhere
