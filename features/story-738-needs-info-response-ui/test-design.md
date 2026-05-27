# Test Design: STORY-738 — Needs Info Response UI (DB-Mediated Q&A)

**Story:** STORY-738
**Scope:** Medium
**Coverage target:** 60%
**Phase:** 7 — Test Design (RED state)

---

## Test Structure

```
tests/
└── ops_console/
    └── test_dispatch_needs_info_answer.py    # 22 backend tests (19 RED, 3 GREEN)

e2e/
└── dispatch-needs-info-answer.spec.ts        # 8 Playwright e2e tests (all RED)
```

**Total: 30 tests** (27 RED, 3 GREEN)

---

## Backend Tests — `test_dispatch_needs_info_answer.py`

### Group A — Service layer: `answer_needs_info()` + extended `needs_info()` (T01–T07)

| Test | What It Verifies | RED Reason | SC |
|------|-----------------|------------|-----|
| `test_answer_needs_info_transitions_to_pending` | T01: `answer_needs_info()` transitions needs_info → pending | Method doesn't exist (AttributeError) | SC-5 |
| `test_answer_needs_info_stores_answer_text` | T02: answer_text column is populated | Method doesn't exist | SC-4 |
| `test_answer_needs_info_clears_claimed_fields` | T03: claimed_by/claimed_at cleared for re-queue | Method doesn't exist | SC-5 |
| `test_answer_needs_info_not_found_raises` | T04: NotFoundError for unknown story_id | Method doesn't exist | — |
| `test_answer_needs_info_wrong_state_raises` | T05: InvalidTransitionError for non-needs_info | Method doesn't exist | SC-9 |
| `test_needs_info_accepts_question_text` | T06: needs_info() stores optional question_text | Parameter doesn't exist (TypeError) | — |
| `test_needs_info_rejects_oversized_question_text` | T07: 64 KB cap on question_text | Parameter doesn't exist | — |

### Group B-1 — Route layer: `GET /dispatch/{story_id}/question` (T08–T11)

| Test | What It Verifies | RED/GREEN | SC |
|------|-----------------|-----------|-----|
| `test_get_question_returns_200_for_needs_info_story` | T08: Returns question metadata | RED — endpoint doesn't exist | SC-2 |
| `test_get_question_returns_404_for_non_needs_info` | T09: 404 when status ≠ needs_info | GREEN — route missing → default 404 | SC-8 |
| `test_get_question_returns_404_for_unknown_story` | T10: 404 for unknown story_id | GREEN — route missing → default 404 | SC-8 |
| `test_get_question_has_question_text_false_when_null` | T11: has_question_text=false when NULL | RED — endpoint doesn't exist | SC-7 |

### Group B-2 — Route layer: `POST /dispatch/{story_id}/answer` (T12–T18, T22)

| Test | What It Verifies | RED/GREEN | SC |
|------|-----------------|-----------|-----|
| `test_post_answer_returns_200_and_transitions` | T12: Returns 200 + AnswerResponse | RED — endpoint doesn't exist | SC-3, SC-5 |
| `test_post_answer_returns_409_for_non_needs_info` | T13: 409 when already answered | RED — endpoint doesn't exist | SC-9 |
| `test_post_answer_returns_404_for_unknown_story` | T14: 404 for unknown story_id | GREEN — route missing → default 404 | — |
| `test_post_answer_rejects_empty_answer` | T15: 422 for empty answer | RED — endpoint doesn't exist | — |
| `test_post_answer_rejects_oversized_answer` | T16: 422 for >64 KB answer | RED — endpoint doesn't exist | — |
| `test_post_answer_logs_audit_line` | T17: Audit log with operator identity | RED — endpoint doesn't exist | SC-10 |
| `test_post_answer_idempotent_second_submit_409` | T18: Second submit → 409 | RED — endpoint doesn't exist | SC-9 |
| `test_post_answer_rejects_unknown_fields` | T22: 422 for extra fields (Pydantic forbid) | RED — endpoint doesn't exist | — |

### Group C — Service layer: `cancel()` clears Q&A columns (T19)

| Test | What It Verifies | RED Reason | SC |
|------|-----------------|------------|-----|
| `test_cancel_clears_question_and_answer_text` | T19: UPDATE SQL includes question_text=NULL, answer_text=NULL | Columns not in current cancel() UPDATE | — |

### Group D — Output-variance tests: stub detection (T20–T21)

| Test | What It Verifies | RED Reason | SC |
|------|-----------------|------------|-----|
| `test_needs_info_stores_different_question_texts` | T20: Two different question_texts → different stored values | needs_info() has no question_text param | — |
| `test_answer_needs_info_stores_different_answers` | T21: Two different answers → different stored values | answer_needs_info() doesn't exist | — |

---

## Frontend E2E Tests — `dispatch-needs-info-answer.spec.ts`

All 8 tests are RED (NeedsInfoAnswerModal component, Answer button, and new API endpoints do not exist yet).

| Test | What It Verifies | SC |
|------|-----------------|-----|
| `E01: Answer button visible on needs_info rows` | Button appears in dispatch queue for needs_info items | SC-1 |
| `E02: Clicking Answer opens modal and shows question` | Modal fetches GET /question and displays text | SC-2 |
| `E03: Submit sends POST /answer and closes modal` | Submit → POST → modal closes | SC-3, SC-5 |
| `E04: Queue refreshes after submit` | dispatch-queue query refetched; row leaves needs_info | SC-6 |
| `E05: Degraded state shows fallback when question_text null` | Fallback panel with needs_info_path when no question text | SC-7 |
| `E06: Submit button disabled when answer empty` | Disabled when textarea empty; enabled when typed | — |
| `E07: Error banner on fetch failure with retry` | 500 → error banner + retry button | SC-7 |
| `E08: 409 response shows "already answered" message` | Second submit → "already answered" UX | SC-9 |

---

## SC Coverage Matrix

| SC | Description | Backend Test(s) | E2E Test(s) |
|----|-------------|----------------|-------------|
| SC-1 | Answer button on needs_info rows | — | E01 |
| SC-2 | Modal fetches and shows question | T08 | E02 |
| SC-3 | Submit POSTs answer | T12 | E03 |
| SC-4 | answer_text written to DB | T02 | — |
| SC-5 | needs_info → pending transition | T01, T03, T12 | E03 |
| SC-6 | Queue refreshes after submit | — | E04 |
| SC-7 | Degraded fallback on no question text | T11 | E05, E07 |
| SC-8 | 404 when not needs_info | T09, T10 | — |
| SC-9 | Idempotent — second submit 409 | T05, T13, T18 | E08 |
| SC-10 | Audit log with operator identity | T17 | — |

All 10 success criteria have test coverage.

---

## Defensive Gate Coverage

### Gate 1: Null/None Boundary
- T04: answer_needs_info with nonexistent story → NotFoundError
- T11: question_text is NULL → has_question_text=false
- T15: Empty answer string → 422

### Gate 4: Input Validation
- T15: Empty answer → 422
- T16: Oversized answer (>64 KB) → 422
- T07: Oversized question_text (>64 KB) → reject
- T22: Unknown fields in POST body → 422 (Pydantic extra="forbid")

### Gate 9: Failure Recovery (Stateful Operations)
- T18: Second POST /answer finds already-resumed story → 409 (not corrupt state)
- T05: answer_needs_info on wrong-state story → InvalidTransitionError

### Output-Variance (Stub Detection)
- T20: Two different question_texts → stored differently
- T21: Two different answers → stored differently

### LLM Error-Prone Areas
- Boundary conditions: T15 (empty), T16 (oversized), T07 (oversized)
- Edge cases: T11 (null question_text), T14 (unknown story)
- Output format: T08 (response schema with has_question_text, fetched_at)
- Security: T22 (extra field injection)

---

## API Mock Verification (Route Mock Gate)

| E2E Test | Mock Pattern | Matches Endpoint |
|----------|-------------|-----------------|
| E01–E08 | `**/api/dispatch/queue*` | `GET /api/dispatch/queue` (existing) |
| E02, E05, E07 | `**/api/dispatch/${STORY_ID}/question` | `GET /api/dispatch/{story_id}/question` (new) |
| E03, E04, E08 | `**/api/dispatch/${STORY_ID}/answer` | `POST /api/dispatch/{story_id}/answer` (new) |

All mock patterns trace to actual router registrations defined in the feature spec. No account-scoping is used in the dispatch endpoints (no JWT-based account_id), so no `accounts/*` segment is needed.

---

## RED State Summary

**Backend:** 22 collected, 19 FAIL, 3 GREEN (expected — route-missing 404s)
**E2E:** 8 tests, all RED (component and endpoints don't exist)

**RED reasons by category:**
- `AttributeError`: `answer_needs_info()` not on `DispatchDBService` (T01–T05, T21)
- `TypeError`: `needs_info()` doesn't accept `question_text` kwarg (T06–T07, T20)
- Route 404: `GET /question` and `POST /answer` endpoints not registered (T08, T11–T13, T15–T18, T22)
- SQL assertion: cancel() UPDATE doesn't include question_text/answer_text (T19)
- Component missing: NeedsInfoAnswerModal and Answer button don't exist (E01–E08)

All failures are for the right reasons — missing implementation, not broken test logic.

---

## Review Checklist

- [x] Every test name clearly states what it verifies
- [x] Arrange/Act/Assert structure is explicit
- [x] Junior-readable — no assumed context
- [x] Tests organized by layer (service → route → e2e)
- [x] No duplicate coverage
- [x] Happy paths covered (T01, T02, T08, T12, E02, E03)
- [x] Error cases covered (T04, T05, T09, T10, T13, T14, T15, T16, E07, E08)
- [x] Edge cases covered (T07, T11, T18, T19, E05, E06)
- [x] Output-variance tests for stub detection (T20, T21)
- [x] All 10 success criteria mapped to tests
- [x] Audit log test (T17 → SC-10)
- [x] Idempotency tests (T18, T05, E08 → SC-9)
- [x] Test files created as runnable code
- [x] `pytest --collect-only` discovers all 22 backend tests
- [x] `pytest` runs — 19 FAIL, 3 GREEN (RED state confirmed)
- [x] Playwright tests created in `e2e/` (8 tests)
- [x] Route mock patterns verified against actual endpoints
- [x] Cross-referenced with feature-spec.md and seed.md

Ready for Phase 8 (Implementation) — TDD workflow begins.
