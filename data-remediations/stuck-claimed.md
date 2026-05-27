# Runbook: Stuck Claimed Job (dispatch_state_current)

> See [queue-stuck-claimed-runbook.md](./queue-stuck-claimed-runbook.md) for the full runbook.

This alias file satisfies the STORY-916 Acceptance Diff requirement.  
Alert UID: `dispatch-stuck-claimed` | Grafana group: `dispatch_queue_health`

**Quick reference:**

```sql
SELECT job_id, updated_at,
       EXTRACT(EPOCH FROM (NOW() - updated_at)) / 60 AS minutes_stuck
FROM dispatch_state_current
WHERE state = 'claimed'
  AND updated_at < NOW() - INTERVAL '30 minutes'
ORDER BY updated_at ASC;
```

See [queue-stuck-claimed-runbook.md](./queue-stuck-claimed-runbook.md) for full triage steps and resolution options.
