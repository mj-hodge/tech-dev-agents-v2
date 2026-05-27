# Feature Spec: Resume-Aware Phase Runner + Dispatch Observability

> Phase 6 -- STORY-507
> Date: 2026-04-21
> Scope: Large

---

## Table of Contents

1. [Technical Design Overview](#technical-design-overview)
2. [API Changes](#api-changes)
3. [Database Changes](#database-changes)
4. [File-by-File Change Plan](#file-by-file-change-plan)
5. [Observability Design](#observability-design)
6. [Rollback Strategy](#rollback-strategy)
7. [Acceptance Criteria Mapping](#acceptance-criteria-mapping)

---

## Technical Design Overview

This story addresses five structural defects in the dispatch system by modifying the phase runner, poller, backend API, database schema, and frontend. The design follows a layered approach:

1. **Data layer** -- New `paused` status, `paused_at` column, updated constraints
2. **Service layer** -- `pause()` method, modified `next_pending()`, modified `recover_stale_claims()`
3. **API layer** -- `POST /dispatch/pause/{story_id}` endpoint, modified status transitions
4. **Agent layer** -- Branch resume in `_ensure_branch()`, SIGTERM handler, per-file commits, structured logging
5. **Frontend layer** -- Paused tab in DispatchQueue, phase-progress column
6. **Observability layer** -- Structured log events, Grafana alert rules

### Design Principles

- **Additive changes only** -- No breaking changes to existing API contracts; new status is handled gracefully by older code
- **Fail-safe defaults** -- If branch resume fails, fall back to fresh branch; if structured logging fails, fall back to print
- **Minimal blast radius** -- Each change is independently deployable and testable
- **VM deployment awareness** -- All agent-side changes require `push-code.sh` restart

---

## API Changes

### New Endpoint: `POST /dispatch/pause/{story_id}`

**Purpose:** Allows the phase runner to mark a dispatch item as paused when interrupted.

```
POST /api/dispatch/pause/{story_id}
Content-Type: application/json

Request Body:
{
  "agent": "daisy",           // required: claiming agent name
  "current_phase": 6,         // optional: last completed or in-progress phase
  "reason": "sigterm"         // optional: "sigterm" | "rate_limit" | "session_cap" | "question"
}

Response 200:
{
  "story_id": "STORY-507",
  "status": "paused",
  "paused_at": "2026-04-21T14:30:00Z",
  "current_phase": 6
}

Response 404: Story not found
Response 409: Story not in claimable state (completed/cancelled)
```

**Transition rules:** Accepts from `claimed` status only. A `pending` item cannot be paused (it was never started). A `failed` item must be re-enqueued, not paused.

### Modified Endpoint: `GET /dispatch/next`

**Current:** Returns oldest `pending` item.
**New:** Returns oldest item where `status IN ('pending', 'paused')`, ordered by `enqueued_at`.

This means paused items are automatically re-dispatched in FIFO order alongside pending items, with no priority difference. The agent that picks up a paused item receives the full dispatch payload and uses branch resume to avoid re-running completed phases.

### Modified Endpoint: `POST /dispatch/claim/{story_id}`

**Current:** Accepts transition from `pending` only.
**New:** Accepts transition from `pending` OR `paused`. When claiming a paused item, `paused_at` is preserved (not cleared) for audit trail. `claimed_at` is updated to the new claim time.

### Modified Endpoint: `POST /dispatch/complete/{story_id}`

**Current:** Accepts from `claimed` or `pending`.
**New:** Also accepts from `paused` (edge case: manual completion of a paused item via force-complete).

### Modified Endpoint: `POST /dispatch/fail/{story_id}`

**Current:** Accepts from `claimed` or `pending`.
**New:** Also accepts from `paused`.

### Modified Endpoint: `GET /dispatch/queue`

**Current:** Returns items where `status IN ('pending', 'claimed')`.
**New:** Also includes `paused` items. The `paused` items appear in the response alongside pending and claimed items.

---

## Database Changes

### Migration: `004_paused_status.sql`

```sql
-- STORY-507: Add paused status and phase-tracking columns
-- Additive migration: safe to run on active systems
-- Run during low-activity window (recommended)

-- 1. Add paused_at timestamp column
ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ;

-- 2. Add phase-tracking columns for observability
ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS current_phase INTEGER;
ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS phase_started_at TIMESTAMPTZ;

-- 3. Update CHECK constraint to include 'paused'
-- Drop existing constraint, add new one with 'paused' included
ALTER TABLE dispatch_items DROP CONSTRAINT IF EXISTS dispatch_items_status_check;
ALTER TABLE dispatch_items ADD CONSTRAINT dispatch_items_status_check
    CHECK (status IN ('pending', 'claimed', 'completed', 'cancelled', 'failed', 'paused'));

-- 4. Update partial unique index to include 'paused'
-- A paused story should block duplicate enqueues just like pending/claimed
DROP INDEX IF EXISTS uq_story_active_idx;
CREATE UNIQUE INDEX uq_story_active_idx
    ON dispatch_items (story_id)
    WHERE status IN ('pending', 'claimed', 'paused');

-- 5. Index for efficient paused item queries
CREATE INDEX IF NOT EXISTS idx_dispatch_paused_at ON dispatch_items (paused_at)
    WHERE status = 'paused';
```

**Rollback SQL:**
```sql
-- Reverse 004_paused_status.sql
-- WARNING: Must first update any 'paused' rows to 'failed' before running
UPDATE dispatch_items SET status = 'failed' WHERE status = 'paused';

ALTER TABLE dispatch_items DROP CONSTRAINT IF EXISTS dispatch_items_status_check;
ALTER TABLE dispatch_items ADD CONSTRAINT dispatch_items_status_check
    CHECK (status IN ('pending', 'claimed', 'completed', 'cancelled', 'failed'));

DROP INDEX IF EXISTS uq_story_active_idx;
CREATE UNIQUE INDEX uq_story_active_idx
    ON dispatch_items (story_id)
    WHERE status IN ('pending', 'claimed');

DROP INDEX IF EXISTS idx_dispatch_paused_at;
ALTER TABLE dispatch_items DROP COLUMN IF EXISTS phase_started_at;
ALTER TABLE dispatch_items DROP COLUMN IF EXISTS current_phase;
ALTER TABLE dispatch_items DROP COLUMN IF EXISTS paused_at;
```

### Contaminated Row Migration: `005_cleanup_contaminated_completed.sql`

```sql
-- STORY-507 AC-13: Re-label contaminated completed rows to paused
-- DRY RUN: Comment out the UPDATE, run the SELECT to get row count
-- CONFIRM with Mark before executing UPDATE on prod

-- Dry-run: show affected rows
SELECT id, story_id, status, completed_at, commit_sha, pr_number
FROM dispatch_items
WHERE status = 'completed'
  AND (commit_sha IS NULL OR commit_sha = '');

-- Execute: re-label to failed (not paused -- these are historical, not resumable)
-- UPDATE dispatch_items
-- SET status = 'failed', updated_at = now()
-- WHERE status = 'completed'
--   AND (commit_sha IS NULL OR commit_sha = '');
```

---

## File-by-File Change Plan

### 1. `deployment/hermes/sdlc_phase_runner.py` (Heavy)

#### 1a. Branch Resume -- Modify `_ensure_branch()` (lines 394-445)

**Before (current, lines 432-442):**
```python
# Create or checkout the story branch
result = subprocess.run(
    ["git", "-C", workdir, "checkout", "-b", target_branch],
    capture_output=True, text=True, timeout=10,
)
if result.returncode != 0:
    # Branch might already exist
    subprocess.run(
        ["git", "-C", workdir, "checkout", target_branch],
        capture_output=True, timeout=10,
    )
```

**After:**
```python
# Check if branch exists on origin (prior dispatch attempt)
ls_remote = subprocess.run(
    ["git", "-C", workdir, "ls-remote", "--heads", "origin", f"story-{num}/*"],
    capture_output=True, text=True, timeout=15,
)
remote_branch = None
if ls_remote.returncode == 0 and ls_remote.stdout.strip():
    # Parse the first matching ref
    for line in ls_remote.stdout.strip().split("\n"):
        ref = line.split("\t")[-1].replace("refs/heads/", "")
        if f"story-{num}/" in ref:
            remote_branch = ref
            break

if remote_branch:
    # Resume: fetch and checkout the existing remote branch
    _emit_event("branch_resume", story_id=story_id, details={"remote_branch": remote_branch})
    subprocess.run(
        ["git", "-C", workdir, "fetch", "origin", remote_branch],
        capture_output=True, timeout=30,
    )
    result = subprocess.run(
        ["git", "-C", workdir, "checkout", "-b", remote_branch, f"origin/{remote_branch}"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        # Branch already exists locally -- just checkout and pull
        subprocess.run(
            ["git", "-C", workdir, "checkout", remote_branch],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["git", "-C", workdir, "pull", "--ff-only", "origin", remote_branch],
            capture_output=True, timeout=30,
        )
    print(f"[DISPATCH] Resumed existing branch: {remote_branch}", flush=True)
else:
    # Greenfield: create new branch from main
    result = subprocess.run(
        ["git", "-C", workdir, "checkout", "-b", target_branch],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        subprocess.run(
            ["git", "-C", workdir, "checkout", target_branch],
            capture_output=True, timeout=10,
        )
    print(f"[DISPATCH] Created new branch: {target_branch}", flush=True)
```

**Lines changed:** ~35 modified, ~15 added

#### 1b. SIGTERM Handler -- New function + registration

**New code (add after `_save_partial_work`, ~line 228):**
```python
import signal
import threading

_shutdown_requested = threading.Event()
_current_sdk_pid: int | None = None
_current_story_id: str | None = None
_current_workdir: str | None = None
_current_phase_num: int | None = None
_current_phase_name: str | None = None

# API base URL for pause endpoint
_DISPATCH_API_BASE = os.environ.get("DISPATCH_API_BASE", "http://ops-console.internal:8000/api")

def _graceful_shutdown(signum, frame):
    """SIGTERM handler: commit, push, mark paused, exit 143."""
    print(f"[DISPATCH] SIGTERM received — initiating graceful shutdown", flush=True)
    _emit_event("sigterm_received", story_id=_current_story_id or "unknown",
                details={"phase": _current_phase_num})
    _shutdown_requested.set()

    # 1. Send SIGTERM to child SDK process
    if _current_sdk_pid:
        try:
            os.kill(_current_sdk_pid, signal.SIGTERM)
            # Wait up to 10 seconds for graceful exit
            for i in range(10):
                try:
                    os.kill(_current_sdk_pid, 0)  # Check if alive
                    time.sleep(1)
                except ProcessLookupError:
                    break  # Process exited
        except (ProcessLookupError, PermissionError):
            pass  # Already exited

    # 2. Commit and push any uncommitted work
    if _current_workdir and _current_story_id:
        _save_partial_work(
            _current_workdir, _current_story_id,
            _current_phase_num or "?", _current_phase_name or "interrupted",
        )

    # 3. Call POST /dispatch/pause/{story_id}
    if _current_story_id:
        try:
            agent_name = os.environ.get("AGENT_NAME", "unknown")
            import urllib.request
            import json as json_mod
            req = urllib.request.Request(
                f"{_DISPATCH_API_BASE}/dispatch/pause/{_current_story_id}",
                data=json_mod.dumps({
                    "agent": agent_name,
                    "current_phase": _current_phase_num,
                    "reason": "sigterm",
                }).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            print(f"[DISPATCH] Marked {_current_story_id} as paused", flush=True)
        except Exception as exc:
            print(f"[DISPATCH] Warning: could not mark paused: {exc}", flush=True)

    # 4. Exit with 143 (128 + SIGTERM=15)
    sys.exit(143)
```

**Registration (add at start of `run_sdlc_phases`, ~line 460):**
```python
signal.signal(signal.SIGTERM, _graceful_shutdown)
```

**Lines added:** ~55

#### 1c. Per-File Commit Helper -- New function

```python
PHASE8_COMMIT_CADENCE = os.environ.get("PHASE8_COMMIT_CADENCE", "per_file")

def _commit_file(workdir: str, filepath: str, story_id: str) -> bool:
    """Stage and commit a single file. Returns True if committed."""
    try:
        subprocess.run(
            ["git", "-C", workdir, "add", filepath],
            capture_output=True, timeout=10,
        )
        result = subprocess.run(
            ["git", "-C", workdir, "commit", "-m",
             f"phase-8({story_id}): {os.path.basename(filepath)}"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False
```

**Phase 8 prompt modification:** The Phase 8 prompt template gains an additional instruction block:

```
COMMIT CADENCE: After writing each file, run: git add <file> && git commit -m "phase-8({story_id}): <filename>"
This ensures no work is lost if the session is interrupted. Do NOT batch commits.
```

**Lines added:** ~25

#### 1d. Structured Log Events -- New helper

```python
import json
import time

def _emit_event(event_type: str, story_id: str = "", **kwargs):
    """Emit a structured JSON log event for Loki ingestion."""
    payload = {
        "event": event_type,
        "story_id": story_id,
        "agent": os.environ.get("AGENT_NAME", "unknown"),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    payload.update(kwargs.get("details", {}))
    if "phase" in kwargs:
        payload["phase"] = kwargs["phase"]
    if "duration_s" in kwargs:
        payload["duration_s"] = kwargs["duration_s"]
    if "status" in kwargs:
        payload["status"] = kwargs["status"]
    print(json.dumps(payload), flush=True)
```

**Integration points in phase loop (lines 509-626):**
- Before SDK call: `_emit_event("phase_start", story_id=story_id, phase=phase_num)`
- After SDK call: `_emit_event("phase_end", story_id=story_id, phase=phase_num, duration_s=elapsed, status="success"|"failure"|"rate_limited")`
- On skip: `_emit_event("phase_skip", story_id=story_id, phase=phase_num, details={"reason": "deliverable_exists"})`

**Lines added:** ~25

#### 1e. Update `_run_phase_sdk` to Track PID

Add to `_run_phase_sdk` (after subprocess.Popen or SDK call):
```python
global _current_sdk_pid
_current_sdk_pid = proc.pid  # Store for SIGTERM handler
```

And update the global tracking variables in the phase loop:
```python
global _current_story_id, _current_workdir, _current_phase_num, _current_phase_name
_current_story_id = story_id
_current_workdir = workdir
_current_phase_num = phase_num
_current_phase_name = phase_name
```

**Lines modified:** ~10

#### 1f. Check `_shutdown_requested` Between Phases

In the phase loop (after `_save_partial_work` call at phase end):
```python
if _shutdown_requested.is_set():
    print(f"[DISPATCH] Shutdown requested — exiting phase loop", flush=True)
    return False, None
```

**Lines added:** ~3

---

### 2. `deployment/hermes/dispatch_poller.py` (Moderate)

#### 2a. Pre-Claim Budget Check Enhancement (lines 873-922)

**Add 15-minute threshold check after `ccusage blocks --json` parsing:**
```python
# Parse remaining time in current 5h block
if block_data and "resets_at" in block_data:
    remaining_seconds = block_data["resets_at"] - time.time()
    if remaining_seconds < 900:  # 15 minutes = 900 seconds
        print(f"[DISPATCH] Rate-limit budget low: {remaining_seconds:.0f}s remaining in block, skipping poll", flush=True)
        return "busy"
```

**Lines modified:** ~20

#### 2b. Structured Logging

Replace key `print(f"[DISPATCH]...")` calls with structured JSON:
```python
def _log_event(event: str, **kwargs):
    import json
    payload = {"event": event, "agent": AGENT_NAME, "timestamp": time.strftime(...)}
    payload.update(kwargs)
    print(json.dumps(payload), flush=True)
```

Key replacement points:
- `poll_once()` claim success: `_log_event("dispatch_claimed", story_id=...)`
- `_report_complete()`: `_log_event("dispatch_completed", story_id=...)`
- `_report_fail()`: `_log_event("dispatch_failed", story_id=..., reason=...)`
- Rate-limit skip: `_log_event("dispatch_rate_limited", remaining_s=...)`

**Lines modified:** ~30

---

### 3. `tech_dev_agents/ops_console/services/dispatch_db_service.py` (Moderate)

#### 3a. New `pause()` Method (add after `fail()`, ~line 381)

```python
async def pause(self, story_id: str, agent_name: str,
                current_phase: int | None = None) -> dict[str, Any]:
    """Transition a claimed item to paused.

    Only accepts claimed -> paused. Stores the phase in progress
    for observability.
    """
    async with self._pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE dispatch_items
               SET status = 'paused',
                   paused_at = now(),
                   current_phase = COALESCE($3, current_phase),
                   updated_at = now()
               WHERE story_id = $1 AND status = 'claimed' AND claimed_by = $2
               RETURNING *""",
            story_id, agent_name, current_phase,
        )
        if row is not None:
            return _row_to_dict(row)

        # Check current state for error message
        exists = await conn.fetchval(
            "SELECT status FROM dispatch_items WHERE story_id = $1 "
            "AND status IN ('pending', 'claimed', 'paused')",
            story_id,
        )
        if exists is None:
            raise NotFoundError(f"{story_id} not found in active queue")
        raise ValueError(f"{story_id} is {exists}, cannot pause (must be claimed)")
```

**Lines added:** ~25

#### 3b. Modify `next_pending()` (lines 156-167)

```python
async def next_pending(self) -> dict[str, Any] | None:
    """Return the oldest pending or paused item, or None if empty."""
    async with self._pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM dispatch_items
               WHERE status IN ('pending', 'paused')
               ORDER BY enqueued_at
               LIMIT 1"""
        )
    if row is None:
        return None
    return _row_to_dict(row)
```

**Lines modified:** ~3

#### 3c. Modify `claim()` (lines 171-201)

Update WHERE clause to accept `paused` as a valid source status:
```python
row = await conn.fetchrow(
    """UPDATE dispatch_items
       SET status = 'claimed',
           claimed_by = $1,
           claimed_at = now(),
           updated_at = now()
       WHERE story_id = $2 AND status IN ('pending', 'paused')
       RETURNING *""",
    agent_name, story_id,
)
```

And update the exists check:
```python
exists = await conn.fetchval(
    "SELECT status FROM dispatch_items WHERE story_id = $1 "
    "AND status IN ('pending', 'claimed', 'paused')",
    story_id,
)
```

**Lines modified:** ~5

#### 3d. Modify `complete()` and `fail()`

Update `complete()` WHERE clause to also accept `paused`:
```python
WHERE story_id = $1 AND status IN ('pending', 'claimed', 'paused')
```

Update `fail()` similarly:
```python
WHERE story_id = $1 AND status IN ('claimed', 'pending', 'paused')
```

**Lines modified:** ~4

#### 3e. Modify `recover_stale_claims()` (lines 427-447)

**Critical: Exclude paused items from stale-claim recovery.** Paused items are intentionally in that state; they should not be auto-recovered back to pending.

```python
async def recover_stale_claims(self, timeout_seconds: int = 300) -> int:
    """Move stale claimed (NOT paused) items back to pending."""
    async with self._pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE dispatch_items
               SET status = 'pending',
                   claimed_by = NULL,
                   claimed_at = NULL,
                   updated_at = now()
               WHERE status = 'claimed'
                 AND claimed_at < now() - ($1 || ' seconds')::interval""",
            str(timeout_seconds),
        )
    count = int(result.split()[-1])
    return count
```

No change needed -- the current query already filters on `status = 'claimed'` only. But add a comment clarifying this is intentional:

```python
# NOTE: Only recovers 'claimed' items, NOT 'paused' items.
# Paused items are intentionally in that state and should not be auto-recovered.
```

**Lines modified:** ~2 (comment only)

#### 3f. Modify `list_queue()` (lines 136-152)

Include `paused` in the active items query:
```python
WHERE status IN ('pending', 'claimed', 'paused')
```

**Lines modified:** ~1

---

### 4. `tech_dev_agents/ops_console/routes/dispatch.py` (Moderate)

#### 4a. New Endpoint: `POST /dispatch/pause/{story_id}` (add after `fail_story`, ~line 589)

```python
@router.post("/dispatch/pause/{story_id}")
async def pause_story(
    story_id: str,
    body: dict = Body(...),
    dispatch_db: DispatchDBService = Depends(get_dispatch_db),
):
    """Mark a dispatch item as paused (partial work exists, resume later)."""
    agent = body.get("agent", "unknown")
    current_phase = body.get("current_phase")
    reason = body.get("reason", "unknown")

    try:
        item = await dispatch_db.pause(story_id, agent, current_phase)
    except NotFoundError:
        raise HTTPException(404, f"{story_id} not found in active queue")
    except ValueError as e:
        raise HTTPException(409, str(e))

    logger.info(
        "Dispatch paused: %s by %s (phase=%s, reason=%s)",
        story_id, agent, current_phase, reason,
    )

    # Fire-and-forget presence push (agent is now available)
    asyncio.ensure_future(_fire_presence(dispatch_db, agent, "Available"))

    return item
```

**Lines added:** ~25

#### 4b. Modify `next_story()` (lines 207-239)

No change needed -- it calls `dispatch_db.next_pending()` which already returns paused items after change 3b.

#### 4c. Modify `claim_story()` (lines 242-294)

No change needed at the route level -- the DB service `claim()` method handles the `paused` -> `claimed` transition after change 3c.

#### 4d. Modify `list_queue()` (lines 163-204)

No change needed at the route level -- the DB service `list_queue()` handles the inclusion after change 3f.

#### 4e. Modify `complete_story()` status handling

The SDLC deliverable validation check (lines 414-473) should also accept `paused` items. The DB service change (3d) handles this.

---

### 5. `tech_dev_agents/ops_console/models/responses.py` (Minor)

#### 5a. Add `PAUSED` to `DispatchStatusEnum` (line 293)

```python
class DispatchStatusEnum(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    PAUSED = "paused"
```

**Lines added:** 1

#### 5b. Add Optional Fields to `DispatchItem` Model (after line 322)

```python
class DispatchItem(BaseModel):
    # ... existing fields ...
    paused_at: str | None = None
    current_phase: int | None = None
    phase_started_at: str | None = None
```

**Lines added:** 3

---

### 6. `frontend/src/components/DispatchQueue.tsx` (Moderate)

#### 6a. Add `paused` to `statusBadge()` (after line 53)

```typescript
case "paused":
  return { className: "bg-orange-100 text-orange-800", label: "paused" };
```

**Lines added:** 2

#### 6b. Add Phase Progress Column to `ItemRow`

Add a column showing `current_phase` if present:
```typescript
<td className="px-4 py-2 text-sm text-gray-500">
  {item.current_phase ? `Phase ${item.current_phase}` : "—"}
</td>
```

**Lines modified:** ~5 per row component

#### 6c. Add "Paused" Tab

Add a third tab between Queue and History. The Paused tab shows items with `status === "paused"`, filtered from the queue data (which now includes paused items).

```typescript
const pausedItems = queueData.filter((item: any) => item.status === "paused");
```

Tab UI:
```typescript
<button
  onClick={() => setActiveTab("paused")}
  className={activeTab === "paused" ? activeClass : inactiveClass}
>
  Paused {pausedItems.length > 0 && `(${pausedItems.length})`}
</button>
```

**Lines added:** ~40 (tab button, tab content panel, PausedRow component)

---

### 7. `scripts/migrations/004_paused_status.sql` (New)

Full SQL shown in [Database Changes](#database-changes) section above. 8 lines.

### 8. `scripts/migrations/005_cleanup_contaminated_completed.sql` (New)

Full SQL shown in [Database Changes](#database-changes) section above. ~15 lines.

### 9. `deployment/observability/grafana-alerts.yml` (New)

See [Observability Design](#observability-design) section below. ~60 lines.

### 10. `tests/test_507_lifecycle.py` (New)

See [Acceptance Criteria Mapping](#acceptance-criteria-mapping) section below. ~200-300 lines.

---

## Observability Design

### Structured Log Events (AC-10)

All events are JSON objects written to stdout, picked up by Promtail and forwarded to Loki.

**Event schema:**
```json
{
  "event": "phase_start|phase_end|phase_skip|sigterm_received|branch_resume|dispatch_claimed|dispatch_completed|dispatch_failed|dispatch_rate_limited",
  "story_id": "STORY-507",
  "phase": 4,
  "agent": "daisy",
  "timestamp": "2026-04-21T14:30:00Z",
  "duration_s": 120,
  "status": "success|failure|rate_limited|skipped",
  "reason": "deliverable_exists|sigterm|rate_limit"
}
```

### Metrics via Loki Recording Rules (AC-8)

Since Prometheus is not deployed on the ops-console VM, metrics are derived from structured logs using Loki recording rules. This avoids new infrastructure while providing metric semantics.

**Recording rules (conceptual, defined in Grafana):**

| Metric | LogQL Source |
|--------|-------------|
| `dispatch_phase_duration_seconds` | `avg_over_time({event="phase_end"} \| json \| unwrap duration_s [5m]) by (story_id, phase, agent)` |
| `dispatch_cycle_total` | `count_over_time({event=~"dispatch_completed\|dispatch_failed\|dispatch_paused"} [1h]) by (event)` |
| `dispatch_phase_skip_total` | `count_over_time({event="phase_skip"} [1h]) by (reason)` |
| `dispatch_sigterm_total` | `count_over_time({event="sigterm_received"} [1h])` |

### Grafana Alert Rules (AC-9)

**File:** `deployment/observability/grafana-alerts.yml`

```yaml
apiVersion: 1
groups:
  - orgId: 1
    name: dispatch-alerts
    folder: Dispatch
    interval: 5m
    rules:
      - uid: phase-duration-warning
        title: "Phase duration > 30 minutes"
        condition: C
        data:
          - refId: A
            datasourceUid: loki
            model:
              expr: |
                max_over_time(
                  {job="dispatch"} | json | event="phase_end" | unwrap duration_s [5m]
                ) by (story_id, phase, agent)
          - refId: C
            datasourceUid: __expr__
            model:
              type: threshold
              conditions:
                - evaluator: { type: gt, params: [1800] }
        for: 0s
        labels:
          severity: warning

      - uid: phase-duration-critical
        title: "Phase duration > 60 minutes"
        condition: C
        data:
          - refId: A
            datasourceUid: loki
            model:
              expr: |
                max_over_time(
                  {job="dispatch"} | json | event="phase_end" | unwrap duration_s [5m]
                ) by (story_id, phase, agent)
          - refId: C
            datasourceUid: __expr__
            model:
              type: threshold
              conditions:
                - evaluator: { type: gt, params: [3600] }
        for: 0s
        labels:
          severity: critical

      - uid: dispatch-failure-rate
        title: "Dispatch failure rate > 20% over 1 hour"
        condition: C
        data:
          - refId: A
            datasourceUid: loki
            model:
              expr: |
                sum(count_over_time({job="dispatch"} | json | event="dispatch_failed" [1h]))
                /
                sum(count_over_time({job="dispatch"} | json | event=~"dispatch_completed|dispatch_failed" [1h]))
          - refId: C
            datasourceUid: __expr__
            model:
              type: threshold
              conditions:
                - evaluator: { type: gt, params: [0.2] }
        for: 0s
        labels:
          severity: warning

      - uid: partial-pr-rate
        title: "Partial PR detected (Morris flagged incomplete)"
        # This alert fires when a PR is created but later closed/request-changed
        # Detection via structured log event from Morris review flow
        condition: C
        data:
          - refId: A
            datasourceUid: loki
            model:
              expr: |
                count_over_time({job="dispatch"} | json | event="partial_pr_detected" [24h])
          - refId: C
            datasourceUid: __expr__
            model:
              type: threshold
              conditions:
                - evaluator: { type: gt, params: [0] }
        for: 0s
        labels:
          severity: warning
```

### Alert Routing

**Decision deferred to Mark (Decision Point #1 from seed).** The alert rules define thresholds; the notification channel configuration (Teams webhook, email, etc.) is done in Grafana UI and is out of scope for this story's code changes.

---

## Rollback Strategy

### Phase 1: Database Rollback
If the migration causes issues:
1. Run `005_cleanup_contaminated_completed.sql` in reverse (no-op if never executed)
2. Run `004_paused_status.sql` rollback SQL (update paused -> failed, drop constraint, recreate, drop columns)
3. Verify: `SELECT DISTINCT status FROM dispatch_items` shows only 5 original values

### Phase 2: Backend Rollback
1. Revert `models/responses.py` -- remove `PAUSED` enum value and optional fields
2. Revert `dispatch_db_service.py` -- remove `pause()` method, revert `next_pending()` and `claim()` WHERE clauses
3. Revert `routes/dispatch.py` -- remove `/dispatch/pause` endpoint
4. Deploy ops-console: `sudo systemctl restart ops-console`

### Phase 3: Agent VM Rollback
1. Revert `sdlc_phase_runner.py` -- remove branch resume, SIGTERM handler, structured logging, per-file commits
2. Revert `dispatch_poller.py` -- remove budget threshold, structured logging
3. Deploy to all VMs: `./deployment/vm/push-code.sh all`

### Phase 4: Frontend Rollback
1. Revert `DispatchQueue.tsx` -- remove Paused tab, phase progress column, paused status badge
2. Rebuild and deploy frontend

### Rollback Order
Database first (to avoid constraint violations), then backend, then agents, then frontend. Each phase can be rolled back independently -- the system degrades gracefully:
- Without `paused` status: SIGTERM handler falls back to `_report_fail()` instead of `pause()`
- Without branch resume: fresh branches are created (existing behavior)
- Without structured logs: `[DISPATCH]` prefix logging still works
- Without Paused tab: paused items appear in Queue tab with "paused" badge

---

## Acceptance Criteria Mapping

| AC | Verified By | Test Location |
|----|-------------|---------------|
| AC-1 | Integration test: create branch with seed.md on origin, re-dispatch, confirm Phase 1 skipped | `tests/test_507_lifecycle.py::test_branch_resume_skips_existing_phases` |
| AC-2 | Integration test: pre-existing analysis.md on branch, confirm Phase 4 skipped | `tests/test_507_lifecycle.py::test_deliverable_skip_on_resume` |
| AC-3 | Integration test: git log after Phase 8 shows multiple commits | `tests/test_507_lifecycle.py::test_phase8_per_file_commits` |
| AC-4 | Integration test: send SIGTERM during Phase 8, confirm commit + push + paused status | `tests/test_507_lifecycle.py::test_sigterm_graceful_shutdown` |
| AC-5 | Unit test: `DispatchStatusEnum.PAUSED.value == "paused"`; migration adds column + constraint | `tests/test_507_lifecycle.py::test_paused_enum_exists` |
| AC-6 | Integration test: enqueue, claim, pause, verify next_pending returns paused item, re-claim | `tests/test_507_lifecycle.py::test_resume_lifecycle` |
| AC-7 | Unit test: mock ccusage with <15 min remaining, verify poll_once returns "busy" | `tests/test_507_lifecycle.py::test_rate_limit_budget_check` |
| AC-8 | Integration test: structured log events appear in stdout after phase run | `tests/test_507_lifecycle.py::test_structured_log_events` |
| AC-9 | File validation: `grafana-alerts.yml` contains 4 alert rules with correct thresholds | `tests/test_507_lifecycle.py::test_grafana_alerts_config` |
| AC-10 | Integration test: LogQL query parses structured events | `tests/test_507_lifecycle.py::test_structured_event_format` |
| AC-11 | Meta: all tests in `test_507_lifecycle.py` pass GREEN | CI pipeline |
| AC-12 | Manual staging smoke test (documented procedure) | Manual |
| AC-13 | Dry-run on staging; confirm count with Mark | `scripts/migrations/005_cleanup_contaminated_completed.sql` |

### Test Outline: `tests/test_507_lifecycle.py`

```python
"""STORY-507: Resume-Aware Phase Runner + Dispatch Observability

Integration tests covering:
  (a) Full greenfield lifecycle (pending -> claimed -> completed)
  (b) Resume lifecycle (claimed -> paused -> re-claimed -> completed)
  (c) SIGTERM mid-phase with commit verification
  (d) Deliverable-skip on re-dispatch
  (e) Rate-limit pre-check blocks claim
"""
import pytest

class TestGreenfield:
    """(a) Full lifecycle: pending -> claimed -> completed"""
    async def test_enqueue_claim_complete(self, dispatch_db):
        ...

class TestResumeLifecycle:
    """(b) Resume: claimed -> paused -> re-claimed -> completed"""
    async def test_pause_and_resume(self, dispatch_db):
        ...
    async def test_paused_appears_in_next_pending(self, dispatch_db):
        ...
    async def test_paused_blocks_duplicate_enqueue(self, dispatch_db):
        ...

class TestSIGTERM:
    """(c) SIGTERM handling"""
    def test_sigterm_commits_and_pushes(self, tmp_git_repo):
        ...
    def test_sigterm_marks_paused(self, mock_api):
        ...
    def test_sigterm_exit_code_143(self):
        ...

class TestDeliverableSkip:
    """(d) Skip on re-dispatch"""
    def test_branch_resume_fetches_origin(self, tmp_git_repo):
        ...
    def test_existing_deliverable_skips_phase(self, tmp_git_repo):
        ...

class TestRateLimitBudget:
    """(e) Pre-claim budget check"""
    def test_low_budget_skips_poll(self, mock_ccusage):
        ...
    def test_sufficient_budget_allows_claim(self, mock_ccusage):
        ...

class TestStructuredLogging:
    """Structured event format validation"""
    def test_phase_start_event_format(self):
        ...
    def test_phase_end_event_format(self):
        ...
    def test_phase_skip_event_format(self):
        ...

class TestPausedStatus:
    """Paused enum and model validation"""
    def test_paused_enum_value(self):
        ...
    def test_dispatch_item_has_paused_fields(self):
        ...
    def test_recover_stale_claims_excludes_paused(self, dispatch_db):
        ...
```

---

## Implementation Order

1. **Migration first:** `004_paused_status.sql` (unblocks all other changes)
2. **Models:** Add `PAUSED` to enum, add fields to `DispatchItem`
3. **DB service:** `pause()` method, modified `next_pending()`, `claim()`, `complete()`, `fail()`
4. **Routes:** `POST /dispatch/pause`, verify other endpoints work with paused status
5. **Phase runner:** Branch resume, SIGTERM handler, per-file commits, structured logging
6. **Poller:** Budget threshold, structured logging
7. **Frontend:** Paused tab, phase progress column, status badge
8. **Observability:** Grafana alerts YAML
9. **Tests:** All test classes
10. **Contaminated row cleanup:** `005_cleanup_contaminated_completed.sql` (run manually post-deploy)

This order ensures each layer builds on the one below it, and tests can be written against the already-deployed backend.
