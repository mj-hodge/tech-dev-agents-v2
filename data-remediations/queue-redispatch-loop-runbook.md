# Runbook: Dispatch — Redispatch Loop (>3 in 24h)

**Alert UID:** `queue-redispatch-loop`  
**Severity:** WARN  
**Story:** STORY-916

---

## What this alert means

A `correlation_key` (story + repo identifier) has accumulated more than 3 `redispatched`
events in `dispatch_v2_events` within the last 24 hours. This indicates an agent is being
dispatched, failing, and re-dispatched without root-cause resolution.

Left unchecked, a redispatch loop burns:
- Agent tokens (each dispatch runs a full multi-phase Claude session)
- Budget quota (cost attributed to the correlation_key each cycle)
- Operator attention (repeated failures may mask a systemic issue)

Common causes:
- A flaky test that reliably fails on Phase 7 or 8 but not locally
- A broken dependency (external API, DB migration not yet applied)
- A spec conflict the agent cannot resolve without human input
- Misconfigured story (missing acceptance criteria, ambiguous scope)

---

## Quick triage

```sql
-- Identify all hot-looping correlation_keys in the last 24 hours
SELECT
    correlation_key,
    COUNT(*) AS redispatch_count,
    MIN(created_at) AS first_redispatch,
    MAX(created_at) AS last_redispatch
FROM dispatch_v2_events
WHERE event_type = 'redispatched'
  AND created_at > NOW() - INTERVAL '24 hours'
  AND correlation_key IS NOT NULL
GROUP BY correlation_key
HAVING COUNT(*) > 3
ORDER BY redispatch_count DESC;
```

```sql
-- Inspect recent events for the hot-looping key
SELECT event_type, payload, created_at
FROM dispatch_v2_events
WHERE correlation_key = '<correlation_key>'
  AND created_at > NOW() - INTERVAL '24 hours'
ORDER BY created_at DESC
LIMIT 30;
```

---

## Resolution steps

### Step 1 — Identify the failure mode

Review the last `failed` or `error` event's `payload` for the `correlation_key`.
Common patterns in `payload.reason`:

| Reason | Action |
|--------|--------|
| `phase_8_test_failure` | Review failing test; file a bug or update spec |
| `external_api_timeout` | Check API health; retry may self-heal |
| `needs_info` | Story requires human input — add clarification to Asana |
| `phase_timeout` | Agent exceeded time budget; split story or extend limit |

### Step 2 — Pause the loop

```sql
-- Move the job to 'paused' to stop re-dispatch while root cause is investigated
UPDATE dispatch_state_current
SET state = 'paused',
    updated_at = NOW()
WHERE job_id = (
    SELECT dj.job_id
    FROM dispatch_jobs dj
    WHERE dj.correlation_key = '<correlation_key>'
    ORDER BY dj.created_at DESC
    LIMIT 1
);
```

### Step 3 — Resolve root cause

- If spec is ambiguous: update the Asana task description, add acceptance criteria
- If tests are broken: fix the test or implementation per Phase 7 guidance
- If external dependency is missing: deploy it, then unpause

### Step 4 — Resume

```sql
-- Unpause and re-queue after root cause is resolved
UPDATE dispatch_state_current
SET state = 'queued',
    updated_at = NOW()
WHERE job_id = '<job_id>';
```

---

## Prevention / follow-up

- Consider adding a `max_redispatch_count` circuit-breaker in the dispatch poller that
  moves a job to `needs_info` after N redispatches instead of looping indefinitely.
- The Grafana threshold (>3 in 24h) is conservative. If two retries on transient failures
  are expected, consider raising the threshold to >5.
- Review `dispatch_v2_events` for `correlation_key` patterns to identify systemic failures.

---

## Related

- `dispatch_v2_events` table — `sql/dispatch_v2_schema.sql` (column: `correlation_key`)
- `dispatch_jobs` table — `sql/dispatch_v2_schema.sql`
- Grafana alert rule: `deployment/observability/grafana-alerts.yml` → `queue_health_slos`
