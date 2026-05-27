# STORY-766 — Fold Post-Merge Deploy + Re-Enqueue Sweep Into Morris's Fleet Vigilance

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Fleet-vigilance: post-merge deploy + re-enqueue sweep |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |
| Hard Dependency | **STORY-765** (Morris needs MANAGER cancel/re-enqueue authority for Mark-dispatched stories — without 765, this skill 403's on the re-enqueue step) |
| Soft Dependency | STORY-762 (failure_reason persistence — sweep classification works better with it) |

## Problem Statement

Morris has a `fleet-vigilance` skill (`deployment/vm/skills/fleet-vigilance/SKILL.md`) that runs every 30 min via heartbeat. It already covers token quota, stale claims, ghost completions, PR backlog, auth drift. **It does NOT cover the "PR merged → deploy → re-enqueue impacted failures" loop.**

Result: when a fix-PR merges (e.g., STORY-759 fixing the hardcoded-`main` bug), the deploy doesn't auto-fire for `deployment/hermes/*` changes (auto-deploy workflow only watches `tech_dev_agents/ops_console/*`, `frontend/*`, `deployment/ops-console/*`). And even if a deploy succeeds, no automated sweep re-enqueues the stories that were stuck on the bug. Mark or I have to do this by hand every time.

Concrete impact 2026-04-29:
- STORY-759 merged at 09:03Z → no auto-deploy fired (path filter).
- 11 stuck api-retail-target stories sat in failed state.
- Mark spent ~1 hour on the loop: stash manifest → push-code.sh → wait for SDK idle → restart pollers → smoke test → verify hash → re-enqueue 11 stories with `[RETRY]` prefix stripped → monitor.
- This is fully automatable.

## Target User / Use Case

**User:** Morris's heartbeat cron + fleet-vigilance skill.
**Today:** Mark is the post-merge shepherd.
**After this story:** fleet-vigilance has a new check (Check 9 — Post-Merge Deploy + Re-Enqueue Sweep) that runs every 30 min. When it detects a new merge to main since last run touching `deployment/hermes/*` (or other agent-VM paths): runs `push-code.sh`, verifies smoke tests, then sweeps `dispatch_items` for failed rows whose `failed_at > <merge_commit_ts - 24h>` and re-enqueues the eligible ones with `[RETRY]` prefix stripped (uses STORY-764's helper). Posts a Teams DM summary. Falls back to escalating to Mark on any deploy or smoke failure.

## Success Criteria

1. **SC-1 — Detect new merges to main touching agent-VM paths.** Skill runs `git -C <repo> log --since='<last-vigilance-ts>' --no-merges --pretty=format:%H -- deployment/hermes/ deployment/morris/` and collects merge commit SHAs. State persisted in `/home/hermes/state/morris/last-fleet-vigilance-merge-sweep.txt`.
2. **SC-2 — Run push-code.sh after detected merges.** Runs `bash deployment/vm/push-code.sh --wait 5 all` from a worktree on `main`. Captures output. Smoke test failures on any agent → CRIT, escalate to Mark via DM, do NOT proceed to re-enqueue sweep.
3. **SC-3 — Re-enqueue eligible failures.** After successful deploy, query `dispatch_items` for `status='failed'` rows whose `failed_at` is after the merge commit timestamp - 24h AND whose `failure_reason` matches a known fixed pattern (initial allowlist: `branch_setup_failed:%`, `phase_progress_stalled:%`, `sdk_died_no_phase_end:%` — each can be re-enqueued safely). Use STORY-764's `_strip_retry_prefix` helper. POST each to `/api/dispatch` with the merge commit SHA in the prompt header (audit trail).
4. **SC-4 — Cancel before re-enqueue if needed.** If the row is in non-terminal state (paused/needs_info/claimed/in_review with stale claims), use the new STORY-765 manager-override `DELETE /api/dispatch/queue/{story_id}?reason=...` first, then re-enqueue. Reason format: `Auto-sweep after merge <SHA[:8]>: STORY-N fix re-enables story.`.
5. **SC-5 — Idempotent.** Running the sweep twice in a row with no new merges is a no-op. State file prevents double-deploys.
6. **SC-6 — Teams DM summary.** Post a single DM summary per sweep cycle: `[FLEET-SWEEP] Post-merge deploy <SHA[:8]> + re-enqueue: <N> stories. Smoke: ✓. <details>`. CRIT cases get a separate DM.
7. **SC-7 — Audit trail.** Every action (deploy run, smoke result, cancel-with-override, re-enqueue) logs to `/home/hermes/state/morris/fleet-health.md` with timestamps + outcomes.
8. **SC-8 — Zero impact on other fleet-vigilance checks.** This is a new Check 9 that runs after Check 8. Existing Checks 0-8 are unmodified.
9. **SC-9 — Configurable sweep window.** Sweep looks back 24h by default, env-overridable via `FLEET_SWEEP_LOOKBACK_HOURS`.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `pytest tests/morris/test_fleet_vigilance_post_merge_sweep.py::test_detect_new_merges_since_last_run -v` | PASSED |
| SC-2 | `pytest tests/morris/test_fleet_vigilance_post_merge_sweep.py::test_smoke_failure_blocks_reenqueue -v` | PASSED |
| SC-3 | `pytest tests/morris/test_fleet_vigilance_post_merge_sweep.py::test_reenqueue_uses_strip_helper -v` | PASSED |
| SC-4 | `pytest tests/morris/test_fleet_vigilance_post_merge_sweep.py::test_manager_override_cancel_before_reenqueue -v` | PASSED |
| SC-5 | `pytest tests/morris/test_fleet_vigilance_post_merge_sweep.py::test_idempotent_no_new_merges -v` | PASSED |
| SC-6 | `pytest tests/morris/test_fleet_vigilance_post_merge_sweep.py::test_teams_dm_summary_format -v` | PASSED |
| SC-9 | `FLEET_SWEEP_LOOKBACK_HOURS=48 pytest tests/morris/test_fleet_vigilance_post_merge_sweep.py::test_lookback_window_configurable -v` | PASSED |
| End-to-end | After deploy, simulate a merge to a `deployment/hermes/*` file, watch fleet-vigilance heartbeat in 30 min, verify deploy ran + sweep posted | Documented manually in PR body |

## Test Criteria

- **Tests use mocked subprocess + mocked dispatch API** (no real push-code.sh, no real DB writes during unit tests).
- **State-file behavior** tested with tmp_path fixture — confirms idempotency.
- **Deploy failure path** tested: smoke fails on one agent → CRIT logged, no re-enqueue happens.
- **Audit content** tested: `fleet-health.md` after a sweep run contains the expected lines.

## Validation

| Step | Command | Pass criterion |
|------|---------|----------------|
| 1 | `pytest tests/morris/test_fleet_vigilance_post_merge_sweep.py -v` | All ≥ 7 tests GREEN |
| 2 | `pytest tests/ -x --ignore=tests/e2e -q` | Full suite GREEN; zero regressions |
| 3 | After deploy: trigger a no-op test merge of `deployment/hermes/agent-registry.json` (whitespace-only change) on main, observe fleet-vigilance next cycle, verify deploy ran + DM posted | Manual proof in PR body |
| 4 | Negative-case: simulate a smoke-test failure (mock push-code.sh exit code 1), verify sweep DOES NOT proceed to re-enqueue | Test fixture + assertion |

## Acceptance Criteria

- [ ] AC-1: New section in `deployment/vm/skills/fleet-vigilance/SKILL.md` titled "Check 9: Post-Merge Deploy + Re-Enqueue Sweep" with the same structure as Checks 0-8 (purpose, command, severity rubric, remediation steps).
- [ ] AC-2: New helper script `deployment/morris/scripts/post-merge-sweep.py` (or extend `heartbeat-collector.py`) implementing the merge-detection + deploy + sweep logic. Pure Python, stdlib subprocess + urllib.
- [ ] AC-3: State file path: `/home/hermes/state/morris/last-fleet-vigilance-merge-sweep.txt` containing the last-processed merge SHA + timestamp.
- [ ] AC-4: Failed-row eligibility allowlist by `failure_reason` prefix is documented in `SKILL.md` Check 9 — implementer can add new entries when STORY-762 categorises new prefixes.
- [ ] AC-5: Manager-override cancel uses `DELETE /api/dispatch/queue/{story_id}?reason=...` from STORY-765. Reason includes merge SHA.
- [ ] AC-6: Re-enqueue POST uses `_strip_retry_prefix` from STORY-764 (or its equivalent). Reason: avoid 5000-char cap.
- [ ] AC-7: Smoke-test failure ABORTS the sweep — no re-enqueue. CRIT escalation via Teams DM.
- [ ] AC-8: Existing fleet-vigilance Checks 0-8 unmodified (verified by diffing `SKILL.md` before/after on Phase 8).
- [ ] AC-9: Tests pass; zero regressions.
- [ ] AC-10: Logging — every sweep cycle appends to `fleet-health.md` with `[POST-MERGE-SWEEP]` prefix and outcome counts.
- [ ] AC-11: Error/logging AC — when push-code.sh hangs (e.g., unreachable VM), the sweep times out the script (default 15 min) and reports CRIT with the agent name. No infinite hangs.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | small — Python helper + skill markdown + tests |
| Timeline | URGENT — once 765 ships, this is the durable shepherd |
| Tech | Python 3.12 stdlib + bash subprocess. No new deps. |

## Performance Requirements
- Per cycle overhead (no new merges): < 1 second (just `git log --since` + state file check).
- Per cycle with deploy: 2–10 minutes typical (push-code.sh).
- Sweep query against dispatch_items: < 500ms (indexed by status + failed_at).

## Security Constraints
- [ ] Manager-override reason in re-enqueue MUST NOT include credentials or tokens. Truncate merge commit message to 200 chars.
- [ ] State file `/home/hermes/state/morris/*` permissions: 644, owned by hermes.
- [ ] No new auth surface (uses existing X-API-Key for ops console + existing SSH keys for push-code.sh).

## Operational Lifecycle

- **Configuration:** `FLEET_SWEEP_LOOKBACK_HOURS` env var (default 24).
- **How operators tune:** edit Morris's heartbeat cron environment, restart heartbeat service.
- **Monitoring:** `fleet-health.md` file shows every cycle's outcome. Loki picks up `[POST-MERGE-SWEEP]` lines.
- **Disable:** delete the helper script's executable bit, OR remove the skill section invocation. Document a kill-switch in skill.md.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Use STORY-765's manager-override cancel for non-terminal rows | Whether to extend sweep to `tech_dev_agents/ops_console/*` paths (separate auto-deploy workflow already covers those) | Re-enqueue without canceling first if row is non-terminal |
| Use STORY-764's `_strip_retry_prefix` on every re-enqueue | Whether to also re-enqueue rows that failed > 24h ago when a "stale fix" lands (defaults to no — too risky) | Skip the smoke-test gate before re-enqueueing |
| Persist last-merge state to disk for idempotency | Whether to also support manual override (`--force-sweep`) for ad-hoc operations | Re-deploy on every cycle even with no new merges |
| Post a Teams DM summary every cycle that did something | Whether silent cycles (no merges) should still ping Mark — current default: silent unless CRIT | Run push-code.sh from a feature branch (must be `main` worktree) |
| Time-box push-code.sh to 15 min | Whether to break the sweep into per-agent parallel deploys (probably overkill) | Hide deploy failures — always escalate CRIT to Mark |

## Files to Modify

- `deployment/vm/skills/fleet-vigilance/SKILL.md` — add Check 9 section.
- `deployment/morris/scripts/post-merge-sweep.py` — **new file**, the helper.
- `deployment/morris/scripts/heartbeat-collector.py` — invoke the new helper (or have the skill instruct manually).
- `tests/morris/test_fleet_vigilance_post_merge_sweep.py` — **new file**, ≥ 7 tests.
- `state/morris/SETUP-CHECKLIST.md` — document the new state file + env var.
- `features/story-766-morris-post-merge-deploy-cron/test-design.md` — Phase 7 deliverable.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- Existing fleet-vigilance Checks 0-8 in `SKILL.md` — preserve.
- `deployment/vm/push-code.sh` — caller; do not modify.
- The auto-deploy workflow `.github/workflows/deploy-ops-console.yml` — orthogonal.
- `requeue-failed` skill — overlaps but is operator-triggered, not heartbeat-triggered. Keep separate.
- `routes/dispatch.py` — Story 765 is the API change. This story is the consumer.

## Done Looks Like

```
$ pytest tests/morris/test_fleet_vigilance_post_merge_sweep.py -v
============================= test session starts ==============================
test_detect_new_merges_since_last_run PASSED
test_smoke_failure_blocks_reenqueue PASSED
test_reenqueue_uses_strip_helper PASSED
test_manager_override_cancel_before_reenqueue PASSED
test_idempotent_no_new_merges PASSED
test_teams_dm_summary_format PASSED
test_lookback_window_configurable PASSED
============================== 7 passed in 0.61s ===============================

$ pytest tests/ -x --ignore=tests/e2e -q
ALL PASSED

# Operationally — after deploy, on next heartbeat cycle:
# Heartbeat-cron logs:
[FLEET-VIGILANCE 2026-04-30T01:00Z] Check 9: Post-Merge Sweep
  - 1 new merge since last run: 14b771b (STORY-759 deploy)
  - push-code.sh --wait 5 all: started
  - dan ✓ derrick ✓ daisy ✓ devon ✓ — all smoke tests passed
  - Eligible failed rows: 11 (failure_reason LIKE 'branch_setup_failed:%')
  - Cancel-with-override: 11/11 (manager_override events written)
  - Re-enqueue: 11/11 (HTTP 201, queue_depth 11)
  - Teams DM sent.

# Teams DM:
[FLEET-SWEEP] Post-merge deploy 14b771b + re-enqueue:
- Deploy: ✓ all 4 agents, smoke green
- Re-enqueued: 11 api-retail-target stories (STORY-759 fix)
- Cycle time: 8m 14s
```

## Escalation Contract

1. **A VM is unreachable during deploy** (like devon was 2026-04-29) → Skip that VM, succeed on the rest, mark CRIT in fleet-health, escalate to Mark via DM. Do NOT auto-reboot the VM (Azure CLI is Mark-only per memory `feedback_no_azure_access.md`).
2. **Smoke test fails post-deploy** → Roll back is out of scope (push-code.sh handles its own rollback). Mark CRIT, escalate. The sweep does NOT proceed to re-enqueue.
3. **No `failed_at` index on dispatch_items** (query slow) → That's a DB ops issue; document in `audit.md`. Do NOT add an index in this story.
4. **Failure_reason is NULL on rows that should be sweep-eligible** (STORY-762 not yet shipped) → Skip those rows; they fail-safe (don't re-enqueue what we can't classify). When STORY-762 ships, the allowlist starts working for them.
5. **Cancel-with-override fails (HTTP 403 → STORY-765 not yet shipped)** → CRIT, escalate to Mark via DM. Do NOT proceed without override capability.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `deployment/vm/skills/fleet-vigilance/SKILL.md` (Check 9 added), new helper at `deployment/morris/scripts/post-merge-sweep.py` |
| Reference incident | 2026-04-29 STORY-759 + 11 api-retail-target re-enqueue chain |
| Architecture | Morris cron → fleet-vigilance skill (markdown instructions) → invokes Python helper → calls push-code.sh + dispatch API |
| Test pattern | Mock subprocess + mock urllib + tmp_path fixtures (existing pattern in `tests/morris/`) |

## Out of Scope

- Auto-rollback on smoke failure (push-code.sh handles its own rollback semantics).
- Auto-reboot of unreachable VMs (Azure CLI is Mark-only).
- Cross-repo sweeps (e.g., changes to `tech-gc-knowledgebase` triggering ops-console deploys) — separate concern.
- Real-time webhook-triggered sweep (this is heartbeat polling — every 30 min).
- Bulk-cancel API to make the override calls atomic (separate STORY if motivated).

## Notes for Implementer

- **STORY-765 is a hard gate.** Verify `gh pr list --state merged --search "STORY-765" --repo hpi-gorillacommerce/tech-dev-agents` returns a merged PR before claiming Phase 7. If not, write QUESTION.md.
- **Reference Check 0** (token quota) in fleet-vigilance SKILL.md as the structural pattern to copy. Use the same severity rubric, same DM format, same state-file-update conventions.
- **Use STORY-764's helper** for prompt-prefix stripping when re-enqueueing. If 764 hasn't shipped yet, replicate the regex inline with a comment to migrate later.
- **`fleet-health.md` is git-untracked** — never commit it. The file is on Morris's VM filesystem only. Existing SKILL.md has a CRITICAL section about this. Don't violate it.
- **Test order matters:** unit tests first (mocked), then a manual smoke (PR-body documented) after deploy.
- The 2026-04-29 incident is the canonical playback. Mark spent ~1 hour doing exactly this loop by hand. Replay that experience in your test design.
