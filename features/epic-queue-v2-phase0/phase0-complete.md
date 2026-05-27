# Epic-Queue-v2 Phase 0: Complete

**Shipped:** 2026-05-02  
**Branch:** `epic-queue-v2/phase-0`  
**PR:** feat/unified-queue-reliability ← epic-queue-v2/phase-0

## What shipped

### 1. GET /api/dispatch/metrics (new)
Returns four SLO health metrics:
- `claim_409_per_story_5m_max` — worst-case 409 storm depth per (story_id, repo) in 120s window
- `head_of_line_age_seconds` — seconds since oldest unclaimed pending item was enqueued (null when queue empty)
- `failure_reason_null_rate` — fraction of 24h failures with NULL failure_reason
- `claim_conflict_rate_5m` — concurrent AlreadyClaimedError collisions in last 5 min

### 2. Queue SLO panel (frontend)
New "SLO" tab in the Dispatch Queue panel. Four status cards with red/green threshold indicators:
- 409 max > 1 → red
- HOL age > 300s → red
- Null failure rate > 0 → red
- Conflict rate > 1 → red

### 3. dispatch_quarantine table + auto-quarantine (migration 014)
- `dispatch_quarantine` table: id, story_id, repo, quarantined_at, reason, cleared_at
- `/claim` endpoint: in-memory `_claim_409_window` sliding window (120s horizon); after >5 409s for same (story_id, repo) → force_release + insert_quarantine row
- `/next` endpoint: skips items with active quarantine (cleared_at IS NULL) via `is_quarantined()` DB check

### 4. Quarantine management endpoints
- `GET /api/dispatch/quarantine` — list active quarantines
- `POST /api/dispatch/quarantine/{id}/clear` — operator clear (TODO v2: restrict to MANAGER role)

## Acceptance criteria: all met

| AC | Result |
|----|--------|
| AC1: metrics endpoint returns 4 non-null values | PASS |
| AC2: dispatch_quarantine table in migration 014 | PASS (014_dispatch_quarantine.sql exists) |
| AC3: 6 simulated 409s → quarantine fires | PASS |
| AC4: quarantined item excluded from /next | PASS |

## Test results
13 tests, 13 passed (0 failures)

## Files changed
- `tech_dev_agents/ops_console/routes/dispatch.py` — sliding window, metrics endpoint, auto-quarantine in /claim, quarantine check in /next, quarantine management endpoints
- `tech_dev_agents/ops_console/services/dispatch_db_service.py` — 5 new methods: `head_of_line_age_seconds`, `failure_reason_null_rate`, `insert_quarantine`, `is_quarantined`, `list_quarantines`, `clear_quarantine`
- `scripts/migrations/014_dispatch_quarantine.sql` — new table + index
- `frontend/src/components/DispatchQueue.tsx` — QueueSLOTab + SLOCard components, new SLO tab
- `frontend/src/hooks/useDispatchQueue.ts` — `useDispatchMetrics` hook
- `frontend/src/types/api.ts` — `DispatchMetrics` interface
- `tests/ops_console/test_dispatch_phase0_slo.py` — 13 new tests
