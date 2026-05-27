# Runbook: Dispatch — Stuck Claimed Job > 30 min

**Alert UID:** `queue-stuck-claimed`  
**Severity:** PAGE  
**Story:** STORY-916

---

## What this alert means

A row in `dispatch_state_current` has `state = 'claimed'` and the `updated_at` timestamp
has not advanced in more than 30 minutes. The agent that claimed the job may be:

- Hung mid-phase (Claude API timeout, OOM, network partition)
- Crashed without releasing the lease
- Rate-limited with no retry logic surfaced to the queue

Until the job is released or fails, no other agent can pick it up. The story is silently stalled.

---

## Quick triage

```sql
-- Identify all stuck claimed rows with age breakdown
SELECT
    job_id,
    story_id,
    state,
    updated_at,
    EXTRACT(EPOCH FROM (NOW() - updated_at)) / 60 AS minutes_stuck
FROM dispatch_state_current
WHERE state = 'claimed'
  AND updated_at < NOW() - INTERVAL '30 minutes'
ORDER BY updated_at ASC;
```

```sql
-- Check if the agent is still heartbeating (agent_presence table)
SELECT ap.agent_id, ap.last_seen, ap.status
FROM agent_presence ap
JOIN dispatch_state_current dsc ON dsc.claimed_by = ap.agent_id
WHERE dsc.state = 'claimed'
  AND dsc.updated_at < NOW() - INTERVAL '30 minutes';
```

---

## Resolution steps

### Option A — Wait and watch (if agent is alive)

1. Confirm the agent is heartbeating: run the presence query above.
2. If `last_seen` is recent (< 5 min ago), the agent may be in a long-running Claude call.
   Allow up to 60 min for critical stories before escalating.
3. Check agent logs: `ssh <agent-vm> journalctl -u dispatch-poller -n 200`

### Option B — Force-release the claim

If the agent is confirmed dead or has been stuck > 60 min:

```sql
-- Release the claim so another agent can pick up the job
UPDATE dispatch_state_current
SET state = 'queued',
    claimed_by = NULL,
    updated_at = NOW()
WHERE job_id = '<job_id>'
  AND state = 'claimed';
```

Then verify the row moved back to `queued`:

```sql
SELECT job_id, state, updated_at FROM dispatch_state_current WHERE job_id = '<job_id>';
```

### Option C — Mark as failed (if story is irrecoverable)

```sql
UPDATE dispatch_state_current
SET state = 'failed',
    updated_at = NOW()
WHERE job_id = '<job_id>';
```

---

## Prevention / follow-up

- Confirm the agent VM is healthy: `./deployment/vm/push-code.sh <agent>` runs a smoke test.
- If the agent crashed without releasing, file a bug against the dispatch-poller watchdog.
- Consider increasing the Grafana evaluation interval to 5m if PAGE fatigue becomes an issue
  after confirming the 30m threshold is appropriate.

---

## Related

- `dispatch_state_current` table schema — `sql/dispatch_v2_schema.sql`
- Agent restart: `./deployment/vm/push-code.sh <agent>`
- Grafana alert rule: `deployment/observability/grafana-alerts.yml` → `queue_health_slos`
