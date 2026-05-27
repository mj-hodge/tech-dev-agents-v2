# STORY-1010 Feature Spec — Ops-skill Fallback Registry + PR-link Assertion at Merge Gate

**Phase:** 6 — Design
**Date:** 2026-05-25
**Scope:** Medium

---

## 1. Problem Statement

Two gaps in the dispatch pipeline need to close simultaneously:

1. **Operator skill outages** — when a `/requeue-failed` or similar operator skill is
   unavailable, engineers have no documented fallback. Queue jams require on-call
   escalation to find the right SQL.

2. **Orphaned review rows** — agents can transition to `in_review` without stamping a
   `pr_number` on `dispatch_jobs`. Morris's review workflow depends on this link; a
   missing `pr_number` produces an unreviewable dispatch row that requires manual triage.

---

## 2. Ops-skill Fallback Registry

### 2.1 Data Model

```python
@dataclass(frozen=True)
class Fallback:
    """In-memory registry entry — NOT a DB model.  migration-ci: ignore"""

    skill_name: str       # e.g. "requeue-failed"
    sql_template: str     # PostgreSQL SQL with $param placeholders
    runbook_url: str      # URL to operator runbook
    description: str      # One-line plain-English description
    required_params: list[str]  # Placeholder names for operator substitution
```

### 2.2 Registry API

```python
REGISTRY: dict[str, Fallback]   # module-level, populated at import time

def register_fallback(skill_name, sql_template, runbook_url,
                      description, required_params=None) -> Fallback
def get_fallback(skill_name) -> Fallback | None
def render_fallback(skill_name, **params) -> str | None
```

`render_fallback()` produces a copy-pasteable block with the SQL template (params
substituted) and the runbook URL. Output is operator-facing text, not executed
server-side.

### 2.3 Initial Registry — 5 Skills

| Skill | Description | Params |
|-------|-------------|--------|
| `requeue-failed` | Requeue failed jobs for a given repo in the last 24 h | `repo` |
| `dispatch-recovery` | Release stale leases and requeue stuck jobs | none |
| `release-stale-claim` | Release a single stale lease by `job_id` + `lease_token` | `job_id`, `lease_token` |
| `dead-letter-purge` | Transition dead-lettered jobs >7 days old to `cancelled` | none |
| `force-claim` | Force-claim a specific job for a specific agent | `job_id`, `agent_name` |

### 2.4 HTTP Endpoint

```
GET /api/ops/fallback/{skill_name}
```

- **Auth:** ops console API key (any role)
- **200:** `{"skill_name": "...", "sql": "...", "runbook_url": "...", "description": "..."}`
- **404:** skill not registered

```
GET /api/ops/fallback/
```

Returns the list of all registered skill names.

---

## 3. PR-number Gate in `transition()`

### 3.1 Decision Tree

Added inside `DispatchV2Service.transition()` when `event_type == "submitted"`:

```
if event_type != "submitted":
    → pass through (no check needed)
else:
    fetch job row (pr_number, created_at) from dispatch_jobs
    if event_data.pr_number provided:
        validate int >= 1
        if job.pr_number IS NULL: inline-stamp it (UPDATE dispatch_jobs)
    if job.pr_number IS NULL (still):
        if job.created_at > OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT:
            raise InvalidEventDataError(
                "in_review requires pr_number — backfill via
                /api/dispatch/v2/{job_id}/set-pr-number"
            )
        else:
            allow (legacy dispatch — backfill sweeper handles it)
```

### 3.2 Rollout Flag

`OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT` — ISO-8601 timestamp env var.
Default: `2099-01-01T00:00:00Z` (gate effectively disabled until ops team activates).

New dispatches (`created_at > rollout`) → hard 422.
Legacy dispatches (`created_at ≤ rollout`) → allowed through; `dispatch_pr_link_backfill_sweeper` (STORY-919) backfills `pr_number` later.

---

## 4. `set-pr-number` Endpoint

### 4.1 Route

```
POST /api/dispatch/v2/{job_id}/set-pr-number
```

**Auth:** agent role or above
**Payload:** `{"pr_number": int}`

### 4.2 Semantics

| Job `pr_number` | Call value | Result |
|-----------------|-----------|--------|
| `NULL` | any ≥ 1 | Set it → `200 {"status": "ok", "pr_number": N, "idempotent": false}` |
| `N` (same) | `N` | No-op → `200 {"status": "ok", "pr_number": N, "idempotent": true}` |
| `N` (different) | `M ≠ N` | Conflict → `409` (`PrNumberConflictError`) |
| not found | any | `404` (`JobNotFoundError`) |

### 4.3 Service Method

```python
async def set_pr_number(self, *, job_id: str, pr_number: int) -> dict[str, Any]
```

Raises `JobNotFoundError` (→ 404) or `PrNumberConflictError` (→ 409).

### 4.4 Race-safety Invariant (G04)

The SELECT inside `set_pr_number()` **must use `FOR UPDATE`** to acquire a row-level
lock before reading `pr_number`. Without it, two concurrent callers under `READ
COMMITTED` isolation can both observe `pr_number IS NULL` in their snapshot, both
bypass the conflict check, and both proceed to write — a classic TOCTOU race.

```sql
-- REQUIRED: serialises concurrent callers at the row level
SELECT job_id, pr_number
  FROM dispatch_jobs
 WHERE job_id = $1::uuid
   FOR UPDATE
```

`FOR UPDATE` blocks the second transaction at the SELECT until the first commits.
The second caller then reads the committed `pr_number` and correctly returns
`{"idempotent": true}` or raises `PrNumberConflictError`.

Test **G04** (`test_set_pr_number_uses_for_update`) enforces this invariant by
asserting that `FOR UPDATE` appears in the SQL string passed to `conn.fetchrow`.

---

## 5. New Files

| File | Purpose |
|------|---------|
| `tech_dev_agents/ops_console/ops_skill_fallback_registry.py` | `Fallback` dataclass + `REGISTRY` + 5 initial registrations |
| `tech_dev_agents/ops_console/routes/ops_fallback.py` | `GET /api/ops/fallback/{skill_name}` + list endpoint |
| `tests/ops_console/test_ops_fallback_registry.py` | Registry + render tests (Groups A–D) |
| `tests/ops_console/test_dispatch_v2_pr_number_gate.py` | Gate + set-pr-number tests (Groups E–G) |
| `tests/epic_1000/test_incident_replays.py` | REPLAY-3 + STORY-919 class replays (Groups H–I) |
| `features/story-1010-queue-fallback-prlink/test-design.md` | SDLC Phase 7 deliverable |

## 6. Modified Files

| File | Change |
|------|--------|
| `dispatch_v2_service.py` | Rollout gate in `transition()` + `set_pr_number()` with `FOR UPDATE` + `PrNumberConflictError` |
| `routes/dispatch_v2.py` | `POST /{job_id}/set-pr-number` handler |
| `main.py` | Mount `ops_fallback.router` at `/api/ops` |
| `services/self_healing.py` | `pr_link_backfill: legacy_dispatch` log line on every successful backfill |
