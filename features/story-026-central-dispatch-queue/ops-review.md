# Ops Review: Central Dispatch Queue

**Phase:** 6d -- Ops Review
**Story:** STORY-026
**Date:** 2026-04-08
**Reviewer:** Ops Review Agent
**Scope:** Medium
**Verdict:** APPROVED WITH CONDITIONS

---

## Review Scope

This review evaluates the operational readiness of the Central Dispatch Queue feature: a JSON file-backed FIFO queue (`/opt/ops-console/dispatch-queue.json`) exposed via FastAPI endpoints, with a background task for stale claim recovery (60s interval) and agent-side polling (60s interval). The system serves a 2-agent fleet (Dan, Derrick) dispatched by Mark.

---

## Findings

| ID | Severity | Finding | Recommendation | Status |
|----|----------|---------|----------------|--------|
| OPS-1 | **Medium** | **No health indicator for the dispatch queue service.** The existing `/health` endpoint does not report whether `dispatch-queue.json` is readable/writable or whether the stale recovery background task is still running. If the JSON file becomes inaccessible (permissions, disk full) or the background task silently dies, there is no signal. | Add a `dispatch_queue` field to the `/health` response that checks: (a) `dispatch-queue.json` is readable, (b) the stale recovery task is alive (store a `last_recovery_run` timestamp on the service, flag unhealthy if >120s stale). This is a lightweight addition -- read the file, check the timestamp. | OPEN |
| OPS-2 | **Medium** | **Insufficient structured logging for queue lifecycle events.** The feature spec logs stale claim recovery via `logger.warning` and `logger.info`, but does not specify structured log lines for enqueue, claim, cancel, or poll events. Without these, debugging queue flow requires reading raw FastAPI access logs rather than grepping tagged events. | Add structured log lines with a `[DISPATCH]` prefix for key events: `[DISPATCH] enqueue story_id=STORY-XXX by=mark`, `[DISPATCH] claim story_id=STORY-XXX agent=derrick`, `[DISPATCH] cancel story_id=STORY-XXX`, `[DISPATCH] stale_recovery story_id=STORY-XXX was_claimed_by=dan age_s=310`, `[DISPATCH] poll agent=derrick result=claimed/empty`. These integrate with existing Loki label queries. | OPEN |
| OPS-3 | **Medium** | **No backup or corruption recovery strategy for dispatch-queue.json.** If the JSON file is corrupted (partial write despite atomic rename, disk error), `load()` returns an empty queue and logs an error. All pending and claimed stories are silently lost. There is no backup copy and no way to recover the queue state. | Before each `save()`, copy the current file to `dispatch-queue.json.bak` (a single rotating backup). This costs one extra file copy per write -- negligible at this load. On corruption, the operator can inspect the `.bak` file and manually restore. Document this in the runbook. | OPEN |
| OPS-4 | **Low** | **Background task restart behavior on rolling deploy is clean.** The `_stale_claim_recovery_loop` is created as an `asyncio.create_task` inside the lifespan context manager. On shutdown, the task is cancelled and awaited. On restart, a fresh task is created. The 60s sleep means at most one recovery cycle is skipped during restart. No data loss risk. | No change needed. The lifespan pattern handles this correctly. | ACCEPTED |
| OPS-5 | **Low** | **Queue depth and claim latency have no metrics or alerting.** There is no mechanism to alert Mark if the queue grows beyond a threshold (e.g., >5 pending stories sitting unclaimed) or if stale recovery fires repeatedly (indicating agents are failing to complete claims). At 2-agent scale this is observable via the dashboard, but unattended operation needs alerts. | Add two alert conditions to the ops console alerting system: (a) `dispatch_queue_depth_high` -- if pending count > 5 for more than 10 minutes, alert Mark. (b) `dispatch_stale_recovery_frequent` -- if stale recovery fires > 3 times in 30 minutes, alert Mark. These can be implemented as simple threshold checks in the existing alerting loop or as Loki alert rules on the `[DISPATCH]` log lines. Not a launch blocker -- track as follow-up. | ACCEPTED |
| OPS-6 | **Low** | **File lock contention is not a concern at current scale.** With 2 agents polling every 60s and Mark enqueuing occasionally, the fcntl advisory lock will never be contested for more than milliseconds. The lock blocks (no timeout), which is correct -- at this load, no request will wait meaningfully. | No change needed. If agent count exceeds 10, revisit locking strategy. | ACCEPTED |
| OPS-7 | **Info** | **Log volume impact is minimal.** The dispatch queue adds at most a few log lines per minute (agent polls, occasional enqueue/claim). The stale recovery task logs only when it actually recovers items. No measurable increase in log volume or Loki storage cost. | No action needed. | ACCEPTED |

---

## 1. Health Checks

The `/health` endpoint should include a `dispatch_queue` status field. Recommended implementation:

```python
# In the health endpoint handler
dispatch_ok = False
try:
    svc = request.app.state.dispatch_service
    queue = svc.load()
    dispatch_ok = isinstance(queue, dict) and "pending" in queue
except Exception:
    pass

# Include in response
"dispatch_queue": {
    "status": "ok" if dispatch_ok else "degraded",
    "pending_count": len(queue.get("pending", [])),
    "claimed_count": len(queue.get("claimed", [])),
}
```

This gives Mark a single-glance check that the queue subsystem is functional without requiring a separate monitoring endpoint.

---

## 2. Logging

All queue lifecycle events should emit structured log lines with the `[DISPATCH]` tag for Loki filtering:

| Event | Log Line | Level |
|-------|----------|-------|
| Story enqueued | `[DISPATCH] enqueue story_id=STORY-XXX repo=advertising-amazon scope=medium by=mark` | INFO |
| Story claimed | `[DISPATCH] claim story_id=STORY-XXX agent=derrick` | INFO |
| Story cancelled | `[DISPATCH] cancel story_id=STORY-XXX` | INFO |
| Stale claim recovered | `[DISPATCH] stale_recovery story_id=STORY-XXX was_claimed_by=dan age_s=310` | WARNING |
| Agent poll (empty) | `[DISPATCH] poll agent=derrick result=empty` | DEBUG |
| Agent poll (found work) | `[DISPATCH] poll agent=derrick result=found story_id=STORY-XXX` | INFO |
| Queue file error | `[DISPATCH] error action=load msg="Failed to parse JSON"` | ERROR |

DEBUG-level poll logs should be suppressed in production by default (set dispatch logger to INFO). They can be enabled temporarily for troubleshooting.

---

## 3. Monitoring and Metrics

At current scale (2 agents), the dashboard queue view provides sufficient visibility. For unattended operation, add these lightweight metrics:

| Metric | Source | Alert Threshold |
|--------|--------|----------------|
| Queue depth (pending count) | `/health` response or `[DISPATCH]` log lines | > 5 pending for > 10 minutes |
| Stale recovery count | `[DISPATCH] stale_recovery` log lines | > 3 recoveries in 30 minutes |
| Queue file age | `stat dispatch-queue.json` mtime | > 1 hour with no updates while agents are online |
| Claim latency (time from enqueue to claim) | Compute from `enqueued_at` vs `claimed_at` in claimed items | No alert needed -- informational |

These can be implemented as Loki alert rules or as threshold checks in the existing ops console alerting loop. Not required for launch but recommended as a follow-up task.

---

## 4. Backup Strategy

**Current state:** No backup. Corruption causes `load()` to return an empty queue, silently losing all pending and claimed stories.

**Recommended approach:**

1. **Single rotating backup:** Before each `save()`, copy the current file to `dispatch-queue.json.bak`. This is one `shutil.copy2` call -- negligible overhead.
2. **Manual recovery:** If the primary file is corrupted, the operator copies `.bak` over the primary and restarts. At most one operation is lost (the one that triggered the corruption).
3. **No automated recovery:** At this scale, automated failover is over-engineering. The queue is ephemeral (stories can be re-dispatched) and Mark has dashboard visibility.

The `load()` method already handles corruption gracefully (returns empty queue + logs error). The backup file provides a recovery path when the operator notices the error.

---

## 5. Deployment Safety

**Rolling restart behavior:**

1. FastAPI shutdown triggers lifespan exit -- the stale recovery task is cancelled cleanly.
2. Any in-flight API request completes before shutdown (standard uvicorn graceful shutdown).
3. The JSON file on disk is unaffected by the restart -- atomic writes via `os.replace` ensure no partial state.
4. On startup, the new process reads the existing queue file and resumes normal operation.
5. Agents polling during the restart window (~2 seconds) receive connection errors and retry on their next 60s cycle. No data loss.

**Deploy order:** No special ordering required. Deploy backend changes, restart FastAPI. The background task starts automatically on boot. Agent-side polling changes (if any) can deploy independently.

**Rollback:** Remove the dispatch router from `main.py` and restart. The queue file remains on disk but is inert. Re-adding the router restores the queue without data loss.

---

## 6. Alerting

| Alert | Condition | Action | Priority |
|-------|-----------|--------|----------|
| `dispatch_queue_depth_high` | Pending count > 5 for > 10 minutes | Notify Mark via Teams | Medium |
| `dispatch_stale_recovery_frequent` | > 3 stale recoveries in 30 minutes | Notify Mark via Teams -- indicates agents are claiming but failing to start work | High |
| `dispatch_queue_file_error` | `[DISPATCH] error` log line appears | Notify Mark via Teams -- queue file may be corrupted or inaccessible | High |
| `dispatch_queue_idle` | Pending items > 0 but no claims in > 30 minutes while agents are online | Notify Mark -- agents may not be polling | Medium |

At launch, only the `dispatch_queue_file_error` alert is critical. The others are recommended as follow-up once the system is in steady-state operation.

---

## 7. Runbook Entries

### Inspect the dispatch queue

```bash
# View the full queue
cat /opt/ops-console/dispatch-queue.json | jq .

# Count pending stories
cat /opt/ops-console/dispatch-queue.json | jq '.pending | length'

# Count claimed stories
cat /opt/ops-console/dispatch-queue.json | jq '.claimed | length'

# List pending story IDs
cat /opt/ops-console/dispatch-queue.json | jq -r '.pending[].story_id'

# Check who claimed what
cat /opt/ops-console/dispatch-queue.json | jq '.claimed[] | {story_id, claimed_by, claimed_at}'
```

### Re-queue a story manually

If a story needs to be moved from claimed back to pending (e.g., agent crashed):

```bash
# Edit the queue file directly (stop FastAPI first to avoid conflicts)
sudo systemctl stop ops-console

# Use jq to move a story from claimed to pending
STORY="STORY-094"
QUEUE="/opt/ops-console/dispatch-queue.json"
jq --arg sid "$STORY" '
  .claimed as $c |
  ($c | map(select(.story_id == $sid))) as $found |
  if ($found | length) > 0 then
    .pending += [$found[0] | del(.claimed_by, .claimed_at)] |
    .claimed = ($c | map(select(.story_id != $sid)))
  else
    .
  end
' "$QUEUE" > /tmp/queue-fixed.json && mv /tmp/queue-fixed.json "$QUEUE"

sudo systemctl start ops-console
```

Alternatively, use the API (no restart needed):

```bash
# Cancel and re-enqueue via API (only works for pending items)
# For claimed items, manual file editing is required
curl -X DELETE "http://localhost:8000/api/dispatch/queue/STORY-094" \
  -H "X-API-Key: $OPS_API_KEY"
```

### Recover from queue file corruption

```bash
# Check if backup exists
ls -la /opt/ops-console/dispatch-queue.json.bak

# Validate the backup
cat /opt/ops-console/dispatch-queue.json.bak | jq . > /dev/null && echo "Valid JSON" || echo "Also corrupted"

# Restore from backup
sudo systemctl stop ops-console
cp /opt/ops-console/dispatch-queue.json.bak /opt/ops-console/dispatch-queue.json
sudo systemctl start ops-console

# If no backup or backup also corrupted, reset to empty queue
echo '{"pending": [], "claimed": [], "last_updated": ""}' > /opt/ops-console/dispatch-queue.json
sudo systemctl start ops-console
```

### Check stale recovery task health

```bash
# Search Loki for recent stale recovery events
# (via Grafana or direct Loki query)
# Label: {job="ops-console"} |= "[DISPATCH] stale_recovery"

# Check FastAPI logs for recovery loop errors
journalctl -u ops-console --since "1 hour ago" | grep "DISPATCH"
```

### Verify agent polling

```bash
# Check if agents are reaching the dispatch endpoint
journalctl -u ops-console --since "5 minutes ago" | grep "dispatch/next"

# Or check Loki for poll log lines
# {agent="derrick"} |= "[DISPATCH] poll"
```

---

## Summary of Risk Areas

### What could break silently

1. **Queue file becomes unreadable** -- `load()` returns empty queue, all pending stories invisible, agents see no work (OPS-1, OPS-3)
2. **Stale recovery background task dies** -- claimed stories never return to pending, queue appears stuck (OPS-1)
3. **Agents stop polling** -- pending stories accumulate with no claims, only visible if Mark checks the dashboard (OPS-5)

### What is well-designed

- Atomic writes via `os.replace` prevent partial JSON on disk
- Advisory file locking prevents concurrent mutation from overlapping requests
- `load()` degrades gracefully on corruption (returns empty queue, logs error)
- Lifespan context manager ensures clean background task shutdown on restart
- 5-minute stale claim timeout prevents indefinite claim hoarding
- 409 Conflict on double-claim prevents race conditions between agents
- Queue size cap (50 pending) prevents unbounded growth

---

## Deployment Checklist

- [ ] Ensure `/opt/ops-console/` directory exists with correct ownership (`ops-console` user, `0755`)
- [ ] Deploy backend Python changes (new routes, service, models)
- [ ] Restart FastAPI service: `sudo systemctl restart ops-console`
- [ ] Verify `/health` returns successfully (or includes `dispatch_queue` field if OPS-1 is addressed)
- [ ] Verify queue file was created: `ls -la /opt/ops-console/dispatch-queue.json`
- [ ] Test enqueue via API: `curl -X POST http://localhost:8000/api/dispatch -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{"story_id":"STORY-TEST","repo":"test","prompt":"test"}'`
- [ ] Verify queue list: `curl http://localhost:8000/api/dispatch/queue -H "X-API-Key: $KEY"`
- [ ] Clean up test item: `curl -X DELETE http://localhost:8000/api/dispatch/queue/STORY-TEST -H "X-API-Key: $KEY"`
- [ ] Deploy frontend changes (DispatchQueue component on Fleet page)
- [ ] Deploy agent-side polling changes to both agent VMs
- [ ] Verify agent polling appears in logs within 60 seconds of agent idle
- [ ] Add `[DISPATCH]` structured logging (OPS-2) before or shortly after launch

---

## Verdict

**APPROVED WITH CONDITIONS**

The design is operationally sound for the current 2-agent fleet. The JSON file-backed queue with atomic writes, advisory locking, and stale claim recovery is appropriate for this scale. Deployment is straightforward with no downtime risk.

**Conditions (address before or shortly after launch):**

1. **OPS-1 (Medium):** Add a `dispatch_queue` health indicator to the `/health` endpoint so that queue file issues and background task failures are detectable without manual inspection.
2. **OPS-2 (Medium):** Add `[DISPATCH]`-tagged structured log lines for all queue lifecycle events (enqueue, claim, cancel, stale recovery). Without these, operational debugging requires parsing raw access logs.
3. **OPS-3 (Medium):** Add a single rotating backup (`dispatch-queue.json.bak`) before each save. This provides a recovery path for the most likely failure mode (file corruption) at negligible cost.

None of these conditions block deployment. The system degrades gracefully on failure (empty queue, logged errors), and Mark has dashboard visibility into queue state. The conditions improve detectability and recoverability for unattended operation.
