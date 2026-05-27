# Seed: STORY-702 — Claim heartbeat + auto-release

## Overview

| Field | Value |
|-------|-------|
| Mode | feature_add |
| Scope | small |
| Frontend | false |
| Feature Name | Heartbeat-driven stale-claim detection; auto-release after 15 min of silence |
| Phase Path | 1 (Seed) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | main |
| Status | Seed written 2026-04-25 |
| Priority | 70 — closes the ghost-claim class permanently |
| Depends on | STORY-700 (events table) — must merge first |

---

## 1. Idea / Trigger

Tonight's STORY-575 partial-loop and last week's STORY-228/229/230/443/446/496 ghost-claim incidents share a root cause: a story marked `claimed` with no signal that the agent is still working. If the agent crashes, gets killed, or its SDK process dies, the row sits `claimed` indefinitely. Today's reconcile cron (every 5 min) is best-effort and inconsistent.

The fix: a **heartbeat** column updated every 5 min while a phase is active. A cron checks for stale heartbeats (no update in 15 min) and auto-releases the claim back to `pending`. After 3 stale-releases on the same story, the row transitions to `failed` (with `failure_reason='agent_died'` from STORY-701).

## 2. Problem Statement

- Ghost claims block work. The story's slot is held; the queue depth misrepresents real availability.
- Reconcile cron uses heuristics (PID liveness, journal recency) that miss real failures (e.g., agent VM is up but SDK crashed silently).
- Without a heartbeat, "is this story stuck?" requires manual investigation across journalctl, work_queue.json, and the dispatch_items row.
- Tonight's STORY-575 went needs_info-pending-claimed-needs_info in a loop because nothing was tracking actual progress between transitions. A heartbeat would have shown 0 progress for 15 min and force-released.

## 3. Scope Classification

**Small.** Two columns + two emitters + one reconcile-cron tightening + tests.

## 4. Codebase Context

### Schema migration: `scripts/migrations/012_claim_heartbeat.sql`

```sql
ALTER TABLE dispatch_items
    ADD COLUMN IF NOT EXISTS claim_heartbeat_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS stale_release_count  INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN dispatch_items.claim_heartbeat_at IS
    'Last heartbeat from the claiming agent. Updated every ~5min by phase '
    'runner background thread + dispatch poller while status=claimed. NULL '
    'when status != claimed.';

COMMENT ON COLUMN dispatch_items.stale_release_count IS
    'Count of stale-release events on this row. After 3, the row transitions '
    'to failed with failure_reason=agent_died (STORY-701).';

CREATE INDEX IF NOT EXISTS idx_di_stale_check ON dispatch_items (claim_heartbeat_at)
    WHERE status = 'claimed';
```

### Heartbeat emitters (Q11: BOTH for redundancy)

**Phase runner thread** (`deployment/hermes/sdlc_phase_runner.py`)
- A daemon thread spawned at the top of `run_sdlc_phases`. Every 5 min, POST `/api/dispatch/heartbeat/{story_id}?repo=<repo>`. Daemon dies when the run completes.
- Has direct SDK process context, knows the phase number.

**Poller side** (`deployment/hermes/dispatch_poller.py`)
- Existing poll loop already runs every 60s. Add: when an agent's local state shows it's working a story (`is_agent_idle()` returns False AND a current claim exists), POST a heartbeat too.
- Catches the case where the phase runner thread dies but the poller is still alive.

Both call the same endpoint. Idempotent — duplicate calls just refresh the timestamp.

### Heartbeat endpoint: `POST /api/dispatch/heartbeat/{story_id}`

```python
@router.post("/dispatch/heartbeat/{story_id}", response_model=HeartbeatResponse)
async def heartbeat(story_id: str, request: Request, repo: str | None = Query(None)):
    """Update claim_heartbeat_at to now() if the row is claimed.

    Idempotent. Returns 200 even if status changed since last heartbeat.
    """
    db_svc = _get_db_svc(request)
    updated = await db_svc.touch_heartbeat(story_id, repo)
    # Also emit a dispatch_events row (STORY-700)
    await dispatch_events.emit(
        story_id, repo, "heartbeat",
        agent=updated.get("claimed_by"),
        phase_num=updated.get("current_phase"),
    )
    return HeartbeatResponse(story_id=story_id, heartbeat_at=updated["claim_heartbeat_at"])
```

### Reconcile cron tightening

Existing reconcile cron at `_reconcile_stale_queue` runs every 5 min. Tighten its stale check:

```python
STALE_THRESHOLD_S = 900  # 15 min — Q9 confirmed
MAX_STALE_RELEASES_BEFORE_FAIL = 3  # Q10 confirmed

# For each row WHERE status='claimed' AND claim_heartbeat_at < now() - 15min:
if row.stale_release_count >= 3:
    # Transition to failed; STORY-701's classifier sets failure_reason='agent_died'
    update status='failed', failure_reason='agent_died'
else:
    # Release back to pending; bump the counter
    update status='pending', claim_heartbeat_at=NULL,
           claimed_by=NULL, claimed_at=NULL,
           stale_release_count=stale_release_count + 1
```

Emits `dispatch_events.emit(... "stale_release" or "stale_failed" ...)`.

### Files NOT to touch

- The /claim and /fail routes themselves — they already manage status transitions correctly.
- The work_queue.json on agent VMs — orthogonal local state.

## 5. Out of Scope

- Heartbeat from MCP servers or other non-poller producers.
- Per-phase heartbeats with phase-specific data (the heartbeat is just "agent still alive" — granular phase progress is dispatch_events territory).
- Adaptive heartbeat intervals based on agent load.
- Frontend visualization of heartbeat lag.

## Test Criteria

Phase 7 produces `test-design.md` and RED tests. Coverage:

1. **Migration applies idempotently** — `012_claim_heartbeat.sql` on fresh + pre-populated DB.
2. **`/dispatch/heartbeat/{id}` updates claim_heartbeat_at** — call the route; verify column updates and a `heartbeat` event row.
3. **Heartbeat is idempotent** — call 5 times in a row; no error, last call's ts is current.
4. **Phase runner thread emits heartbeats every 5 min** — mock time + DB; run a 12-min fake phase; assert ≥2 heartbeat POSTs.
5. **Poller emits heartbeat when local state shows active claim** — mock the poller's claim-detection; assert heartbeat POST.
6. **Reconcile cron auto-releases stale claim** — fixture: row with claim_heartbeat_at = now() - 16min, stale_release_count = 0. Run reconcile. Assert row is now pending, counter = 1.
7. **Reconcile cron transitions to failed after 3 releases** — fixture: same as above but counter = 3. Run reconcile. Assert row is now failed, failure_reason = 'agent_died'.
8. **Healthy claim is not released** — fixture: claim_heartbeat_at = now() - 5min. Run reconcile. Row unchanged.
9. **Heartbeat events are emitted** — verify `dispatch_events` rows with `event_type='heartbeat'`, `event_type='stale_release'`, `event_type='stale_failed'`.

## Validation

After Phase 8 lands + push-code.sh deploys:

1. Dispatch a small story; observe `claim_heartbeat_at` updating every 5 min in the DB while the phase runs.
2. Force-kill the SDK process on an agent mid-phase; wait 16 min; verify the row auto-released to `pending` and `stale_release_count = 1`.
3. Repeat the kill 3 times on the same story; verify the row transitions to `failed` with `failure_reason='agent_died'`.
4. STORY-575-style ghost-claim becomes structurally impossible.

## 8. Dispatch Notes

- Repo: tech-dev-agents
- Branch: `story-702/dispatch-claim-heartbeat`
- Scope: small
- Priority: 70
- Expected runtime: Phase 7 ~15 min, Phase 8 ~30–40 min
- Wait for STORY-700 to merge before claiming. Can run in parallel with STORY-701.

## Test Criteria

See numbered Test Criteria section above.

## Validation

See numbered Validation section above.
