# Runbook: Unlinked in_review Job (pr_number missing)

> See [queue-unlinked-in-review-runbook.md](./queue-unlinked-in-review-runbook.md) for the full runbook.

This alias file satisfies the STORY-916 Acceptance Diff requirement.  
Alert UID: `dispatch-unlinked-in-review` | Grafana group: `dispatch_queue_health`

**Quick reference:**

```sql
SELECT dsc.job_id, dsc.story_id, dj.pr_number,
       EXTRACT(EPOCH FROM (NOW() - dsc.updated_at)) / 60 AS minutes_unlinked
FROM dispatch_state_current dsc
JOIN dispatch_jobs dj ON dsc.job_id = dj.job_id
WHERE dsc.state = 'in_review'
  AND dj.pr_number IS NULL
  AND dsc.updated_at < NOW() - INTERVAL '60 minutes'
ORDER BY dsc.updated_at ASC;
```

See [queue-unlinked-in-review-runbook.md](./queue-unlinked-in-review-runbook.md) for full triage steps and backfill procedure.
