# STORY-903: Deterministic PR Detection in Dispatch Poller v2

**Phase:** 1 — Seed  
**Scope:** Small  
**Frontend:** false  
**Created:** 2026-05-06

---

## Problem Statement

`dispatch_poller_v2.py` detects "agent opened a PR" by scanning SDK output for `PR #N` text
(`_PR_NUMBER_RE`). In practice, agents create PRs via `gh pr create`, which returns a bare
GitHub URL (`https://github.com/.../pull/341`). The URL contains no `PR #N` text, so the
regex fires only when agents also narrate PR creation in free-form output — approximately
7% of runs (2 of 27 historically).

When detection fails, `_success_transition_payload` emits `needs_info` with reason
`missing_pr_linkage` instead of `submitted`. The story moves to `needs_info` / human_queue
and waits for a response that never comes. STORY-901's `pr_merge_sweeper` only processes
`in_review` rows with `pr_number` set, so these stories are never auto-merged.
STORY-902 ships a backfill scanner as a band-aid; this story fixes the source.

---

## Approach

Pure addition to `dispatch_poller_v2.py`:

1. **New function `_lookup_pr_by_branch`** — uses `urllib.request` to query:
   `GET https://api.github.com/repos/hpi-gorillacommerce/{repo}/pulls?head={org}:{branch}&state=all&per_page=5`
   Returns the highest `created_at` PR's number, or `None`.

2. **Modified `_success_transition_payload`** — adds optional `story_id`, `repo`, `branch`
   kwargs and implements the three-level resolution:
   - **Fast path:** regex `_PR_NUMBER_RE` on SDK output (unchanged, unchanged code)
   - **Fallback:** GitHub REST query by branch
   - **Null:** both miss → `needs_info` (degrades to STORY-902 backfill territory)

3. **Call site update** — `poll_loop` passes `claim.story_id`, `claim.repo`, `claim.branch`
   to `_success_transition_payload`.

Logging: `logger.info` on regex hit, `logger.info` on REST hit, `logger.warning` when
REST is called but returns no result. Both paths return identical `(story_id, pr_number)` shape.

---

## Files Changed

| File | Change |
|------|--------|
| `deployment/hermes/dispatch_poller_v2.py` | Add `_lookup_pr_by_branch`; extend `_success_transition_payload`; update `poll_loop` call site |
| `tests/deployment/test_poller_pr_detection.py` | New test file (5 test cases) |

---

## Test Criteria

- [ ] **TC-1** Regex matches output → `submitted` returned, REST fallback NOT called
- [ ] **TC-2** Regex misses, REST returns PR → `submitted` returned with gh PR number
- [ ] **TC-3** Both miss → `needs_info` with `reason=missing_pr_linkage`, story NOT crashed
- [ ] **TC-4** GitHub API returns 4xx/5xx → logged, returns `None`, poller continues
- [ ] **TC-5** story_id has no STORY-N pattern → fallback skipped, regex result wins or `None`
- [ ] **TC-6** `claim.branch` set → used directly (no story_id derivation needed)
- [ ] Existing `test_dispatch_poller_v2_regressions.py` tests remain GREEN (no regressions)

---

## Validation

1. Deploy to agent fleet via `./deployment/vm/push-code.sh all`
2. Monitor next 10 completed dispatch jobs for `submitted` events with `pr_number` set
3. Confirm `in_review` rows in dashboard have `pr_number != null` for new completions
4. No increase in `needs_info` queue depth after deploy

---

## Acceptance Criteria

- [ ] `features/story-903-poller-pr-detection/investigation.md` written
- [ ] `features/story-903-poller-pr-detection/seed.md` written
- [ ] `features/story-903-poller-pr-detection/test-design.md` written
- [ ] `dispatch_poller_v2.py`: regex path retained; gh REST fallback added; both paths return same `(event_type, event_data)` shape; `logger` logs which path matched
- [ ] Tests at `tests/deployment/test_poller_pr_detection.py` cover TC-1 through TC-6
- [ ] `tests/deployment/test_dispatch_poller_v2_regressions.py` still GREEN
- [ ] Tracking docs updated
- [ ] Commit, push, PR opened with investigation findings + deployment note

---

## Phase Path

`1 → 7 → 8 → Done` (Small scope)

## Out of Scope

- Changing `_PR_NUMBER_RE` or `_extract_pr_number_from_output` (pure addition only)
- Modifying v2 state machine or migration SQL
- Changing ops_console routes
