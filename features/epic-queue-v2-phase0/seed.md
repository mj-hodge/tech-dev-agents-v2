# Epic-Queue-v2 Phase 0: SLO Observability + Auto-Quarantine

**Branch:** `epic-queue-v2/phase-0`  
**Base:** `feat/unified-queue-reliability`  
**Date:** 2026-05-02  
**Scope:** Stop-the-bleed — ships BEFORE the 7-day dispatch-surface freeze

## Problem

The dispatch queue has developed reliability issues that are hard to detect and slow to remediate:

1. Stories get stuck in 409-loop claim races (same story/repo hammered repeatedly)
2. Pending items age unnoticed — head-of-line blocking invisible to operators
3. Failed items silently drop their failure_reason (NULL), making triage impossible
4. No quarantine mechanism — stuck stories re-enter the queue immediately after force-release

## Deliverables

### 1. GET /api/dispatch/metrics — 4 new SLO metrics
- `claim_409_per_story_5m_max` — worst-case claim-409 storm per story in last 5 min
- `head_of_line_age_seconds` — age of oldest pending item with no active claim
- `failure_reason_null_rate` — fraction of recent failures with no categorical label
- `claim_conflict_rate_5m` — concurrent claim attempts hitting the same item

### 2. Queue SLO panel in dispatch dashboard
Status card grid showing all 4 metrics with red/green threshold indicators.

### 3. dispatch_quarantine table + auto-quarantine
- Migration 014 adds `dispatch_quarantine` table
- `/claim` maintains an in-memory 409 sliding window per (story_id, repo)
- After 6 409s in 120s: force-release + insert quarantine row + update metric
- `/next` excludes quarantined items (cleared_at IS NULL)

### 4. Quarantine management endpoints
- GET /api/dispatch/quarantine — list active quarantines
- POST /api/dispatch/quarantine/{id}/clear — operator clear

## Acceptance Criteria
- AC1: /api/dispatch/metrics returns non-null values for all 4 metrics
- AC2: dispatch_quarantine table exists after migration 014
- AC3: After 6 simulated 409s for same (story_id, repo), item is quarantined
- AC4: Quarantined item is excluded from /next
