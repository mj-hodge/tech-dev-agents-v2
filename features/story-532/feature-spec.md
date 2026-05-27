# STORY-532 Phase 6: Feature Specification

**Feature:** Dispatch `needs_info` state — human gate for ambiguous stories
**Scope:** Medium
**Author:** Phase 6 design
**Date:** 2026-04-22
**Supersedes:** the indiscriminate `[RETRY N/3]` handling of QUESTION.md exits

---

## 1. Overview

Introduce a new terminal-until-resumed DB state `needs_info` on
`dispatch_items`. When a phase agent writes `features/<story>/QUESTION.md`, the
phase runner transitions the story to `needs_info` instead of returning into
the auto-retry loop. `needs_info` rows are **invisible** to the agent-facing
poller (`/api/dispatch/next`) but appear in the operator dashboard as a
distinct bucket. A human resumes the story by appending their answer to
`QUESTION.md` on the story branch and POSTing `/api/dispatch/resume/{story_id}`.

This mirrors the `paused` state pattern from STORY-507 with one structural
inversion: `next_pending()` **excludes** `needs_info` (vs. paused, which is
included for auto-resume).

---

## 2. Component Map

```
                    ┌──────────────────────────────┐
                    │    sdlc_phase_runner.py      │
                    │  (deployment/hermes)         │
                    │                              │
                    │  Phase N agent exits         │
                    │      │                       │
                    │      ▼                       │
                    │  _check_for_questions()      │
                    │      │ returns text          │
                    │      ▼                       │
                    │  POST /api/dispatch/         │
                    │       needs-info/{story_id}  │
                    │   body: {question_file_path} │
                    │      │                       │
                    │      ▼ (on POST failure)     │
                    │  _notify_teams + return False│
                    └──────────┬───────────────────┘
                               │
                               ▼
      ┌────────────────────────────────────────────────┐
      │    ops_console/routes/dispatch.py              │
      │                                                │
      │  POST /api/dispatch/needs-info/{story_id}      │
      │  POST /api/dispatch/resume/{story_id}          │
      │                                                │
      │  GET  /api/dispatch/next   ◄── excludes        │
      │                              needs_info rows   │
      │  GET  /api/dispatch/queue  ◄── returns         │
      │                              needs_info bucket │
      └────────────────────────┬───────────────────────┘
                               │
                               ▼
      ┌────────────────────────────────────────────────┐
      │  ops_console/services/dispatch_db_service.py   │
      │                                                │
      │  needs_info(story_id, question_file_path)      │
      │  resume_from_needs_info(story_id)              │
      │  next_pending()   — excludes needs_info        │
      │  list_queue()     — separate needs_info bucket │
      └────────────────────────┬───────────────────────┘
                               │
                               ▼
                  ┌──────────────────────────┐
                  │   PostgreSQL             │
                  │   dispatch_items         │
                  │                          │
                  │   + status='needs_info'  │
                  │   + needs_info_path TEXT │
                  │   (migration 007)        │
                  └──────────────────────────┘

      ┌────────────────────────────────────────────────┐
      │  frontend/src/components/DispatchQueue.tsx     │
      │                                                │
      │  statusBadge('needs_info') → violet            │
      │  render needs_info group (similar to paused)   │
      └────────────────────────────────────────────────┘
```

---

## 3. Database Schema

### 3.1 Migration: `scripts/migrations/007_needs_info_state.sql`

Idempotent, additive-only, patterned after `004_paused_status.sql`.

```sql
-- STORY-532: Add 'needs_info' status to dispatch_items.
-- Stories with unanswered QUESTION.md are invisible to the poller
-- until a human explicitly resumes them.
--
-- Idempotent: safe to run multiple times.

BEGIN;

-- 1. Add column storing the relative path to features/<story>/QUESTION.md
ALTER TABLE dispatch_items
    ADD COLUMN IF NOT EXISTS needs_info_path TEXT;

-- 2. Update CHECK constraint to allow 'needs_info'
ALTER TABLE dispatch_items DROP CONSTRAINT IF EXISTS dispatch_items_status_check;
ALTER TABLE dispatch_items
    ADD CONSTRAINT dispatch_items_status_check
    CHECK (status IN (
        'pending', 'claimed', 'in_review',
        'completed', 'cancelled', 'failed',
        'paused', 'needs_info'
    ));

-- 3. Recreate unique active-index to include 'needs_info'
--    Prevents re-enqueueing a story while it is blocked on human input.
DROP INDEX IF EXISTS uq_story_active_idx;
CREATE UNIQUE INDEX uq_story_active_idx
    ON dispatch_items (story_id)
    WHERE status IN ('pending', 'claimed', 'in_review', 'paused', 'needs_info');

COMMIT;
```

**Notes:**
- `in_review` is included in the constraint and index because it already exists
  in production (STORY-496) — the current 004 migration did not add it, but
  later migrations or app-level logic accept it. We write it explicitly here
  because 007 recreates the constraint and index in full.
- **Deploy ordering:** migrate first, deploy code second. The migration is
  additive; no deployed code writes `'needs_info'` until the code is rolled out.

### 3.2 Final column set for `dispatch_items` (relevant subset)

| Column | Type | STORY-532 change |
|--------|------|------------------|
| `status` | TEXT CHECK | + `'needs_info'` in allowed set |
| `needs_info_path` | TEXT NULL | **NEW** — `features/<story>/QUESTION.md` |
| `paused_at` | TIMESTAMPTZ | unchanged (STORY-507) |
| `current_phase` | INTEGER | unchanged (STORY-507) |
| `needs_info_at` | — | **deliberately omitted** — `enqueued_at` already stamps the row; we can infer transition time from the phase-runner log line if needed. Adding another timestamp column is not justified for MVP. |

---

## 4. Service Layer

**File:** `tech_dev_agents/ops_console/services/dispatch_db_service.py`

### 4.1 New method: `needs_info(story_id, question_file_path)`

```python
async def needs_info(
    self,
    story_id: str,
    question_file_path: str,
) -> dict[str, Any]:
    """Transition a claimed story to needs_info.

    STORY-532: Only claimed items can be marked needs_info (the agent must
    have been the one to write QUESTION.md). Preserves claimed_by for audit.

    Args:
        story_id: dispatch_items.story_id (e.g. "STORY-518")
        question_file_path: relative path, e.g. "features/story-518/QUESTION.md"

    Returns:
        updated row dict

    Raises:
        NotFoundError: story_id not in table
        InvalidTransitionError: story is not in 'claimed' state
    """
    async with self._pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE dispatch_items
               SET status = 'needs_info',
                   needs_info_path = $2
             WHERE story_id = $1 AND status = 'claimed'
            RETURNING *
            """,
            story_id, question_file_path,
        )
        if row is not None:
            return dict(row)
        # Diagnose: does the row exist at all?
        existing = await conn.fetchrow(
            "SELECT status FROM dispatch_items WHERE story_id = $1",
            story_id,
        )
        if existing is None:
            raise NotFoundError(f"{story_id} not found")
        raise InvalidTransitionError(
            f"Cannot mark {story_id} needs_info from status={existing['status']} — "
            f"only claimed items can be gated on info."
        )
```

### 4.2 New method: `resume_from_needs_info(story_id)`

```python
async def resume_from_needs_info(self, story_id: str) -> dict[str, Any]:
    """Transition a needs_info story back to pending.

    STORY-532: Resets claimed_by so the next poller call treats it as a
    fresh claim. Clears needs_info_path — the next agent reads the current
    file contents from the branch. Resets retry-related fields (none today,
    but scaffolds future columns).

    Raises NotFoundError if story_id is not currently needs_info.
    """
    async with self._pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE dispatch_items
               SET status = 'pending',
                   needs_info_path = NULL,
                   claimed_by = NULL,
                   claimed_at = NULL
             WHERE story_id = $1 AND status = 'needs_info'
            RETURNING *
            """,
            story_id,
        )
        if row is None:
            existing = await conn.fetchrow(
                "SELECT status FROM dispatch_items WHERE story_id = $1",
                story_id,
            )
            if existing is None:
                raise NotFoundError(f"{story_id} not found")
            raise InvalidTransitionError(
                f"Cannot resume {story_id} from status={existing['status']} — "
                f"only needs_info stories can be resumed via this endpoint."
            )
        return dict(row)
```

### 4.3 Modified: `next_pending()` — MUST NOT include `needs_info`

```python
# BEFORE (STORY-507):
# WHERE status IN ('pending', 'paused')

# AFTER (STORY-532 — needs_info deliberately omitted):
# WHERE status IN ('pending', 'paused')
# (no code change required — 'needs_info' was never in the set)
```

**Test gate:** a unit test MUST assert that after marking a story `needs_info`,
`next_pending()` does not return it even when it is the only row. This prevents
a future well-intentioned edit from adding `needs_info` to the set.

### 4.4 Modified: `list_queue()` — add `needs_info` bucket

```python
# Extend the existing bucket-splitting logic:
needs_info = []
# ... inside the row loop:
elif d["status"] == "needs_info":
    needs_info.append(d)

return {
    "pending": pending,
    "in_progress": in_progress,
    "in_review": in_review,
    "paused": paused,
    "needs_info": needs_info,   # NEW
}
```

Also extend the SELECT WHERE clause to include `'needs_info'`:

```sql
WHERE status IN ('pending', 'claimed', 'in_review', 'paused', 'needs_info')
```

### 4.5 `claim()` — unchanged

Current logic: `WHERE status IN ('pending', 'paused')` → `'claimed'`. Do not
add `'needs_info'` to this set. Only `resume_from_needs_info` moves a story
out of `needs_info`.

---

## 5. API Design

**File:** `tech_dev_agents/ops_console/routes/dispatch.py`

### 5.1 `POST /api/dispatch/needs-info/{story_id}`

Mark a claimed story as `needs_info`. Called by the phase runner when it
detects a QUESTION.md file written by a phase agent.

**Request:**
```http
POST /api/dispatch/needs-info/STORY-518
Content-Type: application/json
X-API-Key: <hermes-internal-key>

{
  "agent": "devon",
  "question_file_path": "features/story-518/QUESTION.md",
  "phase": 1
}
```

**Response 200:**
```json
{
  "story_id": "STORY-518",
  "status": "needs_info",
  "needs_info_path": "features/story-518/QUESTION.md",
  "current_phase": 1
}
```

**Error codes:**
- `404` — story_id not found
- `409` — story is not in `claimed` state (operator raced with another agent)
- `422` — missing `question_file_path` in body
- `401` / `403` — API key missing / invalid (internal endpoint)

**Pydantic response model** (add to `responses.py`):

```python
class DispatchNeedsInfoResponse(BaseModel):
    story_id: str
    status: Literal["needs_info"]
    needs_info_path: str
    current_phase: int | None = None
```

**Feature gate:** wrap the route in `if not getattr(settings, "dispatch_needs_info_enabled", False): raise HTTPException(404, ...)` — mirrors STORY-507's `dispatch_pause_enabled` gate. Default off; enable in staging first. Env var: `OPS_DISPATCH_NEEDS_INFO_ENABLED=true`.

### 5.2 `POST /api/dispatch/resume/{story_id}`

Human-triggered transition: `needs_info → pending`.

**Request:**
```http
POST /api/dispatch/resume/STORY-518
Content-Type: application/json
X-API-Key: <operator-key>
```

(Empty body — the act of calling resume is the human's confirmation that
they've answered QUESTION.md. Per analysis.md Decision 3, the server does NOT
verify file contents.)

**Response 200:**
```json
{
  "story_id": "STORY-518",
  "status": "pending",
  "resumed_at": "2026-04-22T18:45:00Z"
}
```

**Error codes:**
- `404` — story_id not found or not in `needs_info` state
- `409` — story is in a different state (e.g. already `pending` or `claimed`)

**Pydantic response model:**

```python
class DispatchResumeResponse(BaseModel):
    story_id: str
    status: Literal["pending"]
    resumed_at: str
```

**Feature gate:** same env var as `/needs-info` — single toggle for the whole
feature.

### 5.3 Updated: `DispatchQueueResponse`

Add a top-level `needs_info` list and expose it from `GET /api/dispatch/queue`:

```python
class DispatchQueueResponse(BaseModel):
    pending: list[DispatchItem]
    in_progress: list[DispatchItem]
    in_review: list[DispatchItem]
    paused: list[DispatchItem] = []
    needs_info: list[DispatchItem] = []   # STORY-532
    total_pending: int
    total_claimed: int
    fetched_at: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def items(self) -> list[DispatchItem]:
        return self.pending + self.in_progress + self.in_review + self.paused + self.needs_info
```

### 5.4 Updated: `DispatchStatusEnum`

```python
class DispatchStatusEnum(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    PAUSED = "paused"
    NEEDS_INFO = "needs_info"   # STORY-532
```

### 5.5 Updated: `DispatchItem`

Add one field:

```python
class DispatchItem(BaseModel):
    # ... existing ...
    needs_info_path: str | None = None   # STORY-532
```

### 5.6 `GET /api/dispatch/next` — no code change

`next_pending()` already does not include `'needs_info'`. The test suite MUST
assert this; the route itself is unchanged.

---

## 6. Phase Runner Integration

**File:** `deployment/hermes/sdlc_phase_runner.py`

### 6.1 Current behavior (lines ~1354-1364)

```python
question = _check_for_questions(workdir, story_id, story_folder)
if question:
    print(f"[DISPATCH] Phase {phase_num} ({phase_name}) — agent has a QUESTION, pausing", flush=True)
    _notify_teams(
        f"QUESTION on {story_id} (Phase {phase_num} {phase_name}):\n{question}\n\n"
        f"Reply to me or update features/{story_folder}/QUESTION.md with the answer, "
        f"then re-dispatch the story."
    )
    return False, None
```

The `return False, None` falls into the dispatch-poller's generic failure
handling, which re-queues the story with `[RETRY N/3]`.

### 6.2 New behavior

```python
question = _check_for_questions(workdir, story_id, story_folder)
if question:
    print(f"[DISPATCH] Phase {phase_num} ({phase_name}) — agent has a QUESTION, marking needs_info", flush=True)
    question_path = f"features/{story_folder}/QUESTION.md"
    posted = _post_needs_info(
        story_id=story_id,
        question_file_path=question_path,
        agent_name=os.environ.get("AGENT_NAME", "unknown"),
        phase=phase_num,
    )
    if posted:
        # DB now says 'needs_info' — the poller will not re-queue this story.
        _notify_teams(
            f"QUESTION on {story_id} (Phase {phase_num} {phase_name}):\n{question}\n\n"
            f"Append your answer to features/{story_folder}/QUESTION.md on branch "
            f"{branch_name}, then POST /api/dispatch/resume/{story_id}."
        )
        return False, None

    # Fallback: POST failed (network, 500, feature-gate off). Use the legacy path.
    # This re-queues via [RETRY N/3] — noisy but safe.
    print(
        f"[DISPATCH] /needs-info POST failed for {story_id} — "
        f"falling back to legacy notify+return-False path",
        flush=True,
    )
    _notify_teams(
        f"QUESTION on {story_id} (Phase {phase_num} {phase_name}):\n{question}\n\n"
        f"⚠ ops-console /needs-info POST failed — story will auto-retry. "
        f"Answer QUESTION.md on branch {branch_name} before the next retry."
    )
    return False, None
```

### 6.3 New helper: `_post_needs_info()`

```python
def _post_needs_info(
    story_id: str,
    question_file_path: str,
    agent_name: str,
    phase: int,
) -> bool:
    """POST to /api/dispatch/needs-info/{story_id}. Returns True on 2xx."""
    url = f"{OPS_CONSOLE_BASE_URL}/api/dispatch/needs-info/{story_id}"
    try:
        resp = requests.post(
            url,
            json={
                "agent": agent_name,
                "question_file_path": question_file_path,
                "phase": phase,
            },
            headers={"X-API-Key": OPS_CONSOLE_INTERNAL_KEY},
            timeout=10,
        )
        if 200 <= resp.status_code < 300:
            return True
        print(
            f"[DISPATCH] /needs-info POST returned {resp.status_code}: {resp.text[:500]}",
            flush=True,
        )
        return False
    except requests.exceptions.RequestException as exc:
        print(f"[DISPATCH] /needs-info POST exception: {exc}", flush=True)
        return False
```

Reuse `OPS_CONSOLE_BASE_URL` and `OPS_CONSOLE_INTERNAL_KEY` constants already
present in `sdlc_phase_runner.py` for presence-push and pause endpoints.

### 6.4 Interaction with `_check_daily_session_cap` and rate-limit handling

The `/needs-info` branch is checked **before** the rc-based error branches
(rc == -429, rc != 0). Order:

1. `_check_for_questions` → if QUESTION.md → **POST /needs-info** → return
2. `rc == -429` → rate-limit pause (existing)
3. `rc != 0` → phase failed → return

A story that wrote QUESTION.md AND hit a rate limit in the same phase is
classified as `needs_info` (higher priority — the human block is the real
problem; rate-limit will self-heal).

---

## 7. Frontend

### 7.1 `frontend/src/types/api.ts`

```ts
export type DispatchStatus =
  | 'pending'
  | 'claimed'
  | 'in_review'
  | 'completed'
  | 'cancelled'
  | 'failed'
  | 'paused'
  | 'needs_info';   // STORY-532

export interface DispatchItem {
  // ... existing fields ...
  needs_info_path?: string | null;   // STORY-532
}

export interface DispatchQueueResponse {
  pending: DispatchItem[];
  in_progress: DispatchItem[];
  in_review: DispatchItem[];
  paused?: DispatchItem[];
  needs_info?: DispatchItem[];       // STORY-532
  total_pending: number;
  total_claimed: number;
  fetched_at: string;
}
```

### 7.2 `frontend/src/components/DispatchQueue.tsx`

Add to `statusBadge()`:

```tsx
case 'needs_info':
  return { className: 'bg-violet-600/20 text-violet-400', label: 'needs info' };
```

Render a new group (mirrors `paused` rendering around line 302-310):

```tsx
{/* STORY-532: needs_info rows — awaiting human answer to QUESTION.md */}
{data.needs_info?.map((item) => (
  <ItemRow key={item.story_id} item={item} />
))}
```

If the broader layout already groups visually by status, add a `needs_info`
section header between `paused` and `in_review` groups. The operator should
see `needs_info` prominently because these are the items needing their
attention.

**Exhaustive switch:** if the status switch uses a TypeScript `never`
assertion for exhaustiveness, the new `needs_info` arm is required for the
file to compile. This is the enforcing gate for RISK-5 from analysis.md.

---

## 8. Security & Auth

| Endpoint | Caller | Auth |
|----------|--------|------|
| `POST /needs-info/{id}` | phase runner (hermes VM) | `X-API-Key` internal key (same as `/pause`) |
| `POST /resume/{id}` | operator (dashboard or Mark's terminal) | Operator session or API key (follow existing `/cancel` pattern) |

Both routes are behind the `OPS_DISPATCH_NEEDS_INFO_ENABLED` env gate so the
feature ships dark and is enabled in staging first.

No secrets appear in request/response bodies. `question_file_path` is a
repo-relative path (already visible in git diffs) — not sensitive.

---

## 9. Observability

| Signal | Shape |
|--------|-------|
| Log line on `needs_info` transition | `[DISPATCH] {story_id} → needs_info (phase={N}, path={...})` |
| Log line on `resume` | `[DISPATCH] {story_id} resumed from needs_info (was {N}s in state)` |
| Log line on POST failure | `[DISPATCH] /needs-info POST failed for {story_id}: {status_code}` |
| Teams notification on `needs_info` | Existing `_notify_teams` call with new body (includes resume URL + branch) |
| Teams notification on POST failure | Existing + `⚠ ops-console /needs-info POST failed` prefix |
| Dashboard count | `needs_info.length` visible in DispatchQueue header |

**No new Prometheus metrics required for MVP.** If operational experience
shows a pattern of `needs_info` stories accumulating without human response,
add `dispatch_needs_info_age_seconds` histogram in a follow-up.

---

## 10. Testing Strategy (detailed design → Phase 7)

Phase 7 will produce `test-design.md`. For now, the acceptance-diff test files
and their coverage targets:

### 10.1 `tests/ops_console/test_needs_info_state.py`

| # | Test | Asserts |
|---|------|---------|
| T1 | `needs_info()` transitions claimed → needs_info | row status + needs_info_path |
| T2 | `needs_info()` raises on pending | InvalidTransitionError |
| T3 | `needs_info()` raises on unknown story | NotFoundError |
| T4 | `next_pending()` does NOT return needs_info | None returned when only needs_info exists |
| T5 | `list_queue()` returns needs_info bucket | dict has "needs_info" key, correct rows |
| T6 | `resume_from_needs_info()` transitions needs_info → pending | row status reset, path cleared |
| T7 | `resume_from_needs_info()` raises on non-needs_info | InvalidTransitionError |
| T8 | Unique index blocks duplicate enqueue while needs_info | `enqueue()` raises DuplicateDispatchError |
| T9 | `POST /needs-info/{id}` success path | 200, DB row updated |
| T10 | `POST /needs-info/{id}` on pending → 409 | correct error code |
| T11 | `POST /needs-info/{id}` feature-gate off → 404 | correct error code |
| T12 | `POST /resume/{id}` success path | 200, DB row back to pending |
| T13 | `POST /resume/{id}` on pending → 404/409 | correct error code |
| T14 | `GET /dispatch/queue` includes needs_info bucket | response shape |
| T15 | `GET /dispatch/next` skips needs_info | 204 or returns different story |

### 10.2 `tests/deployment/test_phase_runner_needs_info.py`

| # | Test | Asserts |
|---|------|---------|
| T16 | QUESTION.md present → POSTs /needs-info | stubbed POST called with correct body |
| T17 | POST success → returns (False, None) | no retry scheduled |
| T18 | POST failure → falls back to _notify_teams | fallback path exercised, returns (False, None) |
| T19 | No QUESTION.md → no POST | negative path |
| T20 | QUESTION.md + rc==-429 → needs_info wins | ordering |

### 10.3 Regression
- All existing `paused` tests remain green.
- `test_dispatch_routes.py` `/next`, `/queue`, `/pause`, `/resume-from-pause`
  all unchanged.

---

## 11. Rollout Plan

1. **Migrate staging DB** → apply `007_needs_info_state.sql`.
2. **Deploy ops-console** with `OPS_DISPATCH_NEEDS_INFO_ENABLED=true` in
   staging. Routes are live but no phase runner calls them yet.
3. **Deploy hermes phase runner** with the new `_post_needs_info` handler.
4. **Validation:** dispatch a deliberately-ambiguous story in staging.
   Observe:
   - Agent writes QUESTION.md and exits
   - Phase runner POSTs `/needs-info` (log line visible)
   - Dashboard shows story in `needs_info` bucket with violet badge
   - `/api/dispatch/next` does not return the story (other agents skip)
   - Operator appends an answer to QUESTION.md, POSTs `/resume`
   - Story moves to `pending`, next poll claims it, agent reads the answered
     QUESTION.md and proceeds
5. **Promote to prod:** apply migration 007 → deploy code → enable flag.
6. **Backfill:** no backfill required. Stories already in `failed` from the
   pre-532 retry loop stay in `failed`; operator can manually re-dispatch
   them if needed.

---

## 12. Follow-ups (out of scope for STORY-532)

- Teams Adaptive Card with inline "Answer" text field that POSTs a patch to
  QUESTION.md (current plan: operator edits file on branch directly).
- `dispatch_needs_info_age_seconds` histogram + alert on rows `needs_info > 24h`.
- CLI shortcut: `cai dispatch resume STORY-X` for Mark's terminal.
- One-time script to convert existing `failed` rows that are really
  unanswered-question failures back to `needs_info`. Only worth doing if the
  retroactive cleanup has value; ask before building.

---

## 13. Acceptance Mapping

Cross-check against the dispatch prompt's acceptance diff:

| Acceptance item | Covered in |
|-----------------|-----------|
| `scripts/migrations/NNN_needs_info_state.sql` | §3.1 (filename: `007_...`) |
| `dispatch_db_service.py` — new methods, next_pending excludes, list_queue bucket | §4.1–4.4 |
| `dispatch.py` — POST /needs-info, POST /resume, queue response field | §5.1–5.3 |
| `responses.py` — status union includes needs_info | §5.4 |
| `sdlc_phase_runner.py` — POST /needs-info instead of _notify_teams+return-False | §6.2 |
| `frontend/src/types/api.ts` — status union + needs_info list | §7.1 |
| `frontend/src/components/DispatchQueue.tsx` — violet badge, needs_info group | §7.2 |
| `tests/ops_console/test_needs_info_state.py` | §10.1 |
| `tests/deployment/test_phase_runner_needs_info.py` | §10.2 |

All 9 acceptance items mapped. Test criteria 1–6 from the dispatch prompt
map to T4, T5, T12, T16–T17, T15, and the frontend visual check respectively.

---

## Next Phase

→ **Phase 7 (Test Design)** — expand §10 into `test-design.md`, write RED
  tests covering T1–T20 plus any gaps, confirm test infra runs, hand off to
  Phase 8 implementation.
