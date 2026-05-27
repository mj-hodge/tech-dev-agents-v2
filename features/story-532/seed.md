# STORY-532: Dispatch `needs_info` state — human gate for ambiguous stories

**Status:** Phase 1 — Seed
**Scope:** Medium
**Owner:** Devon (dispatched 2026-04-22 by Mark)
**Next Phase:** 4 (Analysis) → 6 → 7 → 8

---

## Problem

When a dispatch prompt is ambiguous, phase agents are instructed (in
`sdlc_phase_runner._run_phase_sdk` prompt preamble) to write their
question to `features/<story>/QUESTION.md` and exit instead of guessing.

Today's failure path:

1. Agent writes `QUESTION.md` and exits.
2. `sdlc_phase_runner._check_for_questions` detects it, calls
   `_notify_teams(...)`, and returns `(False, None)`.
3. The surrounding dispatch poller treats that like a generic phase
   failure and re-queues the story with `[RETRY N/3]` prepended.
4. The next agent claims it, hits the same ambiguity, writes an
   identical `QUESTION.md`, and the cycle repeats.
5. After 3 retries the story is marked `failed` — but no human has
   actually seen the question in a way that produces an answer in the
   file. The agents have just burned tokens rewriting the same
   question.

**Concrete damage:** STORY-518, STORY-519, STORY-520 burned 9 retries
on 2026-04-22 producing identical QUESTION files that no agent could
progress past. That's ~9× the token spend for zero forward progress,
plus noise in the failure dashboard that hides real failures.

**Root cause:** There is no DB state that represents *"this story is
blocked on human input and MUST NOT be auto-reclaimed."* The retry
loop is indiscriminate — it treats "agent asked a question" identically
to "agent crashed."

## Target User

- **Primary:** the solo-dev engineering manager (Mark) running the
  autonomous dev agent fleet. Needs the queue to stop spending tokens
  on stories that are genuinely blocked on a human decision.
- **Secondary:** the agents themselves — the QUESTION.md escape hatch
  only works if asking a question actually pauses the story.
- **Tertiary:** the ops dashboard viewer, who needs a distinct visual
  signal for "blocked on you" vs. "paused (auto-resumable)" vs.
  "pending."

## Desired Behavior

1. **New terminal-until-resumed DB state `needs_info`** on
   `dispatch_items.status`, alongside the existing `pending | claimed |
   completed | cancelled | failed | paused`.
2. **Phase runner trigger:** when `_check_for_questions` detects a
   QUESTION.md, the runner POSTs `/api/dispatch/needs-info/{story_id}`
   with the question file path instead of returning `False` into the
   generic retry path.
3. **Queue invisibility:** `next_pending()` MUST NOT return
   `needs_info` rows. This is the key distinction from `paused`:
   paused stories auto-resume on the next poll; `needs_info` stories
   are invisible to agents until a human resumes them.
4. **Human resume path:** operator appends the answer directly to
   `QUESTION.md` in the story branch, then POSTs
   `/api/dispatch/resume/{story_id}` to transition `needs_info → pending`.
   The next poller call then claims it normally; the resumed agent
   reads the (now-answered) QUESTION.md and proceeds.
5. **Dashboard visibility:** DispatchQueue shows a separate
   `needs_info` bucket with a distinct (violet) badge so the operator
   can immediately see the "your turn" pile.

## Success Criteria

| # | Criterion | Verification |
|---|-----------|--------------|
| 1 | `status='needs_info'` is a valid value in the `dispatch_items` CHECK constraint | Migration applies cleanly; invalid values still rejected |
| 2 | `DispatchDbService.needs_info(story_id, question_file_path)` sets status and stores the file path | Unit test — row updated |
| 3 | `next_pending()` excludes `needs_info` rows even when they're the oldest | Unit test — enqueue, mark needs_info, poll returns None |
| 4 | `list_queue()` returns needs_info rows in a separate bucket | Unit test — bucket populated |
| 5 | `POST /api/dispatch/resume/{story_id}` transitions needs_info → pending | Route test — status changes, poller can now claim |
| 6 | Phase runner calls `/needs-info` (not `/fail` or implicit retry) when QUESTION.md is present | Test: stub runner, verify HTTP call + no retry scheduled |
| 7 | `/api/dispatch/next` returns 204 or a different story when the only candidate is `needs_info` | Route test |
| 8 | Dashboard renders `needs_info` rows with violet badge and separate group | Frontend component test |
| 9 | No regression to `paused` behavior (STORY-507) | Existing paused tests still green |

## Scope Classification: **Medium**

Rationale:

- Crosses components: DB migration, backend service, routes, response
  models, phase runner (deployment/), frontend types, frontend
  component.
- API surface change: two new POST endpoints, one response-schema
  addition.
- No architectural refactor; reuses the exact pattern already
  established by STORY-507 `paused`. Core logic is a mirror with one
  inversion (`next_pending()` excludes instead of includes).
- Scoped tests: two new test files (`test_needs_info_state.py`,
  `test_phase_runner_needs_info.py`).

Per CLAUDE.md scope table: Medium → `1 → 4 → 6 → 7 → 8`. The dispatch
prompt also calls out producing `analysis.md`, `feature-spec.md`, and
`test-design.md` — matches the Medium path.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected backend files | `tech_dev_agents/ops_console/services/dispatch_db_service.py`, `tech_dev_agents/ops_console/routes/dispatch.py`, `tech_dev_agents/ops_console/models/responses.py` |
| Affected deployment | `deployment/hermes/sdlc_phase_runner.py` (at the `_check_for_questions` handler ~line 1356) |
| Affected frontend | `frontend/src/types/api.ts`, `frontend/src/components/DispatchQueue.tsx` |
| New SQL | `scripts/migrations/007_needs_info_state.sql` (next number after `006_dispatch_priority.sql`) |
| New tests | `tests/ops_console/test_needs_info_state.py`, `tests/deployment/test_phase_runner_needs_info.py` |
| Prior art | STORY-507 introduced `paused` in `004_paused_status.sql` — same migration pattern, same service method shape, same route shape. The ONLY structural difference is that `next_pending()` must exclude `needs_info` (paused is included) |
| Current QUESTION.md handler | `sdlc_phase_runner.py:1354-1364` — today calls `_notify_teams` and returns `(False, None)` into the generic failure path |

## Key Decisions to Lock in Phase 4/6

1. **Schema column for the question file path.** Reuse an existing
   field (e.g., `last_error`) vs. add a new `needs_info_path TEXT`
   column. Recommendation: add dedicated column — it's cheap, it's
   self-documenting, and it mirrors STORY-507's `paused_at` /
   `current_phase` pattern.
2. **Active-index behavior.** The STORY-507 unique index covers
   `pending | claimed | paused`. Should `needs_info` join that set?
   Recommendation: **yes** — a story that's needs_info should NOT be
   re-enqueueable under the same story_id; the existing row is the
   source of truth and must be resumed, not duplicated.
3. **Resume endpoint semantics.** Should `/resume/{story_id}` require
   the operator to have written an answer into QUESTION.md (server
   enforces a file-contents check) or trust the caller? Recommendation:
   **trust the caller** — the server has no reliable way to detect
   "answer appended" across branches; gating belongs in the UI.
4. **Retry counter reset.** If a story went through [RETRY 2/3] before
   someone migrated it to needs_info, does resume reset the counter?
   Recommendation: **yes** — a resumed story is a fresh attempt with
   new information; don't punish it with carried-over retry debt.
5. **Phase runner failure on POST error.** If `/needs-info` POST fails
   (network, 500), what does the runner do? Recommendation: fall back
   to the existing `_notify_teams + return False` path so we don't
   regress — log loudly, surface in Teams, operator triages manually.

## Out of Scope (follow-ups)

- Automated detection of whether QUESTION.md has an answer appended
  (would need branch checkout server-side).
- Teams Adaptive Card "Answer this question" UI — nice-to-have, tracked
  separately; for now the operator edits QUESTION.md in the story
  branch directly.
- Migrating existing `[RETRY N/3]` stories that are already in
  `failed` state back into `needs_info` — one-time cleanup, not part of
  this story.
- Rate limiting / quota on resume (prevent thrashing if an operator
  keeps resuming a genuinely-broken story). Phase 10 (ops) concern.

## Risks

- **Silent drop.** If the phase runner's `/needs-info` POST fails AND
  the fallback notification path also fails, the story could end up in
  `claimed` state with a zombie agent. Mitigation: unit test the
  fallback; alert on claimed items older than N minutes (already
  covered by `recover_stale_claims`).
- **Frontend drift.** Adding `needs_info` to the status union without
  updating every switch statement could crash the dashboard on
  unexpected values. Mitigation: TypeScript exhaustive check on the
  status switch in DispatchQueue.tsx.
- **Migration ordering.** If migration 007 runs while a phase runner
  is mid-flight and tries to UPDATE status to `needs_info`, we could
  hit a CHECK constraint violation. Mitigation: migration is
  additive-only (adds a value to the CHECK set); no code path writes
  `needs_info` until the migration is deployed. Ordering is
  migrate-first, deploy-code-second — standard.

## Acceptance (from dispatch prompt)

Reproduced verbatim from Mark's dispatch so Phase 4/6/7/8 don't lose
it:

- `scripts/migrations/NNN_needs_info_state.sql` — add `needs_info` to `dispatch_items.status` CHECK
- `dispatch_db_service.py` — `needs_info(story_id, question_file_path)` + `resume_from_needs_info(story_id)` methods; `next_pending()` excludes needs_info; `list_queue()` includes needs_info bucket
- `dispatch.py` — POST `/api/dispatch/needs-info/{story_id}` + POST `/api/dispatch/resume/{story_id}`; `DispatchQueueResponse.needs_info` list
- `responses.py` — `DispatchItem.status` union includes `needs_info`
- `sdlc_phase_runner.py` — on QUESTION.md, POST `/needs-info` instead of the current `_notify_teams + return False` path (~line 1356)
- `frontend/src/types/api.ts` — status union + `DispatchQueueResponse.needs_info?`
- `frontend/src/components/DispatchQueue.tsx` — violet badge + needs_info group
- `tests/ops_console/test_needs_info_state.py` — new
- `tests/deployment/test_phase_runner_needs_info.py` — new

## Next Phase

→ **Phase 4 (Analysis)** — evaluate the five decisions above, confirm
   migration number, and lock the service-method signatures before
   Phase 6 design.
