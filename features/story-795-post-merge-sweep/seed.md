# STORY-795 — Rework Fleet Vigilance Post-Merge Sweep

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Fleet-vigilance: post-merge deploy + re-enqueue sweep (Check 8) |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |
| Rework of | STORY-766 (PR #244 closed with REQUEST_CHANGES) |

## Problem Statement

Morris's fleet-vigilance skill covers token quota, stale claims, ghost completions, PR backlog,
and auth drift — but does NOT cover the "PR merged → deploy → re-enqueue impacted failures" loop.

When a fix-PR merges touching `deployment/hermes/*` or `deployment/morris/*`, no auto-deploy fires
(the CD workflow only watches `tech_dev_agents/ops_console/*`, `frontend/*`, `deployment/ops-console/*`).
Mark has to manually shepherd: stash manifest → push-code.sh → smoke test → re-enqueue failed stories.

Concrete impact 2026-04-29: STORY-759 merged at 09:03Z, 11 stuck stories sat in failed state,
Mark spent ~1 hour on the manual loop.

## Required Fixes (from PR #244 review)

1. **H-2 (CRITICAL):** Remove `--no-merges` from git log. This repo uses merge commits for PRs.
   `--no-merges` filters out the exact commits the sweep needs to detect.
2. **H-1 (CRITICAL):** DM delivery integration. The sweep must deliver results via Teams Graph API,
   following the heartbeat data collector pattern.
3. **T-1:** Fix `test_lookback_window_configurable` — replace `assert 48 in url or True` (always passes)
   with `assert 'lookback_hours=48' in captured_url`.
4. **M-1:** First-run with no state file must self-initialize from HEAD. Never fall back to `since=1970`.
5. **M-3:** Check 8 ordering in SKILL.md — insert after Check 7 (VM health), before Checks 9-14.
6. **S-1:** Guard for empty `OPS_CONSOLE_API_KEY` — raise `ValueError` if unset.

## Success Criteria

1. **SC-1:** Detect new merges to main touching agent-VM paths (no `--no-merges` flag).
2. **SC-2:** Run push-code.sh after detected merges; smoke failure blocks re-enqueue.
3. **SC-3:** Re-enqueue eligible failures with `[RETRY]` prefix stripped.
4. **SC-4:** Cancel non-terminal rows via STORY-765 manager override before re-enqueue.
5. **SC-5:** Idempotent — no new merges = no-op. State file prevents double-deploys.
6. **SC-6:** Teams DM summary via Graph API (H-1 fix).
7. **SC-7:** Audit trail in fleet-health.md with `[POST-MERGE-SWEEP]` prefix.
8. **SC-8:** Check 8 in SKILL.md, ordered after Check 7.
9. **SC-9:** Configurable lookback window via `FLEET_SWEEP_LOOKBACK_HOURS`.

## Test Criteria

1. `_validate_api_key()` raises `ValueError` when `OPS_CONSOLE_API_KEY` is unset or empty.
2. `_detect_merges` git-log output is parsed with a 40-char hex SHA regex, not a positional split heuristic.
3. `check_post_merge_sweep()` returns `error_unavailable` when the API key guard fires.
4. First-run initialises state from HEAD (no `since=1970` fallback).
5. `--no-merges` is absent from the git-log invocation.
6. Smoke failure (`push-code.sh` non-zero) blocks re-enqueue and sets severity `crit`.
7. `[RETRY]` / `[RETRY-N]` prefix is stripped before re-enqueue POST.
8. State file is written after a successful sweep (idempotency).

## Validation

- CI: `pytest tests/morris/test_fleet_vigilance_post_merge_sweep.py` passes (all 11+ tests green).
- CI: `pytest tests/test_sdlc_framework_compliance.py` passes (seed-section + Frontend gate).
- Manual: set `OPS_CONSOLE_API_KEY=""` and confirm `error_unavailable` is returned immediately.
- Manual: confirm malformed git-log lines (non-hex first token) are silently skipped.

## Files to Modify

- `deployment/vm/skills/fleet-vigilance/SKILL.md` — add Check 8 section after Check 7.
- `deployment/morris/scripts/post_merge_sweep.py` — **new file**, the helper.
- `deployment/morris/scripts/heartbeat-collector.py` — invoke the new helper.
- `tests/morris/test_fleet_vigilance_post_merge_sweep.py` — **new file**, ≥ 7 tests.
- `features/story-795-post-merge-sweep/seed.md` — this file.
- `features/story-795-post-merge-sweep/test-design.md` — Phase 7 deliverable.
- `features/story-795-post-merge-sweep/feature-spec.md` — Phase 7 deliverable.
