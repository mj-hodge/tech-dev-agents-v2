# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Criticality | important |
| Feature Name | pr-link-backfill-rework-jobs |
| Frontend | false |

## Problem Statement

STORY-902's `dispatch_pr_link_backfill_sweeper` infers the GitHub branch name from `story_id` as `story-N/` (e.g. `STORY-885` → `story-885/...`). That works for net-new stories. It does **NOT** work for Morris's rework dispatches, which use a fresh story_id for a child task whose actual PR lives on the parent's branch.

Real titles seen in the 2026-05-12 stale-row triage:
- `"PR 319 rebase: STORY-640 approved needs rebase"` — dispatch is STORY-839, real PR is on `story-640/...`
- `"Rebase PR #398 (STORY-707 OODA Scorer)"` — dispatch is STORY-929, real PR is on `story-707/...`
- `"PR #260 rework: seed.md..."` — dispatch is STORY-822, PR number 260 is in the title
- `"Fix PR #404 review findings (STORY-713 Approval Queue)"` — dispatch is STORY-964, real PR is for STORY-713

The sweeper queries GitHub with the wrong head ref (`story-839/...`), gets zero PRs back, never populates `pr_number`, and the row sits forever in `in_review` with NULL PR linkage. Multiple downstream automations gate on `pr_number IS NOT NULL` and silently skip the row.

This story extends the sweeper with a **fallback parser** that pulls the PR number directly from the row's `title` or `prompt` text when the branch-lookup fails. Most rework titles contain the PR number in plain text — no GitHub round-trip needed.

## Target User / Use Case

**Ops automation:** Rework jobs become as completable as net-new jobs. Once `pr_number` is populated, the PR-merge sweeper transitions the row to `completed` when the PR merges, exactly like any other dispatch.

## Success Criteria

- [ ] Add a `_extract_pr_number_fallback(row: dict) -> int | None` helper in `tech_dev_agents/ops_console/services/self_healing.py`:
  - Try `title` first, then `prompt`. (Title is shorter and usually has the explicit PR ref.)
  - Match these regexes in order, return the first hit cast to `int`:
    1. `\bPR\s*#?\s*(\d{1,6})\b` (e.g. `PR #320`, `PR 319`, `pr#404`)
    2. `\bpull[s]?[/\s#]+(\d{1,6})\b` (e.g. `pulls/352`, `pull #1234`)
  - Return `None` if no match. Cap to 6 digits to avoid catching long numbers.
- [ ] Modify `_pr_link_backfill_tick()` in the same file:
  - When the GitHub branch lookup returns zero PRs, call `_extract_pr_number_fallback(row)`.
  - If a fallback PR number is found, verify it exists on GitHub via `GET /repos/{repo}/pulls/{n}` (one request) — confirm: not 404 AND the response's repo matches the row's `repo`.
  - If verified, update `dispatch_jobs.pr_number` and log `INFO`: `pr_link_backfill: fallback-linked story=STORY-N repo=R job=UUID pr=N (from title|prompt)`.
  - If the fallback verify returns 404 or mismatched repo, log `DEBUG` and skip — do NOT update.
- [ ] No change to existing happy path (branch-name lookup) — only adds the fallback branch.
- [ ] New tests in `tests/ops_console/test_dispatch_pr_link_backfill.py` (or whatever the existing test module is):
  - `_extract_pr_number_fallback` happy paths for the 5 patterns above + the 4 real-world examples from the problem statement.
  - `_extract_pr_number_fallback` rejects: `STORY-822` (looks numeric but no PR/pull keyword), `2025` (year), `12345678` (too long), empty title/prompt.
  - Tick test: GitHub branch lookup returns []; title contains `PR #320`; mocked verify call returns 200 + matching repo → `dispatch_jobs.pr_number` updated to 320.
  - Tick test: GitHub branch lookup returns []; title contains `PR #999`; mocked verify call returns 404 → no update, no crash.
  - Tick test: GitHub branch lookup returns []; title contains `PR #320`; mocked verify call returns 200 but repo='other-repo' → no update (mismatch guard).
  - Existing tests for STORY-902 backfill happy path still pass.

## Constraints
| Constraint | Value |
|------------|-------|
| Language | Python 3.12 |
| HTTP | urllib only, no gh CLI, no httpx (match existing sweeper pattern) |
| Token | `OPS_GITHUB_TOKEN` env var → fallback `GITHUB_TOKEN` (same as existing sweeper) |
| Logging | structlog/stdlib logger only; no `question_text`-style content leaks |
| Mutation guard | Do NOT modify STORY-901's `dispatch_pr_merge_sweeper` or any other sweeper |
| Coordination | Lands independently of STORY-918 (different layer — STORY-918 fixes a SQL trigger, this fixes a Python sweeper). Both can ship in either order. |

## Security Constraints (Non-Negotiable)

- [ ] Token never logged.
- [ ] Verify-call errors (4xx, 5xx, network) → log `WARNING`, skip update, no exception propagates from tick.
- [ ] Repo-mismatch guard is mandatory — a PR number found in a title could legitimately reference a PR in a different repo (e.g. a tracking ticket). Refusing to link wrong-repo PRs prevents data corruption.

## Test Criteria

- [ ] T01 — regex helper: `"PR #320 rework: foo"` → 320
- [ ] T02 — regex helper: `"Rebase PR 319 onto main"` → 319
- [ ] T03 — regex helper: `"Fix PR #404 review findings"` → 404
- [ ] T04 — regex helper: `"pulls/352"` → 352
- [ ] T05 — regex helper: `"STORY-822 rework"` → None (no PR keyword)
- [ ] T06 — regex helper: `""` → None (empty)
- [ ] T07 — tick: branch lookup empty + fallback hit + verify 200 + repo match → DB updated, INFO logged
- [ ] T08 — tick: branch lookup empty + fallback hit + verify 404 → no DB update, DEBUG logged
- [ ] T09 — tick: branch lookup empty + fallback hit + repo mismatch → no DB update
- [ ] T10 — tick: branch lookup returns 1 PR (happy path) → fallback parser NOT invoked

## Validation

- Deploy and confirm via Loki (after STORY-915 Promtail lands, or via journald scrape on ops-console host): within one sweeper cycle (≤5 min) of deploy, any `in_review` row with NULL `pr_number` AND a parseable PR ref in title gets populated.
- Manually verify on one canary row before bulk effect lands.

## Codebase Context
| Aspect | Details |
|--------|---------|
| Affected files | `tech_dev_agents/ops_console/services/self_healing.py` (add helper + extend `_pr_link_backfill_tick`), `tests/ops_console/test_dispatch_pr_link_backfill.py` (new tests) |
| Reference patterns | `dispatch_pr_link_backfill_sweeper` and `_pr_link_backfill_tick` (STORY-902) — already does urllib + GitHub REST; mirror exactly |
| Reference patterns | `dispatch_pr_merge_sweeper` (STORY-901) — uses the same GitHub REST + urllib + token pattern |
| Failure incident | 2026-05-12 — 26 stale `in_review` rows (1.7–10.8 days old) had explicit PR numbers in title/prompt but sweeper couldn't link them because branch-inference fails for rework dispatches |
| Pairs with | STORY-918 (v1↔v2 pr_number sync — fixes the SQL-layer copy; this story fixes the Python-layer lookup. Independent and complementary.) |
| Out of scope | Inferring PR from commit SHA, GH search API, or any heuristic beyond title/prompt regex. Keep narrow. |
