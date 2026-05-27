# Runbook: Redispatch Loop (correlation_key > 3 in 24h)

> See [queue-redispatch-loop-runbook.md](./queue-redispatch-loop-runbook.md) for the full runbook.

This alias file satisfies the STORY-916 Acceptance Diff requirement.  
Alert UID: `dispatch-redispatch-loop` | Grafana group: `dispatch_queue_health`

**Quick reference:**

```sql
SELECT correlation_key, COUNT(*) AS redispatch_count,
       MIN(created_at) AS first_event, MAX(created_at) AS last_event
FROM dispatch_v2_events
WHERE event_type = 'redispatched'
  AND created_at > NOW() - INTERVAL '24 hours'
  AND correlation_key IS NOT NULL
GROUP BY correlation_key
HAVING COUNT(*) > 3
ORDER BY redispatch_count DESC;
```

See [queue-redispatch-loop-runbook.md](./queue-redispatch-loop-runbook.md) for full triage steps and loop-breaking procedure.
