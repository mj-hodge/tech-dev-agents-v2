# Seed: STORY-701 — Failure classification + Morris DLQ triage

## Overview

| Field | Value |
|-------|-------|
| Mode | feature_add |
| Scope | small |
| Frontend | false |
| Feature Name | Categorical `failure_reason` on dispatch_items + Morris daily DLQ triage |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | main |
| Status | Seed written 2026-04-25 |
| Priority | 70 — surfaces the failed pile so it stops rotting silently |
| Depends on | STORY-700 (events table) — must merge first |

---

## 1. Idea / Trigger

Today, `status='failed'` is terminal and silent. STORY-570/571/572 went to `failed` last week and would have stayed there forever if I hadn't found them while debugging unrelated work. The failed pile is institutional debt that nobody triages.

DLQ pattern: when a row enters `failed`, classify WHY (categorical), and have Morris draft a daily triage so Mark sees the pile + suggested actions instead of finding broken stories by accident.

## 2. Problem Statement

- Failed stories rot silently. No triage, no surfacing, no closure.
- Tonight's queue has multiple stale `failed` rows with no record of WHY they failed beyond "rc=1" (useless for triage).
- Without categorical reasons, you can't compute "same failure_reason 3+ times in last hour = systemic bug" — that's a phase-2 alert from the implementation plan that depends on this column.

## 3. Scope Classification

**Small.** Three thin slices:
1. Schema: add `failure_reason VARCHAR(40)` column to `dispatch_items`.
2. Classifier: `_report_fail` in poller categorizes the failure based on signals already available (exit_code, error message, prior state) and POSTs `failure_reason` along with the existing fail call. Backend persists.
3. Morris triage cron: daily 10am UTC, reads recent failures, drafts a Teams DM. Format and depth Morris figures out — the seed only mandates the inputs (DB query) and channel (Teams DM via m365 to Mark's email).

## 4. Codebase Context

### Schema migration: `scripts/migrations/011_dispatch_failure_reason.sql`

```sql
ALTER TABLE dispatch_items
    ADD COLUMN IF NOT EXISTS failure_reason VARCHAR(40);

COMMENT ON COLUMN dispatch_items.failure_reason IS
    'Categorical reason for status=failed. NULL when status != failed. '
    'Set by the dispatch poller when it transitions a row to failed.';

-- Idempotent. Safe to re-run.
```

### Failure taxonomy (Q5 confirmed, mutable later)

| `failure_reason` | When set | Hint for triage |
|---|---|---|
| `rate_limit_exhausted` | exit_code=429 AND retry attempt >= MAX_RETRY_ATTEMPTS | Pause until Anthropic quota refreshes |
| `branch_setup_failed` | journal contains `branch_setup_failed` event | git/refspec issue; investigate ls-remote behavior |
| `gate_rejection` | exit_code=422 from /complete (the gate rejected the deliverable) | Agent shipped incomplete work; re-dispatch with sharper prompt |
| `cross_story_validation_missing` | retry POST got 422 with prompt-references-other-STORY error | Re-dispatch with `cross_story_reference: true` |
| `excessive_retries` | retry counter >= MAX_RETRY_ATTEMPTS without other classification | Story prompt may be malformed; needs human review |
| `needs_info_unanswered` | story sat in needs_info > 7 days | Operator hasn't answered the QUESTION.md; nudge or close |
| `agent_died` | last heartbeat > 1 hour AND no commits | Agent VM had an issue; investigate that VM's recent logs |
| `unknown` | none of the above | Default; investigate manually |

Add or remove categories later — they're stored as VARCHAR, no enum lock-in.

### Classifier: `_report_fail` in `dispatch_poller.py`

Add classification logic before the `/fail` POST. Use existing signals (exit_code, error message text, retry count, story state). When in doubt, use `unknown` — never raise.

```python
def _classify_failure(exit_code, error_text, retry_count, ...) -> str:
    # ordered checks; first match wins
    if exit_code == 429 and retry_count >= MAX_RETRY_ATTEMPTS:
        return "rate_limit_exhausted"
    if "branch_setup_failed" in error_text:
        return "branch_setup_failed"
    if exit_code == 422 and "Prompt references" in error_text:
        return "cross_story_validation_missing"
    if exit_code == 422:
        return "gate_rejection"
    if retry_count >= MAX_RETRY_ATTEMPTS:
        return "excessive_retries"
    return "unknown"
```

POST body to `/dispatch/fail/{story_id}` adds `failure_reason: <category>`. Backend route stores in the new column.

### Morris triage cron

A new Morris hermes cron, `0 10 * * *` (10:00 UTC daily, which is 5 EST winter / 6 EDT summer per Mark's Q6 b answer).

Script at `deployment/morris/scripts/dlq_triage.py` reads:

```sql
SELECT story_id, repo, failure_reason, claimed_by, claimed_at, completed_at AS failed_at,
       (SELECT prompt FROM dispatch_items di2 WHERE di2.id = di.id) AS prompt_excerpt
  FROM dispatch_items di
  WHERE status = 'failed'
    AND failure_reason IS NOT NULL
    AND completed_at > now() - interval '7 days'
  ORDER BY completed_at DESC
  LIMIT 25;
```

The script outputs structured JSON to stdout. **Morris's review-prs / triage skill** (a separate skill update Morris drafts itself; not part of this story) consumes that JSON and decides how to format the Teams DM. Q7 decision: Morris owns the format.

So the cron runs the data extraction; Morris's skill decides what to say with it.

### Files NOT to touch

- `dispatch_events` table from STORY-700 — that's a parallel observability layer; failure_reason is on the canonical `dispatch_items` row for the simple `WHERE status=failed` query.
- Frontend dashboard — DLQ surface for the dashboard is a follow-up.

## 5. Out of Scope

- A `/api/dispatch/dlq` REST endpoint — direct SQL via Morris is fine for now.
- Auto-retry rules per failure_reason. The smart-retry policy is a separate story.
- Frontend DLQ panel.
- Re-classifying historical failures — forward-only.

## Test Criteria

Phase 7 produces `test-design.md` and RED tests covering:

1. **Migration applies idempotently** — `011_dispatch_failure_reason.sql` runs on fresh + pre-populated DB.
2. **`_classify_failure` taxonomy** — table-driven test feeding each (exit_code, error_text, retry_count) combination and asserting the expected category. All 8 categories covered.
3. **Classifier defaults to `unknown`** — unknown inputs return `unknown`, never raise.
4. **`_report_fail` writes failure_reason** — mock the DB; verify the POST body to `/fail` includes the categorized reason.
5. **Backend persists failure_reason** — `/dispatch/fail` route extracts the field and writes to dispatch_items.
6. **Morris triage cron query returns expected shape** — fixture seeds 5 failed rows with various reasons; assert the SQL returns them sorted DESC by failed_at.
7. **Triage cron is idempotent** — running twice in a row doesn't double-DM (the Teams send is Morris's responsibility, but the data extraction must not break on re-run).

## Validation

After Phase 8 lands + ops-console redeploys:

1. Force-fail a small test story with a known cause (e.g., bad rebase). Verify `dispatch_items.failure_reason` is populated correctly.
2. Wait for the daily 10am UTC Morris triage. Verify Mark receives a Teams DM listing the failed pile (Morris drafts the format).
3. SQL spot-check: `SELECT failure_reason, COUNT(*) FROM dispatch_items WHERE status='failed' GROUP BY failure_reason` returns a breakdown.

## 8. Dispatch Notes

- Repo: tech-dev-agents
- Branch: `story-701/dispatch-failure-classification-dlq`
- Scope: small
- Priority: 70
- Expected runtime: Phase 7 ~15 min, Phase 8 ~30 min
- Wait for STORY-700 to merge before claiming this.

## Test Criteria

See numbered Test Criteria section above.

## Validation

See numbered Validation section above.
