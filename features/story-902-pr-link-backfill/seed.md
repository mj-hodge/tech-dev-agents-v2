# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Criticality | important |
| Feature Name | dispatch-pr-link-backfill |
| Frontend | false |

## Problem Statement

Most `in_review` rows in `dispatch_jobs` have `pr_number = NULL` (25 out of 27 observed on 2026-05-06). The dispatch poller's PR detection by output-parsing is fragile and misses most stories. STORY-901 ships a `pr_merge_sweeper` that transitions `in_review → completed` when the linked PR merges, but it can't help rows whose `pr_number` is not set. This gap means the automated completion pipeline is broken for ~93% of `in_review` jobs.

This story fixes the gap with a backfill scanner that runs every 5 minutes, finds `in_review` rows with `pr_number IS NULL`, infers the PR branch from the `story_id`, queries the GitHub REST API for matching open/closed PRs, and writes the `pr_number` back to `dispatch_jobs`.

## Target User / Use Case

**Ops automation:** Within 5 minutes of a dispatch agent opening a PR, the backfill sweeper links the PR number to the dispatch job row. Once linked, STORY-901's merge sweeper can detect the PR merge and transition the story to `completed` automatically — no manual intervention required.

## Success Criteria

| # | Criterion | Verification |
|---|-----------|-------------|
| AC-1 | Sweeper runs every 300s (PR_LINK_BACKFILL_INTERVAL) as a background asyncio task | Code review |
| AC-2 | SQL query selects job_id, repo, story_id from dispatch_jobs JOIN dispatch_state_current WHERE state='in_review' AND pr_number IS NULL | Unit test |
| AC-3 | Branch name inferred from story_id: STORY-N -> `story-N/` prefix | Unit test |
| AC-4 | GitHub REST API called: GET /repos/hpi-gorillacommerce/{repo}/pulls?head=...&state=all&per_page=5 | Unit test |
| AC-5 | If >= 1 PR found, most recent (highest created_at) is selected; pr_number is UPDATE'd on dispatch_jobs | Unit test |
| AC-6 | If no PR found, row is skipped (debug log only, no UPDATE) | Unit test |
| AC-7 | Each row iteration wrapped in try/except — errors logged, loop never crashes | Unit test |
| AC-8 | Uses urllib + GitHub REST API (NOT gh CLI) with OPS_GITHUB_TOKEN / GITHUB_TOKEN env fallback | Code review |
| AC-9 | Registered in main.py lifespan, cancelled on shutdown | Code review |
| AC-10 | Idempotent — rows with pr_number already set are excluded by SQL WHERE clause | Unit test |

## Constraints
| Constraint | Value |
|------------|-------|
| Language | Python 3.12 |
| HTTP | urllib only — no gh CLI, no httpx in background task |
| Token | OPS_GITHUB_TOKEN env var → fallback GITHUB_TOKEN |
| Interval | PR_LINK_BACKFILL_INTERVAL env var, default 300 seconds |
| Logging | structlog/stdlib logger only |
| Mutation guard | DO NOT modify dispatch_pr_merge_sweeper or dispatch_state_apply |
| Budget | minimal |

## Security Constraints (Non-Negotiable)

- [ ] GitHub token read-only access: only reads PR list
- [ ] Token never logged
- [ ] 4xx/5xx/network errors return empty result (logged WARNING) — no exception propagates from tick

## Technical Analysis

### Branch Convention
Story IDs follow the pattern `STORY-N`. The dispatch system creates branches named `story-N/...` (lowercase, with suffix). The sweeper infers the branch prefix from `story_id` via regex: `STORY-(\d+)` -> `story-{n}/`.

### GitHub REST API
```
GET /repos/hpi-gorillacommerce/{repo}/pulls?head=hpi-gorillacommerce:story-N/&state=all&per_page=5
Authorization: token {OPS_GITHUB_TOKEN or GITHUB_TOKEN}
```

Returns JSON array of PR objects. Pick the one with the highest `created_at`. Extract `number` field.

### SQL
```sql
SELECT dj.job_id, dj.repo, dj.story_id
FROM dispatch_jobs dj
JOIN dispatch_state_current sc ON sc.job_id = dj.job_id
WHERE sc.state = 'in_review'
  AND dj.pr_number IS NULL
```

### Update
```sql
UPDATE dispatch_jobs SET pr_number = $1 WHERE job_id = $2
```

## Test Criteria

- [ ] T01 — happy path: gh returns 1 matching PR → pr_number updated in DB, INFO logged
- [ ] T02 — no PR found: gh returns [] → DB not updated, DEBUG logged
- [ ] T03 — multiple PRs: gh returns 3 PRs with different created_at → most recent (highest) picked
- [ ] T04 — idempotent: rows with pr_number already set are not returned by SQL (WHERE pr_number IS NULL) → tick finds nothing
- [ ] T05 — GitHub API error (HTTP 500): logged WARNING, no crash, returns without updating
- [ ] T06 — story_id doesn't match STORY-N pattern (e.g. "EPIC-5"): row skipped silently
- [ ] T07 — background task runs tick at interval, CancelledError exits cleanly
- [ ] T08 — background task: pool=None → tick does nothing, loop continues

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| HTTP library | urllib (not httpx/aiohttp) | Matches existing `_gh_pr_view` pattern in self_healing.py; Docker image constraint |
| Branch matching | `head=org:story-N/` prefix in API query | GitHub API supports prefix matching on head param |
| PR selection | Highest `created_at` | Most recent PR is most likely the active one |
| Interval | 300s (5 min) | Sufficient for backfill; not too aggressive on GitHub API |
| Token env var | OPS_GITHUB_TOKEN with GITHUB_TOKEN fallback | Matches existing convention |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| GitHub API rate limit | Low | Low | 5-min interval; only queries rows with NULL pr_number |
| Branch naming mismatch | Low | Low | Silently skips non-matching story_ids; debug log |
| Multiple PRs for same story | Medium | Low | Takes most recent by created_at |

## Validation

- Deploy to ops-console and confirm that within 1 loop cycle (≤5 min) all 25 NULL rows get their `pr_number` populated (or remain NULL if no matching PR branch exists on GitHub)
- Confirm INFO log lines like: `pr_link_backfill: linked story=STORY-N repo=R job=UUID pr=N`
- Confirm STORY-901's sweeper then transitions rows with pr_number set to `completed` within its own cycle

## Codebase Context
| Aspect | Details |
|--------|---------|
| Affected files | `tech_dev_agents/ops_console/services/self_healing.py`, `tech_dev_agents/ops_console/main.py` |
| New test file | `tests/ops_console/test_dispatch_pr_link_backfill.py` |
| DB tables | `dispatch_jobs` (pr_number column), `dispatch_state_current` (state column, job_id FK) |
| Pairs with | STORY-901 (pr_merge_sweeper — transitions in_review → completed when PR merges) |
| Pattern to mirror | `dispatch_expired_lease_sweeper` + `dispatch_stuck_agent_watcher` in self_healing.py |
