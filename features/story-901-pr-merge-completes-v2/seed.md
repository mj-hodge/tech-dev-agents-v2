# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Criticality | important |
| Feature Name | pr-merge-completes-v2 |
| Frontend | false |

## Problem Statement

When an agent finishes a story and opens a PR (transitioning the v2 dispatch row to `in_review`), the row stays in `in_review` forever after the PR is merged. Nothing emits an `accepted` event (the correct terminal event for merged PRs). The operator must manually call `/api/dispatch/v2/review-outcome` with `outcome=accepted`. On 2026-05-05, three stories (STORY-885 PR #309, STORY-886 PR #308, STORY-873 PR #310) had their PRs merged but their v2 rows remained stuck in `in_review`, requiring force-cancels.

## Target User / Use Case

**Morris and Mark (operators):** After merging a PR on GitHub, the v2 dispatch row should automatically transition to `completed` within the next sweeper tick (≤5 minutes), with no manual intervention.

## Success Criteria

- [ ] New `dispatch_pr_merge_sweeper` async loop in `tech_dev_agents/ops_console/services/self_healing.py`
- [ ] Registered in `main.py` lifespan as `asyncio.create_task(dispatch_pr_merge_sweeper(pool=db_pool), name="dispatch-pr-merge-sweeper")`
- [ ] Scans `in_review` rows where `dispatch_jobs.pr_number IS NOT NULL` every 300 seconds (5 min, env-overridable `PR_MERGE_SWEEP_INTERVAL`)
- [ ] Calls `gh pr view <N> --repo <repo> --json state,mergedAt` for each candidate row
- [ ] Emits `accepted` event (via `record_event()`) for rows whose PR has `mergedAt != null`
- [ ] Does NOT emit for PRs still open or PRs closed without merge
- [ ] Idempotent: once `accepted` is emitted, state is terminal — subsequent sweeps skip the row
- [ ] structlog-JSON logging throughout; no `print()` calls
- [ ] Tests RED → GREEN: all 5 acceptance test cases pass

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | minimal |
| Timeline | 1 day |
| Scale | ≤20 in_review rows at any time |

## Security Constraints (Non-Negotiable)

- [ ] Do NOT modify the dispatch_state_apply trigger or migration SQL
- [ ] Do NOT emit `completed` directly (not a valid event_type) — always `accepted`
- [ ] Do not break existing transitions (cancelled, failed, rejected)

## Operational Lifecycle

- `dispatch_pr_merge_sweeper` runs as an asyncio task inside the ops-console process (same pattern as `dispatch_expired_lease_sweeper`)
- Sweep interval default: 300s (5 min), configurable via `PR_MERGE_SWEEP_INTERVAL` env var
- Each sweep: query `in_review` rows with `pr_number`, call `gh pr view` per distinct `(repo, pr_number)` pair, emit `accepted` for merged PRs
- `gh` CLI must be authenticated on the ops-console server (already required for Morris workflows)
- Cron on Morris VM is NOT needed — this runs inside the ops-console process

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files | `tech_dev_agents/ops_console/services/self_healing.py`, `tech_dev_agents/ops_console/main.py` |
| Related components | `dispatch_v2_service.record_event()`, `dispatch_state_apply()` trigger |
| Current behavior | `in_review` rows stay in_review permanently after PR merge |
| Desired change | Add background sweeper that emits `accepted` on PR merge detection |
| Architecture constraints | Option B (reconciler loop) — no GitHub webhook config, mirrors existing self-healing pattern |

## Test Criteria

1. `tests/test_story901_pr_merge_completes_v2.py` — RED → GREEN suite covering:
   - Emits `accepted` event for an `in_review` row whose PR is merged (`mergedAt != null`)
   - Idempotent: running sweep twice on the same merged PR does not double-emit (row is terminal after first sweep)
   - Does NOT emit for rows whose PR is still open (`state == "OPEN"`)
   - Does NOT emit for rows whose PR is closed without merge (`state == "CLOSED"`, `mergedAt == null`)
   - Skip rows where `pr_number IS NULL` (no PR linked yet)

## Validation

- After deploy, merge a test PR linked to an `in_review` v2 row; within 5 minutes the row should appear in `completed` lane
- Confirm structlog shows `pr_merge_sweeper.accepted` log line with `job_id`, `repo`, `pr_number`
- Run `pytest tests/test_story901_pr_merge_completes_v2.py -v` — all 5 tests GREEN
