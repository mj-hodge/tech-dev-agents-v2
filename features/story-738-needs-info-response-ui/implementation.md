# STORY-738 / STORY-804 — Phase 8 Implementation Report

**Phase:** 8 (Implementation) · **Scope:** Medium · **Status:** Complete

---

## STORY-804 Rework Completion Note

**STORY-804** was dispatched as a rework of STORY-738 to address Morris's H-2 finding:
"partial-gate docs missing from feature-spec.md." The fix was to document the intentional
gating asymmetry (write path always active, read/answer paths gated by
`OPS_DISPATCH_NEEDS_INFO_ENABLED`) in **section 2.3a** of the feature spec.

**Resolution:** Section 2.3a ("Gating Strategy — Intentional Asymmetry") was authored and
committed to branch `story-738/story-738` during STORY-738 Phase 8 rounds 4–5, then merged
to `main` via PR #227 on 2026-05-02 (Round 5 final, CI 6/6, dual-review APPROVE). No
additional production code changes were required for STORY-804.

**Verification (2026-05-02):**
- `feature-spec.md` §2.3a present in `origin/main` ✅
- `tests/ops_console/test_dispatch_needs_info_answer.py` — 22/22 GREEN ✅
- PR #227 MERGED ✅

---

## Summary

Implemented the DB-mediated Q&A system for operator-answered `needs_info` dispatch items
(Approach B from Phase 6 feature-spec.md). Agents post `question_text` with their
`/needs-info` signal; operators read and answer via the new modal; agents receive the
`answer_text` in their `/claim` response.

---

## Test Results

| Layer | Tests | Result |
|-------|-------|--------|
| Backend (ops-console) | 22 | ✅ 22/22 GREEN |
| Frontend E2E (Playwright) | 8 | ✅ 8/8 GREEN |
| **Total** | **30** | ✅ **30/30 GREEN** |

---

## Files Changed

### Database Migration
- `scripts/migrations/014_needs_info_qa_columns.sql` — adds `question_text`, `answer_text`, `answered_at`, `answered_by` to `dispatch_items`; idempotent (`ADD COLUMN IF NOT EXISTS`); wrapped in `BEGIN/COMMIT`

### Service Layer (`tech_dev_agents/ops_console/services/dispatch_db_service.py`)
- `needs_info()` — extended to accept optional `question_text` kwarg; stored via `COALESCE($3, question_text)` (preserves existing value on re-signal)
- `record_answer()` — new method; atomic first-writer-wins UPDATE (`WHERE status='needs_info' AND answer_text IS NULL`); transitions `needs_info → pending`; returns `{"_already_answered": True}` sentinel on idempotent re-submit
- `cancel()` — extended to NULL all four new columns alongside `needs_info_path`
- `claim()` — no signature change; route handler reads `row["answer_text"]` for `ClaimResponse`

### Pydantic Models (`tech_dev_agents/ops_console/models/responses.py`)
- `DispatchQuestionResponse` — new
- `AnswerRequest` — new (Pydantic `min_length=1`, `max_length=65_536`)
- `AnswerResponse` — new
- `DispatchNeedsInfoResponse` — extended with `question_text_stored: bool`
- `ClaimResponse` — extended with `answer_text: str | None`

### API Routes (`tech_dev_agents/ops_console/routes/dispatch.py`)
- `GET /api/dispatch/{story_id}/question` — new; 404 when story not in `needs_info`; `available=false` when `question_text IS NULL`; `?repo=` support (STORY-531)
- `POST /api/dispatch/{story_id}/answer` — new; server-derived operator identity; emits `dispatch_answered` structured log; idempotent (200 + `already_answered`)
- `POST /api/dispatch/needs-info/{story_id}` — extended to accept `question_text`; 422 on >64 KB
- `POST /api/dispatch/claim/{story_id}` — extended `ClaimResponse` to include `answer_text`

### Frontend (`frontend/src/`)
- `components/NeedsInfoAnswerModal.tsx` — new; loading skeleton (`role="status"`), fallback panel when `available=false`, answer textarea (6 rows, maxLength=65_536), cancel/submit actions; Escape key closes
- `components/DispatchQueue.tsx` — adds Answer button on `needs_info` rows; `answeringStory` state controls modal open/close
- `hooks/useDispatchQueue.ts` — unchanged (remote implementation uses `api/client.ts` helpers directly in modal)
- `types/api.ts` — extended `DispatchItem` with `needs_info_path`, `current_phase`, `question_text`, `answer_text`; added `QuestionResponse` and `AnswerResponse` interfaces
- `api/client.ts` — added `getQuestion()` and `submitAnswer()` helpers
- `vite-env.d.ts` — added (`/// <reference types="vite/client" />`) to fix TypeScript error for `import.meta.env`

### Agent Runner (`deployment/hermes/sdlc_phase_runner.py`)
- `_post_needs_info()` — reads `QUESTION.md` (64 KB cap, non-fatal on failure) and includes `question_text` in POST body
- `_consume_resumed_answer()` — new function; writes `ANSWER.md` adjacent to `QUESTION.md` from claim response `answer_text`; idempotent on identical content; skips when `answer_text=None`

### Dispatch Poller (`deployment/hermes/dispatch_poller.py`)
- Threads `answer_text` from claim response → `run_sdlc_phases()` as `resumed_answer_text`

---

## Design Decisions (from Feature Spec)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Status after answer | `needs_info → pending` | Keeps single resume codepath; avoids skipping single-active-claim guard (STORY-552) |
| Operator identity | Server-derived | `user_email` → `agent_name` → `key_tail` → `"unknown"`; body `operator` field advisory only |
| Concurrent answers | First-writer-wins | Atomic SQL `WHERE answer_text IS NULL` guard; second writer gets 200 + `already_answered` |
| 64 KB cap enforcement | Application-side | Returns 422 with byte count rather than generic SQL error |
| SSH transport | Not used | Zero SSH infrastructure in ops-console; DB is the exclusive Q&A channel |

---

## PR

**PR #227** — `story-738/story-738 → main`

---

## Follow-ups (not in scope)

- Phase prompt template referencing `ANSWER.md` when present
- Multi-turn Q&A
- Operator answer history panel
- Push notification on `needs_info`
- Markdown rendering of `question_text`
