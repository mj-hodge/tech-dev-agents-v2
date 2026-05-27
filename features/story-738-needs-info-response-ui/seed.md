# STORY-738: Needs Info Response UI — Operator Answer Modal

**Scope:** Medium
**Phase path:** 1 → 6 → 7 → 8 → Done
**Repo:** tech-dev-agents
**Branch:** story-738/needs-info-response-ui

## Problem Statement

When an autonomous agent encounters a question it cannot resolve, it writes a `QUESTION.md` file on its VM and transitions the dispatch item to `needs_info`. The dashboard surfaces this state with a pulsing violet badge, but there is no in-UI affordance to read the question or respond — the operator must SSH into the agent VM, locate `QUESTION.md`, draft an answer in Teams, and manually resume the work item. This high-friction loop is the dominant source of operator time-on-incident for blocked agents and frequently leaves agents idling for hours waiting on a human round-trip that should take a minute.

## User Story

As an operator, I want to read an agent's blocking question and submit an answer from the dashboard, so I don't have to SSH into the agent VM.

## Proposed Solution

### Backend (`apps/ops-console/routes/dispatch.py`)

1. **`GET /api/dispatch/{story_id}/question`**
   - Loads the `DispatchItem` and validates it is in `needs_info` state (404 otherwise).
   - Reads `needs_info_path` from the row, e.g. `/opt/agent/features/story-XXX-slug/QUESTION.md`.
   - Fetches the file contents from the agent VM via the existing SSH access path used for other ops-console operations.
   - Response shape: `{ "story_id", "agent", "phase", "needs_info_path", "question_text", "fetched_at" }`.
   - On SSH/read failure, returns the metadata + a `degraded: true` flag and an `error` field; the modal renders a fallback panel showing `story_id` and `needs_info_path` so the operator can read the file manually.

2. **`POST /api/dispatch/{story_id}/answer`**
   - Body: `{ "answer": "...", "operator": "mark" }`.
   - Validates dispatch item is in `needs_info`.
   - Writes `ANSWER.md` adjacent to `QUESTION.md` (same directory derived from `needs_info_path`).
   - Idempotent: re-writing `ANSWER.md` is allowed; the agent watches/reads the file when it wakes.
   - Calls the existing resume logic (transition `needs_info → claimed`).
   - Response: `{ "story_id", "status": "claimed", "answered_at" }`.

### Frontend (`apps/ops-console/web/src/components/DispatchQueue.tsx` + new modal)

- Add an "Answer" action button on each row rendered in the `needs_info` section of the dispatch table.
- Clicking the button opens a new modal component (e.g., `NeedsInfoAnswerModal.tsx`):
  - Header: agent name, story id, current phase ("Phase 7 — blocked").
  - Body: question text loaded via `GET /api/dispatch/{story_id}/question` (skeleton while loading).
  - If response is `degraded`, render a fallback that displays `needs_info_path` with a copy button and a hint to SSH manually.
  - Text area for the operator's answer + "Submit Answer" button (disabled while empty / submitting).
  - On submit: POST to `/api/dispatch/{story_id}/answer`, close modal, trigger queue refresh.

### Agent-side flow (already partially in place)

1. Agent writes `QUESTION.md` and calls `POST /api/dispatch/{story_id}/needs_info`.
2. Operator clicks "Answer" → modal renders question text.
3. Operator types answer → POST writes `ANSWER.md` and resumes the dispatch item.
4. Agent wakes (claimed), reads `ANSWER.md`, continues its phase.

## Success Criteria

| ID   | Criterion                                                                                                                            | Test type                  |
|------|--------------------------------------------------------------------------------------------------------------------------------------|----------------------------|
| SC-1 | Each `needs_info` row in the dispatch queue renders an "Answer" button.                                                              | Frontend unit + visual     |
| SC-2 | Clicking "Answer" opens a modal that fetches and displays the `QUESTION.md` content via `GET /api/dispatch/{story_id}/question`.    | Frontend integration       |
| SC-3 | Submitting an answer POSTs `{answer, operator}` to `/api/dispatch/{story_id}/answer`.                                                | Frontend integration       |
| SC-4 | Backend writes `ANSWER.md` to the agent VM, in the same directory as `needs_info_path`.                                              | Backend integration (SSH mock) |
| SC-5 | After successful submit, the dispatch item transitions from `needs_info` → `claimed` (resumed).                                      | Backend unit               |
| SC-6 | The dispatch queue refreshes automatically after submit so the operator sees the row leave the `needs_info` section.                 | Frontend integration       |
| SC-7 | If `QUESTION.md` cannot be read (SSH failure), the modal renders a degraded panel showing `story_id`, `needs_info_path`, and an error message. | Frontend + backend unit |
| SC-8 | `GET /api/dispatch/{story_id}/question` returns 404 when the dispatch item is not in `needs_info` state.                            | Backend unit               |
| SC-9 | `POST /api/dispatch/{story_id}/answer` is idempotent — submitting twice does not corrupt state or fail loudly on the second call.    | Backend unit               |
| SC-10| Answer submission is recorded with the `operator` identity (audit-friendly log line).                                                | Backend unit               |

## Out of Scope

- Push notification to the operator (Teams) when `needs_info` fires — separate story.
- Rich formatting / markdown preview in the answer text area — plain text v1.
- Multi-turn Q&A loop within a single `needs_info` cycle — current model is one question, one answer.
- File attachments in the answer (images, logs) — separate story if needed.
- Mobile / responsive polish of the modal — desktop-first; future story.

## Key Files

### Backend (read first)
- `apps/ops-console/routes/dispatch.py` — existing `/needs_info`, `/resume`, `/cancel` endpoints; pattern for adding the new `/question` and `/answer` routes.
- `apps/ops-console/services/dispatch_service.py` (or equivalent) — service layer + transition logic for `needs_info → claimed`.
- `apps/ops-console/models/dispatch.py` — `DispatchItem` model with `needs_info_path`, `current_phase`, status enum.
- `apps/ops-console/services/agent_ssh.py` (or wherever ops-console talks to agent VMs) — existing SSH client pattern.
- `tests/ops_console/test_dispatch_*.py` — existing dispatch route tests; mirror their structure for new routes.

### Frontend (read first)
- `apps/ops-console/web/src/components/DispatchQueue.tsx` — needs_info section rendering and violet badge.
- `apps/ops-console/web/src/components/Modal.tsx` (or equivalent shared modal) — pattern to reuse.
- `apps/ops-console/web/src/api/dispatch.ts` — API client; add `getQuestion()` and `submitAnswer()`.
- `apps/ops-console/web/src/hooks/useDispatchQueue.ts` (if exists) — refresh trigger after answer submit.

## Open Questions

1. **SSH transport reuse:** Does the ops-console already have a generic "read file from agent VM" helper, or is this the first place we need it? If first, the helper itself becomes a small refactor target (and a test seam).
2. **Atomic write semantics for ANSWER.md:** Write-then-rename, or direct overwrite? Atomic is safer if the agent polls the file, but adds complexity. Decide in Phase 6.
3. **Wake mechanism after `ANSWER.md` write:** Does the agent poll for `ANSWER.md`, or does the existing `/resume` call signal it directly? Confirm in Phase 6 — affects whether write order matters (write file before or after status flip).
4. **Operator identity source:** Should `operator` be inferred server-side from the SSO session, or accepted from the client body? Server-side is more trustworthy; client-side is simpler. Recommend server-side derivation in Phase 6.
5. **Question text size limits:** Should we cap `QUESTION.md` size on read (e.g., 64 KB) to avoid pathological payloads? Likely yes — confirm in Phase 6.
6. **Concurrent answers:** If two operators open the modal simultaneously and both submit, second submit should either be a no-op (idempotent) or surface a "already answered, agent resumed" message. Decide in Phase 6.

**Frontend:** true

## Test Criteria
- Validate story behavior with focused unit/integration tests for touched components.
- Verify no regressions in existing framework/contract checks.

## Validation
- [ ] Run required test suite(s) for this story scope.
- [ ] Confirm CI gates pass before merge.
