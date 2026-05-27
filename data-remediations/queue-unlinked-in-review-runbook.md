# Runbook: Dispatch — Unlinked in_review Job > 60 min

**Alert UID:** `queue-unlinked-in-review`  
**Severity:** WARN  
**Story:** STORY-916

---

## What this alert means

A row in `dispatch_state_current` has `state = 'in_review'` but the corresponding
`dispatch_jobs.pr_number` is `NULL`, and this condition has persisted for more than
60 minutes. The submitted event fired, the PR was presumably created by the agent,
but the queue never recorded the `pr_number`.

Without a `pr_number`, Morris cannot:
- Auto-merge the PR
- Link review comments back to the story
- Confirm the story is in the correct terminal state

Root causes include:
- GitHub API miss when the agent submitted the PR (transient 5xx / rate-limit)
- STORY-902 backfill gap — the PR was created before `pr_number` persistence was added
- Race condition between PR creation and the `pr_number` write

---

## Quick triage

```sql
-- Identify all unlinked in_review rows with age breakdown
SELECT
    dsc.job_id,
    dsc.story_id,
    dsc.state,
    dsc.updated_at,
    dj.pr_number,
    dj.correlation_key,
    EXTRACT(EPOCH FROM (NOW() - dsc.updated_at)) / 60 AS minutes_unlinked
FROM dispatch_state_current dsc
JOIN dispatch_jobs dj ON dsc.job_id = dj.job_id
WHERE dsc.state = 'in_review'
  AND dj.pr_number IS NULL
  AND dsc.updated_at < NOW() - INTERVAL '60 minutes'
ORDER BY dsc.updated_at ASC;
```

```sql
-- Check dispatch_v2_events for a submitted event that may contain the PR number
SELECT dve.event_type, dve.payload, dve.created_at
FROM dispatch_v2_events dve
WHERE dve.job_id = '<job_id>'
ORDER BY dve.created_at DESC
LIMIT 20;
```

---

## Resolution steps

### Option A — Manual pr_number backfill

1. Find the PR number from GitHub or from `dispatch_v2_events.payload` (look for a
   `pr_created` or `submitted` event with `pr_number` in the JSON payload).
2. Backfill:

```sql
-- Backfill the missing pr_number
UPDATE dispatch_jobs
SET pr_number = <pr_number>
WHERE job_id = '<job_id>'
  AND pr_number IS NULL;
```

3. Confirm the alert clears within the next evaluation cycle (5 min).

### Option B — Re-submit via Morris

If the PR was never created at all (not just unlinked):

```bash
# Trigger Morris to re-review the story
cai asana-api.sh comment "<task_gid>" "Re-submitting for PR review — pr_number was missing from dispatch_jobs."
```

Then move the story back to `In Progress` so the dispatch poller re-claims it.

---

## Prevention / follow-up

- The `pr_number` write should be retried on failure. File a bug if the dispatch poller
  does not retry the GitHub API call.
- STORY-914 added a DB invariant guard for this — confirm it is deployed if recurrence is observed.
- See `data-remediations/queue-stuck-claimed-runbook.md` if the job is also stuck in claimed.

---

## Related

- `dispatch_jobs` table — `sql/dispatch_v2_schema.sql` (column: `pr_number`)
- STORY-902: pr_number backfill migration
- STORY-914: in_review PR link DB invariant
- Grafana alert rule: `deployment/observability/grafana-alerts.yml` → `queue_health_slos`
