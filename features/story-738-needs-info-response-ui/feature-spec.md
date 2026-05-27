# Feature Spec: STORY-738 — Needs Info Response UI (DB-Mediated Q&A)

**Story:** STORY-738
**Scope:** Medium
**Approach:** B — DB-Mediated Q&A (analysis.md, weighted 4.90/5)
**Phase path:** 1 → 6 → 7 → 8 → Done

---

## Overview

Eliminate SSH-based operator friction when agents are blocked on questions. Instead of reading `QUESTION.md` via SSH, the agent POSTs question text to the dispatch DB at `needs_info` time. The operator reads the question and submits an answer entirely through the dashboard UI. The answer is stored in the DB and returned to the agent on its next claim.

**Key architectural decision:** Zero SSH from ops-console. All data flows through the existing dispatch DB + HTTP API pattern. This is consistent with every other agent↔ops-console interaction in the codebase.

---

## 1. Database Migration — `014_question_answer_text.sql`

Add two nullable TEXT columns to `dispatch_items`:

```sql
-- Migration 014: Add question_text and answer_text columns for DB-mediated Q&A (STORY-738)
ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS question_text TEXT;
ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS answer_text TEXT;
```

| Column | Type | Default | Purpose |
|--------|------|---------|---------|
| `question_text` | TEXT | NULL | Agent's blocking question, posted alongside `question_file_path` |
| `answer_text` | TEXT | NULL | Operator's answer, written via new `/answer` endpoint |

**Size guard:** Application-level 64 KB cap on both columns (reject with 422 if exceeded). PostgreSQL TEXT handles this natively; the cap prevents pathological payloads.

**Lifecycle:**
- `question_text` set by `POST /dispatch/needs-info/{story_id}` (extended, optional field)
- `answer_text` set by `POST /dispatch/{story_id}/answer` (new endpoint)
- Both cleared on cancel (`cancel()` already NULLs ephemeral fields)
- `question_text` preserved across `resume_from_needs_info()` (same as `needs_info_path` — agent may need it)
- `answer_text` preserved across `resume_from_needs_info()` so the next claim can return it

**Rollback:** `ALTER TABLE dispatch_items DROP COLUMN IF EXISTS question_text; ALTER TABLE dispatch_items DROP COLUMN IF EXISTS answer_text;`

---

## 2. Backend Changes

### 2.1 Extend `POST /dispatch/needs-info/{story_id}` (existing route)

**Current request body fields:** `question_file_path` (required), `agent_name` (optional), `phase` (optional)

**Add optional field:** `question_text` (string, max 64 KB)

```python
# In needs_info_story() route handler — after extracting question_file_path:
question_text = body.get("question_text")  # optional
if question_text and len(question_text) > 65536:
    raise HTTPException(422, "question_text exceeds 64 KB limit")
```

**Service layer change** — `DispatchDBService.needs_info()`:
- Add `question_text: str | None = None` parameter
- UPDATE query: `SET status = 'needs_info', needs_info_path = $2, question_text = COALESCE($N, question_text)`
- COALESCE semantics: if agent sends question_text, store it; if omitted, preserve any existing value (idempotency)

**Backward compatibility:** `question_text` is optional. Agents that haven't been updated continue to work — the field stays NULL and the operator sees a fallback message in the modal.

### 2.2 New `GET /dispatch/{story_id}/question` endpoint

**Purpose:** Fetch the stored question for display in the operator modal.

| Attribute | Value |
|-----------|-------|
| Method | GET |
| Path | `/dispatch/{story_id}/question` |
| Auth | Standard (MSAL or API key) |
| Feature gate | `OPS_DISPATCH_NEEDS_INFO_ENABLED` (reuse existing gate) |
| Idempotent | Yes (GET) |
| Missing resource | 404 if story not found |
| Wrong state | 404 if story is not in `needs_info` state |

**Response (200):**
```json
{
  "story_id": "STORY-738",
  "repo": "tech-dev-agents",
  "agent": "daisy",
  "current_phase": 7,
  "needs_info_path": "/opt/agent/features/story-738-slug/QUESTION.md",
  "question_text": "Should the modal include markdown preview?",
  "has_question_text": true,
  "fetched_at": "2026-04-30T12:00:00Z"
}
```

**Response model — `QuestionResponse` (new):**

```python
class QuestionResponse(BaseModel):
    story_id: str
    repo: str
    agent: str | None = None
    current_phase: int | None = None
    needs_info_path: str | None = None
    question_text: str | None = None
    has_question_text: bool  # True if question_text is non-None/non-empty
    fetched_at: str
```

**Logic:**
1. Fetch dispatch item by `story_id` via `db_svc.get(story_id)`.
2. If not found → 404.
3. If `status != 'needs_info'` → 404 (with detail: "Story is not in needs_info state").
4. Build `QuestionResponse` from row fields.
5. `has_question_text = bool(row.get("question_text"))` — tells frontend whether to show the question or the fallback panel.

**No new service method needed** — uses existing `db_svc.get()`.

### 2.3 New `POST /dispatch/{story_id}/answer` endpoint

**Purpose:** Store the operator's answer and resume the dispatch item.

| Attribute | Value |
|-----------|-------|
| Method | POST |
| Path | `/dispatch/{story_id}/answer` |
| Auth | Standard (MSAL or API key) |
| Feature gate | `OPS_DISPATCH_NEEDS_INFO_ENABLED` (reuse existing gate) |
| Idempotent | Yes — re-submitting overwrites `answer_text` and re-triggers resume; second resume is a no-op if already pending |
| Missing resource | 404 if story not found |
| Wrong state | 409 if story is not in `needs_info` (already resumed) |
| Unknown fields | 422 via Pydantic `extra="forbid"` |

**Request model — `AnswerRequest` (new):**
```python
class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(..., min_length=1, max_length=65536)
    operator: str = Field(default="mark", pattern=r"^[a-zA-Z0-9_-]+$")
```

**Response model — `AnswerResponse` (new):**
```python
class AnswerResponse(BaseModel):
    story_id: str
    status: str  # "pending" after resume
    answered_at: str
```

**Response (200):**
```json
{
  "story_id": "STORY-738",
  "status": "pending",
  "answered_at": "2026-04-30T12:01:00Z"
}
```

**Logic:**
1. Validate feature gate.
2. Parse `AnswerRequest` body (Pydantic validates length, extra fields).
3. Call new `db_svc.answer_needs_info(story_id, answer_text, operator)`.
4. Service method atomically: (a) writes `answer_text` to the row, (b) transitions `needs_info → pending`, (c) clears `claimed_by`/`claimed_at`.
5. Emit `answered` event via `emit_event()` (STORY-700 pattern).
6. Log audit line: `"Answered %s (operator=%s, answer_len=%d)"`.
7. Return `AnswerResponse`.

**Idempotency:** If the story was already resumed (status != `needs_info`), return 409 with detail "Story is not in needs_info state — may have already been answered." This is safe: the first submit succeeded, the second is a no-op from the operator's perspective.

### 2.3a Gating Strategy — Intentional Asymmetry

**Design decision:** The needs_info() service method (Section 2.1) is NOT gated by
OPS_DISPATCH_NEEDS_INFO_ENABLED. The write path is always active. Only the retrieval
paths (Sections 2.2 and 2.3) are gated.

**Rationale:** Agents invoke POST /dispatch/needs-info/{story_id} to transition state —
an existing ungated operation. question_text is an additive extension. The NEW
capabilities requiring a feature flag are operator UI features: reading questions (GET)
and posting answers (POST). Agents are not blocked when the feature is disabled.

**Operational implication:** With gate OFF — question_text is written to DB but cannot
be retrieved. With gate ON — full question/answer flow available. This is intentional.

### 2.4 New service method — `DispatchDBService.answer_needs_info()`

```python
async def answer_needs_info(
    self, story_id: str, answer_text: str, operator: str
) -> dict[str, Any]:
    """Store operator answer and resume from needs_info → pending.

    Atomic: writes answer_text + transitions status in a single UPDATE.
    Preserves needs_info_path and question_text (agent needs both on next claim).
    Clears claimed_by/claimed_at so the story re-enters the queue fresh.

    Raises NotFoundError if story_id not found.
    Raises InvalidTransitionError if story is not in needs_info state.
    """
    async with self._pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE dispatch_items
               SET status = 'pending',
                   answer_text = $2,
                   claimed_by = NULL,
                   claimed_at = NULL,
                   updated_at = now()
             WHERE story_id = $1 AND status = 'needs_info'
            RETURNING *
            """,
            story_id, answer_text,
        )
        if row is not None:
            return dict(row)
        existing = await conn.fetchrow(
            "SELECT status FROM dispatch_items WHERE story_id = $1", story_id,
        )
        if existing is None:
            raise NotFoundError(f"{story_id} not found")
        raise InvalidTransitionError(
            f"Cannot answer {story_id} from status={existing['status']} — only needs_info stories."
        )
```

**Why a new method (not reusing `resume_from_needs_info`):** The existing `resume_from_needs_info()` doesn't write `answer_text`. Rather than adding an optional parameter to a method called from the existing `/resume` route (which has no answer concept), a new method keeps concerns separate and avoids changing the existing resume contract.

### 2.5 Extend claim response to include `answer_text`

**In the `claim_story()` route handler** (line ~546):
- Read `answer_text` from the row alongside `needs_info_path`.
- Set `answer_text` on the `DispatchItem` being returned.

**In `DispatchItem` model:**
- Add `answer_text: str | None = None`.

This allows the agent to read the operator's answer directly from the claim response without a separate endpoint call.

### 2.6 Clear `question_text` and `answer_text` on cancel

**In `DispatchDBService.cancel()`** — extend the UPDATE SET clause:
```sql
SET status = 'cancelled',
    cancelled_at = now(),
    ...existing clears...,
    question_text = NULL,
    answer_text = NULL
```

This keeps cancelled rows clean (same pattern as clearing `needs_info_path`).

---

## 3. Frontend Changes

### 3.1 New API functions (`frontend/src/api/client.ts` or new `dispatch.ts`)

```typescript
// Fetch question for a needs_info story
export async function getQuestion(storyId: string): Promise<QuestionResponse> {
  return api.get<QuestionResponse>(`/api/dispatch/${storyId}/question`);
}

// Submit answer and resume the story
export async function submitAnswer(
  storyId: string,
  answer: string,
  operator: string = 'mark'
): Promise<AnswerResponse> {
  return api.post<AnswerResponse>(`/api/dispatch/${storyId}/answer`, {
    answer,
    operator,
  });
}
```

**Types (add to `frontend/src/types/api.ts`):**
```typescript
export interface QuestionResponse {
  story_id: string;
  repo: string;
  agent: string | null;
  current_phase: number | null;
  needs_info_path: string | null;
  question_text: string | null;
  has_question_text: boolean;
  fetched_at: string;
}

export interface AnswerResponse {
  story_id: string;
  status: string;
  answered_at: string;
}
```

### 3.2 New `NeedsInfoAnswerModal` component

**File:** `frontend/src/components/NeedsInfoAnswerModal.tsx`

**Props:**
```typescript
interface NeedsInfoAnswerModalProps {
  storyId: string;
  isOpen: boolean;
  onClose: () => void;
  onAnswered: () => void;  // callback to trigger queue refresh
}
```

**Behavior:**

| State | Rendering |
|-------|-----------|
| Loading | Skeleton placeholder (header + body area) |
| Question loaded (`has_question_text: true`) | Header with agent/story/phase, question text in a styled read-only block, answer textarea + submit button |
| Question unavailable (`has_question_text: false`) | Header, fallback panel showing `needs_info_path` with a "Question text not available — agent posted file path only" message |
| Fetch error (network/500) | Error banner with retry button |
| Submitting | Submit button disabled with spinner, textarea locked |
| Submit success | Modal closes, `onAnswered()` fires (triggers queue refetch) |
| Submit error (409 — already answered) | Toast/banner: "Already answered — refreshing queue", close modal, `onAnswered()` fires |
| Submit error (other) | Error banner with retry option, textarea remains editable |

**Modal layout:**
```
┌─────────────────────────────────────────────┐
│ Answer Question — STORY-738                  │  [X]
│ Agent: daisy · Phase 7 · tech-dev-agents     │
├─────────────────────────────────────────────┤
│                                              │
│  Question:                                   │
│  ┌────────────────────────────────────────┐  │
│  │ Should the modal include markdown      │  │
│  │ preview or plain text only?            │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  Your Answer:                                │
│  ┌────────────────────────────────────────┐  │
│  │                                        │  │
│  │                                        │  │
│  └────────────────────────────────────────┘  │
│                                              │
│                         [Submit Answer]       │
└─────────────────────────────────────────────┘
```

**Implementation notes:**
- Use `useQuery` with `queryKey: ['dispatch-question', storyId]` for the GET call (no auto-refetch — modal is short-lived).
- Use `useMutation` for the POST call, with `onSuccess` calling `queryClient.invalidateQueries({ queryKey: ['dispatch-queue'] })` + `onAnswered()`.
- Submit button disabled when `answer.trim().length === 0` or mutation is pending.
- No markdown preview (out of scope per seed.md).
- Plain `<textarea>` for the answer — no rich editor.

### 3.3 Modify `DispatchQueue.tsx` — Add "Answer" button to needs_info rows

**In the needs_info section's `ItemRow` rendering:**

Add an "Answer" button (violet-themed, matching the needs_info badge color) next to the existing cancel button:

```tsx
{item.status === 'needs_info' && (
  <button
    onClick={() => setAnswerModal({ storyId: item.story_id, isOpen: true })}
    className="..."  // violet outline button
    aria-label={`Answer question for ${item.story_id}`}
  >
    Answer
  </button>
)}
```

**State management:**
- Add `const [answerModal, setAnswerModal] = useState<{ storyId: string; isOpen: boolean } | null>(null)` to `DispatchQueue`.
- Render `<NeedsInfoAnswerModal>` conditionally at the bottom of the component.
- `onAnswered` callback calls `refetch()` from `useDispatchQueue()`.

### 3.4 Update `DispatchItem` TypeScript interface

Add `answer_text` field (for completeness in the type, even though it's primarily consumed by agents):
```typescript
export interface DispatchItem {
  // ...existing fields...
  question_text?: string | null;  // STORY-738
  answer_text?: string | null;    // STORY-738
}
```

---

## 4. Agent-Side Changes (Minimal)

### 4.1 Post question_text in `/needs_info` call

When the phase runner calls `POST /dispatch/needs-info/{story_id}`, include the contents of `QUESTION.md` in the request body:

```python
# In the phase runner's needs_info signal:
question_text = Path(question_file_path).read_text(encoding="utf-8")[:65536]
requests.post(f"{OPS_URL}/api/dispatch/needs-info/{story_id}", json={
    "question_file_path": str(question_file_path),
    "question_text": question_text,
    "agent_name": agent_name,
    "phase": current_phase,
})
```

### 4.2 Read answer_text from claim response

When the poller claims a story, check if `answer_text` is present in the claim response's `item`:

```python
claim_data = response.json()
answer_text = claim_data.get("item", {}).get("answer_text")
if answer_text:
    # Write ANSWER.md adjacent to QUESTION.md
    answer_path = question_dir / "ANSWER.md"
    answer_path.write_text(answer_text, encoding="utf-8")
```

This replaces the need for any SSH-based answer delivery.

---

## 5. Error Handling Design

### Error Response Format

All new endpoints follow the project's existing pattern (HTTPException with status code + detail string). The codebase does not currently use RFC 7807 Problem Details format — new endpoints will match the established convention for consistency.

| Error Condition | Status Code | Detail |
|-----------------|-------------|--------|
| Story not found | 404 | `"{story_id} not found"` |
| Story not in needs_info (GET /question) | 404 | `"{story_id} is not in needs_info state"` |
| Story not in needs_info (POST /answer) | 409 | `"Cannot answer {story_id} from status={status} — only needs_info stories."` |
| Feature gate disabled | 404 | `"endpoint is disabled (OPS_DISPATCH_NEEDS_INFO_ENABLED=false)"` |
| answer field empty | 422 | Pydantic validation error |
| answer exceeds 64 KB | 422 | Pydantic `max_length` validation error |
| question_text exceeds 64 KB | 422 | `"question_text exceeds 64 KB limit"` |
| Unknown fields in POST body | 422 | Pydantic `extra="forbid"` validation error |

### Logging

| Event | Log Level | Fields |
|-------|-----------|--------|
| Question fetched | INFO | story_id, agent, phase, has_question_text |
| Answer submitted | INFO | story_id, operator, answer_length, transition (needs_info→pending) |
| Answer failed (wrong state) | WARNING | story_id, current_status, operator |

### What is NOT exposed to callers

- Internal DB error details (asyncpg exceptions)
- Stack traces
- Server file paths
- Connection pool state

### Fallback behavior

| Dependency | Unavailable Behavior | Rationale |
|------------|---------------------|-----------|
| PostgreSQL | 503 (existing app-level handler) | Fail-closed — no silent zeros |
| Frontend fetch fails | Error banner with retry button | Operator can retry or fall back to SSH manually |

---

## 6. HTTP Method Semantics

| Endpoint | Method | Idempotent | Resource Must Exist | Unknown Fields | Missing Resource |
|----------|--------|-----------|---------------------|----------------|------------------|
| `/dispatch/{story_id}/question` | GET | Yes | Yes → 404 | N/A | 404 |
| `/dispatch/{story_id}/answer` | POST | Yes (by design) | Yes → 404 | Reject (422) | 404 |

**Idempotency detail for POST /answer:** Re-submitting overwrites `answer_text` and attempts `needs_info → pending`. If already pending (second submit), returns 409 — which the frontend handles gracefully as "already answered." The first submit is the one that takes effect; subsequent submits are rejected, not silently accepted.

---

## 7. Build Order

| Commit | Component | Files | Dependencies |
|--------|-----------|-------|--------------|
| 1 | Migration + model updates | `scripts/migrations/014_question_answer_text.sql`, `models/responses.py` | None |
| 2 | Service method + extended needs_info | `services/dispatch_db_service.py` | Commit 1 |
| 3 | Backend routes (GET /question, POST /answer) | `routes/dispatch.py` | Commit 2 |
| 4 | Frontend types + API functions | `frontend/src/types/api.ts`, `frontend/src/api/client.ts` | Commit 3 |
| 5 | NeedsInfoAnswerModal + DispatchQueue integration | `frontend/src/components/NeedsInfoAnswerModal.tsx`, `frontend/src/components/DispatchQueue.tsx` | Commit 4 |

---

## 8. Files Modified / Created

| File | Action | Story |
|------|--------|-------|
| `scripts/migrations/014_question_answer_text.sql` | **Create** | STORY-738 |
| `tech_dev_agents/ops_console/models/responses.py` | Modify — add `question_text`, `answer_text` to `DispatchItem`; add `QuestionResponse`, `AnswerRequest`, `AnswerResponse` | STORY-738 |
| `tech_dev_agents/ops_console/services/dispatch_db_service.py` | Modify — extend `needs_info()` with `question_text` param; add `answer_needs_info()` method | STORY-738 |
| `tech_dev_agents/ops_console/routes/dispatch.py` | Modify — extend `needs_info_story()`, add `get_question()`, add `answer_story()`, extend `claim_story()` to surface `answer_text` | STORY-738 |
| `frontend/src/types/api.ts` | Modify — add `QuestionResponse`, `AnswerResponse` interfaces; extend `DispatchItem` | STORY-738 |
| `frontend/src/api/client.ts` | Modify — add `getQuestion()`, `submitAnswer()` | STORY-738 |
| `frontend/src/components/NeedsInfoAnswerModal.tsx` | **Create** | STORY-738 |
| `frontend/src/components/DispatchQueue.tsx` | Modify — add Answer button + modal integration | STORY-738 |
| `tests/ops_console/test_dispatch_needs_info_answer.py` | **Create** — new test file | STORY-738 |

---

## 9. Open Questions — Resolved

| # | Question (from seed.md) | Resolution |
|---|-------------------------|------------|
| 1 | SSH transport reuse | **Eliminated.** Approach B removes SSH entirely. All data flows through DB. |
| 2 | Atomic write semantics for ANSWER.md | **N/A for ops-console.** Agent writes ANSWER.md locally from claim response data. No cross-VM file writes. |
| 3 | Wake mechanism after ANSWER.md write | **Existing resume flow.** `POST /answer` transitions `needs_info → pending`. Agent picks up the story on its next claim cycle. Answer text is in the claim response. |
| 4 | Operator identity source | **Client-provided, server-logged.** `operator` field in POST body, defaulting to "mark". Server-side SSO derivation is a future enhancement when SSO is fully wired. |
| 5 | Question text size limits | **64 KB cap.** Enforced at application level on both question_text (POST /needs-info) and answer (POST /answer) via Pydantic `max_length` or explicit check. |
| 6 | Concurrent answers | **409 on second submit.** First `POST /answer` transitions to pending. Second submit finds status != needs_info → 409. Frontend displays "already answered" message and refreshes queue. |

---

## 10. Acceptance Criteria Mapping

| SC | Backend Route/Method | Frontend Component | Test Group |
|----|---------------------|-------------------|------------|
| SC-1 | — | DispatchQueue.tsx (Answer button) | Frontend unit |
| SC-2 | GET /question | NeedsInfoAnswerModal (question display) | Integration |
| SC-3 | POST /answer | NeedsInfoAnswerModal (submit flow) | Integration |
| SC-4 | `answer_needs_info()` → writes `answer_text` to DB | — | Backend unit |
| SC-5 | `answer_needs_info()` → `needs_info → pending` | — | Backend unit |
| SC-6 | — | DispatchQueue (refetch on onAnswered) | Frontend integration |
| SC-7 | GET /question → `has_question_text: false` | NeedsInfoAnswerModal (fallback panel) | Frontend + backend unit |
| SC-8 | GET /question → 404 when not needs_info | — | Backend unit |
| SC-9 | POST /answer → second call returns 409 | — | Backend unit |
| SC-10 | `logger.info("Answered %s ...")` | — | Backend unit |
