# Feature Spec — STORY-700: `dispatch_events` Append-Only Log Table

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-700 |
| Scope | Medium |
| Approach | A — Minimal-column JSONB (confirmed in analysis.md) |
| Frontend | No |
| API Contract Changes | None — no new public endpoints |

---

## 1. Components

### 1.1 Migration: `scripts/migrations/010_dispatch_events.sql`

Idempotent DDL. Creates the append-only table and three indexes.

```sql
CREATE TABLE IF NOT EXISTS dispatch_events (
  id          BIGSERIAL    PRIMARY KEY,
  story_id    VARCHAR(20)  NOT NULL,
  repo        VARCHAR(200) NOT NULL,
  event_type  VARCHAR(40)  NOT NULL,
  agent       VARCHAR(50),
  phase_num   SMALLINT,
  payload     JSONB        NOT NULL DEFAULT '{}'::jsonb,
  ts          TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CONSTRAINT valid_event_type CHECK (event_type ~ '^[a-z_]+$')
);

CREATE INDEX IF NOT EXISTS idx_de_story_repo_ts ON dispatch_events (story_id, repo, ts);
CREATE INDEX IF NOT EXISTS idx_de_event_type_ts ON dispatch_events (event_type, ts);
CREATE INDEX IF NOT EXISTS idx_de_agent_ts      ON dispatch_events (agent, ts) WHERE agent IS NOT NULL;
```

**Idempotency:** `IF NOT EXISTS` on both table and indexes. Safe to re-run on a DB that already has the table.

**No foreign key to `dispatch_items`:** Intentional. Events must survive independent of the mutable queue row (terminal-state DELETE-then-INSERT on re-enqueue would cascade-delete history). The `story_id` + `repo` pair is a logical reference, not a database constraint.

### 1.2 Service: `tech_dev_agents/ops_console/services/dispatch_events.py`

Single public function. No class — the service is stateless; it only needs a connection pool.

```python
"""Append-only dispatch event log.

If dispatch_items and dispatch_events disagree on state for any row,
dispatch_events is the source of truth — it captures every transition,
while dispatch_items mutates in place.

Design contract: emit() NEVER raises to the caller. DB failures are
logged but never block forward progress on the dispatch queue. Without
this property, an event-write outage would halt the fleet.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Module-level pool reference, set once during app lifespan startup.
_pool: asyncpg.Pool | None = None


def init(pool: asyncpg.Pool) -> None:
    """Bind the module to an asyncpg pool. Called once in app lifespan."""
    global _pool
    _pool = pool


async def emit(
    story_id: str,
    repo: str,
    event_type: str,
    *,
    agent: str | None = None,
    phase_num: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Best-effort event write. NEVER raises to caller.

    Parameters
    ----------
    story_id : str
        e.g. "STORY-700"
    repo : str
        e.g. "tech-dev-agents"
    event_type : str
        Lowercase snake_case event name. Must match CHECK constraint
        ``^[a-z_]+$``. See §2 Event Catalogue for the full list.
    agent : str | None
        Agent name (e.g. "hermes", "devon"). None for operator-initiated events.
    phase_num : int | None
        SDLC phase number (1, 4, 6, 7, 8, ...). None for queue-level events.
    payload : dict | None
        Arbitrary JSONB-safe dict. Stored as ``'{}'::jsonb`` when None.
    """
    if _pool is None:
        logger.warning("dispatch_events.emit() called before init() — event dropped: %s %s %s", story_id, repo, event_type)
        return

    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO dispatch_events
                       (story_id, repo, event_type, agent, phase_num, payload)
                   VALUES ($1, $2, $3, $4, $5, COALESCE($6::jsonb, '{}'::jsonb))""",
                story_id,
                repo,
                event_type,
                agent,
                phase_num,
                _json_dumps(payload) if payload else None,
            )
    except Exception:
        logger.exception(
            "dispatch_events.emit() failed — event dropped: %s %s %s",
            story_id, repo, event_type,
        )


def _json_dumps(d: dict[str, Any]) -> str:
    """Serialize a dict to a JSON string for asyncpg JSONB parameter."""
    import json
    return json.dumps(d, default=str)
```

**Key design decisions:**

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Module-level `_pool` vs class | Module-level | Avoids threading `DispatchEventsService` through every route handler and the phase runner. `init(pool)` once at startup; `emit()` is importable anywhere. |
| `COALESCE($6::jsonb, ...)` | Server-side default | Keeps the Python caller simple (`payload=None` is fine). |
| `_json_dumps` with `default=str` | Permissive serialization | `datetime`, `Decimal`, etc. in payload dicts are stringified rather than crashing emit(). |
| No retry on DB failure | Log + drop | Retrying on transient errors adds latency to the caller's hot path. Since `emit()` must never block forward progress, log-only is the correct trade-off. A follow-up hardening story can add bounded retry if silent drops are observed. |

### 1.3 App Lifespan Integration

In the ops-console app lifespan (where the asyncpg pool is created), add:

```python
from tech_dev_agents.ops_console.services import dispatch_events
dispatch_events.init(pool)
```

This is a single line in the existing `lifespan()` function, after the pool is created and before routers are mounted.

### 1.4 Producer Call-Sites

#### 1.4.1 `tech_dev_agents/ops_console/routes/dispatch.py` — 8 events

| Route | Event Type | Agent Source | Phase | Payload |
|-------|-----------|-------------|-------|---------|
| `POST /dispatch` (enqueue) | `enqueued` | `body.enqueued_by` | None | `{"scope": body.scope, "title": title}` |
| `POST /dispatch/claim/{id}` | `claimed` | `body.agent_name` | None | `{}` |
| `POST /dispatch/complete/{id}` | `completed` | `row["claimed_by"]` | None | `{"commit_sha": body.commit_sha, "pr_number": body.pr_number}` |
| `POST /dispatch/fail/{id}` | `failed` | `row["claimed_by"]` | None | `{"exit_code": exit_code}` |
| `DELETE /dispatch/queue/{id}` (cancel) | `cancelled` | caller from auth | None | `{"reason": reason[:200]}` |
| `POST /dispatch/release/{id}` | `released` | None | None | `{}` |
| `POST /dispatch/needs-info/{id}` | `needs_info_set` | agent_name | current_phase | `{"question_file_path": question_file_path}` |
| `POST /dispatch/resume/{id}` | `resumed` | None | None | `{}` |

**Placement:** Each `emit()` call goes AFTER the successful DB mutation (the `db_svc.xxx()` call) and BEFORE the HTTP response is returned. This ensures:
- Events are only emitted for transitions that actually happened (no orphan events on DB errors).
- Events don't block the response (emit is best-effort, never raises).

**Import:** Add `from tech_dev_agents.ops_console.services.dispatch_events import emit as emit_event` at module top.

#### 1.4.2 `deployment/hermes/sdlc_phase_runner.py` — 6 events

| Location | Event Type | Agent Source | Phase | Payload |
|----------|-----------|-------------|-------|---------|
| Before SDK subprocess start | `phase_started` | `AGENT_NAME` env | phase_num | `{"phase_name": name}` |
| After SDK subprocess exit | `phase_ended` | `AGENT_NAME` env | phase_num | `{"phase_name": name, "exit_code": rc, "duration_s": elapsed}` |
| Before SDK subprocess.Popen | `sdk_invoke_started` | `AGENT_NAME` env | phase_num | `{"workdir": workdir}` |
| After SDK subprocess exit | `sdk_invoke_ended` | `AGENT_NAME` env | phase_num | `{"exit_code": rc, "duration_s": elapsed}` |
| After deliverable verification passes | `validation_passed` | `AGENT_NAME` env | phase_num | `{"deliverable": filename}` |
| After deliverable verification fails | `validation_failed` | `AGENT_NAME` env | phase_num | `{"deliverable": filename, "reason": reason}` |

**Sync context:** The phase runner runs in a synchronous subprocess context on agent VMs, not inside the async ops-console. It cannot use the async `emit()` directly. Instead, it POSTs the event to a lightweight in-process helper that makes a synchronous HTTP call to the ops-console's internal pool — but that's overengineered for v1.

**V1 approach (simpler):** The phase runner calls a synchronous `emit_sync()` wrapper that uses `urllib.request` to POST directly to the ops-console's existing infrastructure. But this adds a network round-trip per event.

**Chosen approach:** The phase runner writes events via a **synchronous `_emit_event()` helper** that POSTs to `{OPS_CONSOLE_URL}/api/internal/dispatch-event` — a new internal-only endpoint (API-key authenticated, not exposed to agents). This is the same pattern as the existing `_report_complete()` / `_report_fail()` helpers in `dispatch_poller.py`.

New internal endpoint (ops-console side):

```python
@router.post("/internal/dispatch-event", status_code=202)
async def record_dispatch_event(request: Request):
    """Internal endpoint for phase runner event emission."""
    body = await request.json()
    await emit_event(
        story_id=body["story_id"],
        repo=body["repo"],
        event_type=body["event_type"],
        agent=body.get("agent"),
        phase_num=body.get("phase_num"),
        payload=body.get("payload"),
    )
    return {"accepted": True}
```

Phase runner side helper:

```python
def _emit_event(
    story_id: str,
    repo: str,
    event_type: str,
    *,
    agent: str | None = None,
    phase_num: int | None = None,
    payload: dict | None = None,
) -> None:
    """Best-effort sync event emission via ops-console API. Never raises."""
    base_url = os.environ.get("OPS_CONSOLE_URL", "")
    api_key = os.environ.get("OPS_CONSOLE_API_KEY", "")
    if not base_url or not api_key:
        return
    try:
        import json
        data = json.dumps({
            "story_id": story_id,
            "repo": repo,
            "event_type": event_type,
            "agent": agent,
            "phase_num": phase_num,
            "payload": payload,
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/api/internal/dispatch-event",
            data=data,
            headers={"Content-Type": "application/json", "X-API-Key": api_key},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # Best-effort — never block the phase runner
```

#### 1.4.3 `deployment/hermes/dispatch_poller.py` — 2 events

| Location | Event Type | Agent Source | Phase | Payload |
|----------|-----------|-------------|-------|---------|
| `_report_fail` retry path (after successful re-enqueue) | `retry_enqueued` | `AGENT_NAME` env | None | `{"attempt": attempt, "max_retries": MAX_RETRY_ATTEMPTS}` |
| `_report_fail` rate-limit skip | `rate_limited` | `AGENT_NAME` env | None | `{"exit_code": 429}` |

**Implementation:** Same `_emit_event` sync helper (copy the function, or import from a shared module in `/opt/agent/`). Uses `urllib.request` — the poller is synchronous Python.

### 1.5 Retention Cron

Weekly batched DELETE. Runs on Morris's cron (Sunday 04:00 UTC).

Script: `scripts/retention_dispatch_events.sh`

```bash
#!/usr/bin/env bash
# Weekly retention: delete dispatch_events older than 120 days.
# Batched 10k rows per loop to avoid long-running transactions.
set -euo pipefail

psql "${DATABASE_URL}" <<'SQL'
DO $$
DECLARE rows_deleted int;
BEGIN
  LOOP
    DELETE FROM dispatch_events
    WHERE ctid IN (
      SELECT ctid FROM dispatch_events
      WHERE ts < now() - interval '120 days'
      LIMIT 10000
    );
    GET DIAGNOSTICS rows_deleted = ROW_COUNT;
    EXIT WHEN rows_deleted = 0;
  END LOOP;
END $$;
SQL
```

**Schedule:** `0 4 * * 0` (Sunday 04:00 UTC). No urgency — table growth is bounded by dispatch volume (~50 events/day → ~18k rows/year).

---

## 2. Event Catalogue

| Event Type | Producer | Description |
|------------|----------|-------------|
| `enqueued` | routes/dispatch.py | Story added to queue |
| `claimed` | routes/dispatch.py | Agent claimed story |
| `completed` | routes/dispatch.py | Story marked complete |
| `failed` | routes/dispatch.py | Story marked failed |
| `cancelled` | routes/dispatch.py | Story cancelled (admin) |
| `released` | routes/dispatch.py | Claim released back to pending |
| `needs_info_set` | routes/dispatch.py | Agent blocked on question |
| `resumed` | routes/dispatch.py | Operator resumed from needs_info |
| `phase_started` | sdlc_phase_runner.py | SDK session starting for a phase |
| `phase_ended` | sdlc_phase_runner.py | SDK session finished for a phase |
| `sdk_invoke_started` | sdlc_phase_runner.py | Subprocess.Popen called |
| `sdk_invoke_ended` | sdlc_phase_runner.py | Subprocess exited |
| `validation_passed` | sdlc_phase_runner.py | Deliverable file verified |
| `validation_failed` | sdlc_phase_runner.py | Deliverable file missing/invalid |
| `retry_enqueued` | dispatch_poller.py | Story re-enqueued after failure |
| `rate_limited` | dispatch_poller.py | Agent hit Claude Code rate limit |

**Constraint:** All event_type values are lowercase snake_case (`^[a-z_]+$`).

---

## 3. Error Handling

### 3.1 Error Response Format

No new public API endpoints are added. The internal `/api/internal/dispatch-event` endpoint returns:

| Status | Meaning |
|--------|---------|
| 202 | Event accepted |
| 401 | Missing or invalid API key |
| 422 | Malformed body (missing story_id, repo, or event_type) |

The 422 response uses the project-standard format (consistent with existing dispatch routes):
```json
{"detail": "Missing required field: story_id"}
```

### 3.2 What Is Logged on Error

- `emit()` failures: full exception traceback via `logger.exception()`, including story_id, repo, and event_type
- `_emit_event()` (sync helper): silent drop (no logging — the phase runner's stdout goes to journalctl, and noisy event-drop warnings would pollute phase output)

### 3.3 What Is NOT Exposed to Callers

- Stack traces (never in HTTP responses)
- Database connection strings or internal paths
- The fact that event emission failed (callers see no error — that's the contract)

### 3.4 Fallback Behavior

| Dependency | Unavailable Behavior | Rationale |
|------------|---------------------|-----------|
| PostgreSQL (for INSERT) | Fail-open: log + drop event | Queue safety is non-negotiable — event loss is acceptable, queue stall is not |
| asyncpg pool not initialized | Fail-open: log warning + return | App startup race — pool might not be ready yet |
| Ops-console unreachable (from phase runner) | Fail-open: silent drop | Phase runner must never stall on event emission |

---

## 4. Implementation Patterns

### 4.1 Parameter Binding

- **asyncpg (ops-console):** Positional `$1, $2, ...` binding. JSONB parameter passed as `$6::jsonb` with Python-side `json.dumps()`.
- **psql (retention script):** No parameter binding — the DELETE uses only `now()` and interval literals, no user input.

### 4.2 Query Generation

All queries are static SQL strings with positional parameters. No f-string interpolation, no dynamic SQL construction.

### 4.3 Error Handling Pattern

- **asyncpg:** `try/except Exception` wrapping the entire `emit()` body. `logger.exception()` on failure.
- **urllib.request (phase runner):** `try/except Exception` wrapping the HTTP call. Silent pass on failure.

---

## 5. Build Order (5 commits)

| # | Commit | Files | Depends On |
|---|--------|-------|------------|
| 1 | Migration DDL | `scripts/migrations/010_dispatch_events.sql` | None |
| 2 | Service module + lifespan init | `tech_dev_agents/ops_console/services/dispatch_events.py`, app lifespan file | Commit 1 |
| 3 | Route-level emit calls + internal endpoint | `routes/dispatch.py`, new internal route | Commit 2 |
| 4 | Phase runner + poller emit calls | `deployment/hermes/sdlc_phase_runner.py`, `deployment/hermes/dispatch_poller.py` | Commit 2 |
| 5 | Retention script | `scripts/retention_dispatch_events.sh` | Commit 1 |

---

## 6. Files Changed

### New Files
- `scripts/migrations/010_dispatch_events.sql`
- `tech_dev_agents/ops_console/services/dispatch_events.py`
- `scripts/retention_dispatch_events.sh`

### Modified Files
- `tech_dev_agents/ops_console/routes/dispatch.py` — add `emit_event()` calls in 8 route handlers
- `deployment/hermes/sdlc_phase_runner.py` — add `_emit_event()` helper + 6 call sites
- `deployment/hermes/dispatch_poller.py` — add `_emit_event()` helper + 2 call sites
- App lifespan file (wherever `asyncpg.create_pool` is called) — add `dispatch_events.init(pool)`

### Files NOT Changed
- `dispatch_items` table schema (no ALTER TABLE)
- Dispatch API request/response models (no contract changes)
- Any frontend files

---

## 7. Deployment

1. Migration auto-applies via `deploy.sh` (STORY-118's auto-migrate).
2. Retention cron added to Morris's crontab post-deploy.
3. No feature flag — events are always emitted once deployed. Removing the feature is a schema DROP + code revert (no flag needed for an append-only log).

---

## 8. Validation (Post-Deploy)

```sql
-- Confirm table exists and events are flowing
SELECT event_type, COUNT(*), MIN(ts), MAX(ts)
FROM dispatch_events
GROUP BY event_type
ORDER BY COUNT(*) DESC;

-- Time-to-first-claim P50/P95 over 7 days
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY claim_lag_s) AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY claim_lag_s) AS p95
  FROM ( SELECT EXTRACT(EPOCH FROM
           MIN(ts) FILTER (WHERE event_type='claimed')
         - MIN(ts) FILTER (WHERE event_type='enqueued')) AS claim_lag_s
         FROM dispatch_events
         WHERE ts > now() - interval '7 days'
         GROUP BY story_id, repo ) x
  WHERE claim_lag_s IS NOT NULL;
```

---

## 9. Test Matrix (18 tests, 8 groups)

| Group | Test | Type | What It Verifies |
|-------|------|------|------------------|
| A: Migration | A1: applies on fresh DB | PG integration | Table + indexes created |
| A: Migration | A2: idempotent re-run | PG integration | No error on second run |
| A: Migration | A3: indexes present | PG integration | `pg_indexes` query returns all 3 |
| B: emit() | B1: all fields populated | Unit (mock pool) | Row matches input |
| B: emit() | B2: agent=None, phase_num=None | Unit (mock pool) | NULL columns accepted |
| B: emit() | B3: payload=None defaults to `{}` | Unit (mock pool) | COALESCE works |
| B: emit() | B4: payload with nested dict | Unit (mock pool) | JSONB serialization |
| C: emit() safety | C1: DB raises → no exception to caller | Unit (mock pool) | try/except catches |
| C: emit() safety | C2: pool=None → warning logged, no raise | Unit | Pre-init safety |
| D: Route events | D1: enqueue emits `enqueued` | Unit (mock emit) | Correct event_type + payload |
| D: Route events | D2: claim emits `claimed` | Unit (mock emit) | Agent name in event |
| D: Route events | D3: complete emits `completed` | Unit (mock emit) | commit_sha in payload |
| D: Route events | D4: fail emits `failed` | Unit (mock emit) | exit_code in payload |
| D: Route events | D5: cancel emits `cancelled` | Unit (mock emit) | reason in payload |
| D: Route events | D6: release emits `released` | Unit (mock emit) | Event after release |
| D: Route events | D7: needs_info emits `needs_info_set` | Unit (mock emit) | question path in payload |
| D: Route events | D8: resume emits `resumed` | Unit (mock emit) | Event after resume |
| E: Phase runner | E1: phase loop emits start+end per phase | Unit (mock subprocess) | 4 events for 2 phases |
| F: Poller | F1: retry path emits `retry_enqueued` | Unit (mock HTTP) | attempt count in payload |
| G: Indexes | G1: sample queries on seeded data | PG integration | Queries return non-empty |
| H: Retention | H1: DELETE removes only old rows | PG integration | Rows < 120d deleted, recent kept |

---

## 10. Acceptance Criteria Checklist

- [ ] `scripts/migrations/010_dispatch_events.sql` exists and is idempotent
- [ ] `tech_dev_agents/ops_console/services/dispatch_events.py` exports `init()` and `emit()`
- [ ] `emit()` never raises to callers (DB errors logged, not propagated)
- [ ] `routes/dispatch.py` has >= 8 `emit_event(...)` calls
- [ ] `sdlc_phase_runner.py` has >= 4 `_emit_event(...)` calls
- [ ] `dispatch_poller.py` has >= 1 `_emit_event(...)` call
- [ ] All 3 indexes present in `pg_indexes` after migration
- [ ] Retention script deletes rows > 120 days old in 10k-row batches
- [ ] No schema changes to `dispatch_items`
- [ ] No new public API endpoints (internal-only is fine)
- [ ] All tests pass

---

## Follow-ups (not in scope)

- Bounded retry in `emit()` for transient DB errors (hardening story)
- Read API at `/api/dispatch/events` (monitoring story)
- Frontend timeline view (UI story after alerts)
- Backfill script for historical data
- Event-driven projection to replace `dispatch_items` mutations (B path)
